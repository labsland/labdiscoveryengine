"""Recover physical resources through a qualified cold-boundary adapter.

Policy: sessions interrupted by a storage/execution generation change end in
error. Nothing in this coordinator resumes them or treats empty Redis as idle.
The adapter must prove that all previous writers/executors are stopped before
retiring their scoped work and restoring the physical baseline.
"""
import time
import uuid
from pathlib import Path
from .state import read, save, exclusive


class GenerationChanged(RuntimeError):
    pass


class DependenciesUnavailable(RuntimeError):
    pass


class Coordinator:
    def __init__(self, adapter, directory, clock=time.time):
        self.adapter = adapter
        self.directory = Path(directory)
        self.clock = clock
        if not self.directory.is_dir() or self.directory.is_symlink():
            raise ValueError('Provision a persistent recovery directory')
        self.path = self.directory / 'state.json'

    def _save(self, state):
        state['updated_at'] = self.clock()
        save(self.path, state)

    def _close(self, state):
        state.update(version=1, epoch=uuid.uuid4().hex, phase='fencing',
                     generation=None, resources={}, scheduler_identity=None)
        self._save(state)

    def _same_generation(self, state):
        try:
            if self.adapter.generation() != state['generation']:
                raise GenerationChanged('Storage or host generation changed during recovery')
        except Exception as error:
            # A persistence/validation failure can happen without a new Redis
            # run ID. Revoke the receipt immediately, including healthy peers.
            self._close(state)
            raise GenerationChanged('Storage generation is no longer qualified') from error

    def step(self):
        with exclusive(self.directory / 'coordinator.lock'):
            return self._step()

    def _step(self):
        try:
            state = read(self.path)
        except FileNotFoundError:
            state = None
        # Corrupt records are not silently treated as a first installation.
        try:
            generation = self.adapter.generation()
        except DependenciesUnavailable:
            state = dict(version=1, epoch=uuid.uuid4().hex, generation=None,
                         phase='fencing', resources={})
            self._save(state)
            self.adapter.ensure_dependencies(state['epoch'])
            return state
        except Exception:
            self._close(state if state is not None else {})
            raise
        try:
            scheduler_changed = (state is not None and state.get('scheduler_identity') is not None
                                 and self.adapter.scheduler_identity() != state['scheduler_identity'])
        except Exception:
            self._close(state)
            raise
        if state is None or state['generation'] != generation or scheduler_changed:
            state = dict(version=1, epoch=uuid.uuid4().hex, generation=generation,
                         phase='fencing', resources={})
            self._save(state)  # close durable admission before touching services
        if state['phase'] == 'fencing':
            self.adapter.fence_scheduler(state['epoch'])
            # A failed stop prevents ALL admission on this store. Each call must
            # re-prove quiescence; a previous successful call is not durable proof.
            for resource in self.adapter.resources:
                self.adapter.fence_central(resource, state['epoch'])
            self._same_generation(state)
            self.adapter.interrupt_sessions(state['epoch'])
            state['resources'] = {name: dict(phase='recovering', attempts=0, retry_at=0)
                                  for name in self.adapter.resources}
            state['phase'] = 'managed'
            self._save(state)
        for name in self.adapter.resources:
            item = state['resources'][name]
            if item['phase'] == 'ready':
                # An execution restart invalidates this resource, not its peers.
                try:
                    still_ready = self.adapter.ready_identity(name) == item['identity']
                except Exception:
                    still_ready = False
                if still_ready:
                    continue
                item.update(phase='recovering', retry_at=0)
                self._save(state)
            if self.clock() < item.get('retry_at', 0):
                continue
            held = False
            try:
                self._same_generation(state)
                item['epoch'] = uuid.uuid4().hex
                self._save(state)  # invalidate delayed backend start/cleanup requests
                item['operation'] = 'hold'
                self.adapter.hold(name, item['epoch'])
                held = True
                item['operation'] = 'fence'
                self.adapter.fence(name, item['epoch'])
                self._same_generation(state)
                # Repeating this phase after a coordinator crash starts with a
                # fresh cold boundary. Never replay against a running executor.
                item['operation'] = 'interrupt_resource_sessions'
                self.adapter.interrupt_resource_sessions(name, item['epoch'])
                item['operation'] = 'retire'
                self.adapter.retire(name, item['epoch'])
                item['operation'] = 'restore'
                item['baseline'] = self.adapter.restore(name, item['epoch'])
                item['operation'] = 'verify'
                identity = self.adapter.verify(name)
                self._same_generation(state)
                item['operation'] = 'release'
                self.adapter.release(name, item['epoch'])
                item.update(phase='ready', identity=identity, attempts=0, error=None, operation=None, fence_unconfirmed=False)
                self._save(state)
            except GenerationChanged:
                # The current epoch cannot reopen ANY resource. The next step
                # starts a new global fence; HTTP/Lua gates reject old epochs now.
                raise
            except Exception as error:
                item['attempts'] = item.get('attempts', 0) + 1
                item.update(phase='recovering', error=type(error).__name__,
                            retry_at=self.clock() + min(300, 5 * 2 ** min(item['attempts'], 6)))
                self._save(state)
                # Preserve exclusion even if the failure happened after dispatch.
                # An unreachable board stays excluded, but cannot prevent peers
                # from recovering. Its uncertainty is durable and never a release.
                try:
                    if held:
                        self.adapter.fence(name, item['epoch'])
                    item['fence_unconfirmed'] = False
                except Exception:
                    item['fence_unconfirmed'] = True
                self._save(state)
        self._same_generation(state)
        # Starting the scheduler does not authorize held resources. Assignment
        # and backend admission also enforce their own current-epoch receipts.
        state['scheduler_identity'] = self.adapter.start_scheduler(state['epoch'])
        self._save(state)
        return state
