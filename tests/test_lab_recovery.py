"""Crash boundaries for the recovery policy, without importing the web app."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1] / 'labdiscoveryengine/recovery'
spec = importlib.util.spec_from_file_location('recovery_under_test', PACKAGE / '__init__.py',
                                             submodule_search_locations=[str(PACKAGE)])
pkg = importlib.util.module_from_spec(spec); sys.modules[spec.name] = pkg; spec.loader.exec_module(pkg)
from recovery_under_test.coordinator import Coordinator, GenerationChanged
from recovery_under_test.state import read, save, exclusive


class PowerLoss(BaseException):
    pass


class Adapter:
    resources = ['one', 'two']
    def __init__(self):
        self.gen = {'scheduler': 'redis-a', 'backend': 'redis-b', 'boot': 'boot-a'}
        self.running = set(self.resources)
        self.held = set()
        self.identities = {r: 1 for r in self.resources}
        self.trace = []; self.fail = None; self.crash = None
        self.scheduler_running = True
        self.students = ['interrupted-student']
    def event(self, event, name=None):
        self.trace.append((event, name))
        if self.crash == (event, name):
            self.crash = None
            raise PowerLoss()
        if self.fail == (event, name): raise OSError('dependency unavailable')
    def generation(self): return self.gen.copy()
    def fence_scheduler(self, epoch): self.event('fence_scheduler'); self.scheduler_running = False
    def fence_central(self, name, epoch):
        return self.fence(name, epoch)

    def fence(self, name, epoch): self.event('fence', name); self.running.discard(name)
    def interrupt_sessions(self, epoch):
        assert not self.running and not self.scheduler_running
        self.event('interrupt'); self.students = []
    def interrupt_resource_sessions(self, name, epoch): self.event('interrupt_resource', name)
    def hold(self, name, epoch): self.event('hold', name); self.held.add(name)
    def retire(self, name, epoch):
        assert name not in self.running and name in self.held
        self.event('retire', name)
    def restore(self, name, epoch):
        assert name not in self.running and name in self.held
        self.event('restore', name); self.running.add(name); self.identities[name] += 1
    def verify(self, name): self.event('verify', name); return self.identities[name]
    def release(self, name, epoch): self.event('release', name); self.held.remove(name)
    def ready_identity(self, name): self.event('identity', name); return self.identities[name]
    def scheduler_identity(self): return 'scheduler-one'
    def start_scheduler(self, epoch):
        self.event('start_scheduler'); self.scheduler_running = True; return self.scheduler_identity()


class Recovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.adapter = Adapter(); self.now = 100
        self.coordinator = Coordinator(self.adapter, self.tmp.name, lambda: self.now)
    def test_global_boundary_precedes_any_retirement(self):
        state = self.coordinator.step()
        self.assertEqual(self.adapter.students, [])
        self.assertTrue(all(r['phase'] == 'ready' for r in state['resources'].values()))
        self.assertEqual(self.adapter.trace[:4], [('fence_scheduler', None), ('fence', 'one'),
                                                 ('fence', 'two'), ('interrupt', None)])
    def test_storage_validation_failure_revokes_previously_ready_receipt(self):
        self.coordinator.step()
        self.adapter.generation = lambda: (_ for _ in ()).throw(RuntimeError('persistence failed'))
        with self.assertRaises(RuntimeError):self.coordinator.step()
        state = read(self.coordinator.path)
        self.assertEqual(state['phase'], 'fencing')
        self.assertEqual(state['resources'], {})

    def test_validation_failure_mid_recovery_closes_healthy_peers(self):
        state = self.coordinator.step()
        self.adapter.generation = lambda: (_ for _ in ()).throw(RuntimeError('persistence failed'))
        with self.assertRaises(GenerationChanged):self.coordinator._same_generation(state)
        self.assertEqual(read(self.coordinator.path)['resources'], {})

    def test_ready_resources_are_not_reset_by_periodic_checks(self):
        self.coordinator.step(); self.adapter.trace = []; self.coordinator.step()
        self.assertFalse(any(event in ('restore', 'retire', 'interrupt') for event, _ in self.adapter.trace))
    def test_failed_resource_does_not_reset_or_block_healthy_peer(self):
        self.adapter.fail = ('restore', 'one'); state = self.coordinator.step()
        self.assertEqual(state['resources']['one']['phase'], 'recovering')
        self.assertEqual(state['resources']['two']['phase'], 'ready')
        self.assertNotIn('one', self.adapter.running)
        self.adapter.trace = []; self.coordinator.step()
        self.assertNotIn(('restore', 'one'), self.adapter.trace)
        self.adapter.fail = None; self.now += 301; self.coordinator.step()
        self.assertNotIn(('restore', 'two'), self.adapter.trace)
        self.assertIn(('restore', 'one'), self.adapter.trace)
    def test_unreachable_controller_keeps_only_its_resource_closed(self):
        def central(name, epoch):
            self.adapter.event('central_fence',name);self.adapter.running.discard(name)
        self.adapter.fence_central=central
        self.adapter.fail=('fence','one')
        state=self.coordinator.step()
        self.assertTrue(state['resources']['one']['fence_unconfirmed'])
        self.assertEqual(state['resources']['two']['phase'],'ready')
        self.assertTrue(self.adapter.scheduler_running)

    def test_independent_maintenance_does_not_trigger_physical_fence(self):
        self.coordinator.step();self.adapter.trace=[]
        self.adapter.identities['one']+=1;self.adapter.fail=('hold','one')
        state=self.coordinator.step()
        self.assertEqual(state['resources']['one']['phase'],'recovering')
        self.assertNotIn(('fence','one'),self.adapter.trace)
        self.assertEqual(state['resources']['two']['phase'],'ready')

    def test_new_executor_invalidates_only_its_resource(self):
        self.coordinator.step(); self.adapter.trace = []; self.adapter.identities['one'] += 1
        self.coordinator.step()
        self.assertIn(('restore', 'one'), self.adapter.trace)
        self.assertNotIn(('restore', 'two'), self.adapter.trace)
    def test_new_store_epoch_interrupts_all_old_sessions(self):
        first = self.coordinator.step(); self.adapter.students = ['new-student']
        self.adapter.gen['backend'] = 'redis-c'
        second = self.coordinator.step()
        self.assertNotEqual(first['epoch'], second['epoch']); self.assertEqual(self.adapter.students, [])
    def test_failure_to_fence_prevents_any_session_retirement(self):
        self.adapter.fail = ('fence', 'two')
        with self.assertRaises(OSError): self.coordinator.step()
        self.assertEqual(self.adapter.students, ['interrupted-student'])
        self.assertFalse(self.adapter.scheduler_running)
    def test_crash_after_every_mutating_boundary_repeats_fence_before_restore(self):
        for event in [('fence_scheduler', None), ('fence', 'one'), ('interrupt', None),
                      ('hold', 'one'), ('retire', 'one'), ('restore', 'one'),
                      ('verify', 'one'), ('release', 'one'), ('start_scheduler', None)]:
            with self.subTest(event=event), tempfile.TemporaryDirectory() as folder:
                adapter = Adapter(); adapter.crash = event
                coordinator = Coordinator(adapter, folder)
                with self.assertRaises(PowerLoss): coordinator.step()
                state = coordinator.step()
                self.assertTrue(all(r['phase'] == 'ready' for r in state['resources'].values()))
                self.assertEqual(adapter.students, [])
    def test_generation_change_during_restore_never_publishes_ready(self):
        original = self.adapter.restore
        def restore(name, epoch):
            original(name, epoch); self.adapter.gen['scheduler'] = 'replacement'
        self.adapter.restore = restore
        with self.assertRaises(GenerationChanged): self.coordinator.step()
        state = read(Path(self.tmp.name) / 'state.json')
        self.assertEqual(state['phase'], 'fencing')
        self.assertEqual(state['resources'], {})
        self.assertNotIn(('release', 'one'), self.adapter.trace)
    def test_corrupt_journal_does_not_initialize_or_touch_services(self):
        (Path(self.tmp.name) / 'state.json').write_text('broken')
        with self.assertRaises(ValueError): self.coordinator.step()
        self.assertEqual(self.adapter.trace, [])
    def test_parallel_coordinator_is_rejected(self):
        with exclusive(Path(self.tmp.name) / 'coordinator.lock'):
            with self.assertRaises(BlockingIOError): self.coordinator.step()
        self.assertEqual(self.adapter.trace, [])


if __name__ == '__main__': unittest.main()
