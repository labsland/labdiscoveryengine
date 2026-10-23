"""Opt-in central coordinator for a worker-serialized CDC reconnect.

Run from the managed installer timer with explicit target configuration.
There is no SSH, reset, programming, reservation deletion or retry of a command.
An interrupted journal is never automatically replayed: inspect/reconcile it.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import time
import urllib.request
import uuid

import redis
from .maintenance import acquire, owned, release_verified, _validate


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def save(path, value):
    tmp = path.with_suffix('.tmp')
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
    os.replace(str(tmp), str(path))
    fd = os.open(str(path.parent), os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise OSError('Controller redirects are not allowed')


def http(url, data=None):
    body = canonical(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.build_opener(NoRedirect).open(req, timeout=3) as response:
        raw = response.read(65537)
        if len(raw) > 65536: raise ValueError('Controller response too large')
        result = json.loads(raw)
        if not isinstance(result, dict): raise ValueError('Controller response is not an object')
        return result


def backend_idle(store, target):
    resource = target['resource']
    prefixes = target['backend_prefixes']
    if not isinstance(prefixes, list) or not prefixes:
        raise ValueError('Every configured programmer ownership prefix is required')
    for base in prefixes:
        if store.get(base + 'active-user') not in (None, '', b'', '0', b'0') or store.get(base + 'cleanup-state'):
            return False
    for kind in ('active', 'sessions'):
        if next(store.scan_iter(resource + ':weblab:' + kind + ':*'), None) is not None:
            return False
    for kind in ('active_sessions', 'pending_tasks'):
        if store.smembers(resource + ':weblab:index:v1:' + kind) - {'__weblablib_index_v1__', b'__weblablib_index_v1__'}:
            return False
    return True


class Coordinator:
    def __init__(self, scheduler, backend, folder, request=http, sleep=time.sleep):
        self.scheduler, self.backend = scheduler, backend
        self.folder, self.request, self.sleep = Path(folder), request, sleep
        if not self.folder.is_dir() or self.folder.is_symlink():
            raise ValueError('Managed persistent directory required')

    def run(self, target):
        resource = target['resource']
        owner = uuid.uuid4().hex
        _validate(resource, owner)
        run_id = self.scheduler.info('server')['run_id']
        capability = 'cdc-v1:' + run_id
        if self.scheduler.get('lde:serial-recovery-protocol') not in (capability, capability.encode()):
            raise RuntimeError('Scheduler maintenance protocol is not activated')
        resources = self.scheduler.get('lde:serial-recovery-resources') or b''
        if isinstance(resources, bytes): resources = resources.decode('ascii')
        if resource not in resources.split(','):
            raise RuntimeError('Resource is not activated for serial maintenance')
        path = self.folder / (resource + '.json')
        if path.is_symlink(): raise ValueError('Journal must not be a symlink')
        previous = json.loads(path.read_text()) if path.exists() else None
        if previous and previous['phase'] not in ('closed', 'skipped'):
            return 'reconciliation-required'
        endpoint = target['controller_url'].rstrip('/') + '/serial-recovery'
        payload = self.request(endpoint)
        status = payload.get('status', {})
        if not (status.get('resource') == resource and status.get('deleted_cdc') is True and
                status.get('identity_present') is True and status.get('busy') is False and
                status.get('by_id') == target['by_id'] and
                0 <= time.time() - status.get('at', 0) < 3):
            return 'not-eligible'
        if not backend_idle(self.backend, target): return 'busy'
        generation = status['generation']
        identity = dict(owner=owner, generation=generation, resource=resource)
        state = dict(identity, phase='preparing', at=time.time(), target=target,
                     redis_run_id=run_id)
        save(path, state)
        def record(phase, **extra):
            state.update(phase=phase, at=time.time(), **extra); save(path, state)
        if not acquire(self.scheduler, resource, owner):
            record('skipped'); return 'busy'
        record('acquired')
        def guard():
            if (self.scheduler.info('server')['run_id'] != state['redis_run_id'] or
                    self.scheduler.get('lde:running') not in ('true', b'true') or
                    not owned(self.scheduler, resource, owner) or not backend_idle(self.backend, target)):
                raise RuntimeError('Ownership/backend state changed; retain maintenance')
        mirror = 'serial-recovery::' + resource + '::owner'
        try:
            guard()
            if not self.backend.set(mirror, canonical(identity), nx=True):
                raise RuntimeError('Existing controller ownership must be reconciled')
            record('dispatching')
            command = dict(owner=owner, generation=generation, action='reconnect')
            guard(); self.request(endpoint, command)  # Exactly once, even if response is lost.
            verified = self._wait_receipt(endpoint, identity, 'verified', guard)
            record('verified', receipt=verified)
            guard(); record('releasing-worker')
            self.request(endpoint, dict(command, action='release'))
            released = self._wait_receipt(endpoint, identity, 'released', guard)
            record('worker-released', receipt=released)
            self.verify_idle(target, generation, guard)
            guard(); record('ready-to-release')
            expected = canonical(identity)
            if self.backend.eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) end return 0",1,mirror,expected) != 1:
                raise RuntimeError('Controller ownership changed during release')
            if not release_verified(self.scheduler, resource, owner):
                raise RuntimeError('Scheduler ownership changed during release')
            record('closed')
            return 'recovered'
        except Exception as error:
            # Preserve the last dispatch phase; never turn uncertainty into retry.
            record(state['phase'], error=type(error).__name__, reconciliation_required=True)
            raise

    def reconcile(self, target):
        """Explicit completion from a terminal worker receipt; never reopen CDC."""
        path = self.folder / (target['resource'] + '.json')
        state = json.loads(path.read_text())
        if state['target'] != target:
            raise ValueError('Target configuration changed; manual review required')
        if state['phase'] in ('closed', 'skipped'): return 'already-closed'
        owner, generation, resource = state['owner'], state['generation'], state['resource']
        identity = dict(owner=owner, generation=generation, resource=resource)
        endpoint = target['controller_url'].rstrip('/') + '/serial-recovery'
        run_id = self.scheduler.info('server')['run_id']
        capability = 'cdc-v1:' + run_id
        if self.scheduler.get('lde:serial-recovery-protocol') not in (capability, capability.encode()):
            raise RuntimeError('Scheduler must restore pending maintenance before reconciliation')
        def guard():
            if (self.scheduler.info('server')['run_id'] != run_id or
                    self.scheduler.get('lde:running') not in ('true', b'true') or
                    not owned(self.scheduler, resource, owner) or not backend_idle(self.backend, target)):
                raise RuntimeError('Reconciliation requires exact idle ownership')
        guard()
        payload = self.request(endpoint)
        receipt = payload.get('receipt', {})
        if not all(receipt.get(k) == v for k, v in identity.items()):
            raise RuntimeError('Worker receipt identity is unknown')
        if receipt.get('phase') not in ('verified', 'released'):
            raise RuntimeError('No successful terminal worker proof; retain hold')
        if payload.get('status', {}).get('generation') != generation:
            raise RuntimeError('Worker restarted; physical reconciliation required')
        samples = receipt.get('samples', [])
        if len(samples) != 3 or len(set(samples)) != 3:
            raise RuntimeError('Incomplete worker proof')
        if receipt['phase'] == 'verified':
            # Release is idempotently queued by owner/action; no transport replay.
            self.request(endpoint, dict(owner=owner, generation=generation, action='release'))
            self._wait_receipt(endpoint, identity, 'released', guard)
        self.verify_idle(target, generation, guard)
        guard()
        mirror = 'serial-recovery::' + resource + '::owner'
        current = self.backend.get(mirror)
        expected = canonical(identity)
        if current is not None and current not in (expected, expected.encode()):
            raise RuntimeError('Foreign mirror owner; retain hold')
        state.update(phase='ready-to-release', at=time.time()); save(path, state)
        if current is not None:
            if self.backend.eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) end return 0",1,mirror,expected) != 1:
                raise RuntimeError('Mirror owner changed')
        if not release_verified(self.scheduler, resource, owner):
            raise RuntimeError('Scheduler owner changed')
        state.update(phase='closed', at=time.time(), reconciled=True); save(path, state)
        return 'reconciled'

    def _wait_receipt(self, endpoint, identity, phase, guard):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            guard()
            payload = self.request(endpoint)
            receipt = payload.get('receipt', {})
            if all(receipt.get(k) == v for k, v in identity.items()):
                if receipt.get('phase') == 'failed': raise RuntimeError('Worker failed; retain hold')
                if receipt.get('phase') == phase:
                    samples = receipt.get('samples', [])
                    if len(samples) != 3 or len(set(samples)) != 3:
                        raise RuntimeError('Incomplete worker protocol verification')
                    return receipt
            self.sleep(.2)
        raise TimeoutError('Worker receipt unknown; retain maintenance')

    def verify_idle(self, target, generation, guard):
        samples = set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and len(samples) < 3:
            guard()
            payload = self.request(target['controller_url'])
            protocol = payload.get('pico_protocol', {})
            status = self.request(target['controller_url'].rstrip('/') + '/serial-recovery').get('status', {})
            if (payload.get('success') is not True or payload.get('pending_control_tasks') != 0 or
                    protocol.get('success') is not True or not 0 <= time.time() - protocol.get('checked_at', 0) < 3 or
                    status.get('generation') != generation or status.get('busy') is not False or status.get('deleted_cdc') is not False or
                    status.get('identity_present') is not True or status.get('by_id') != target['by_id'] or
                    status.get('resource') != target['resource'] or not 0 <= time.time() - status.get('at', 0) < 3):
                raise RuntimeError('Post-recovery readiness failed')
            samples.add(protocol['checked_at']); self.sleep(.2)
        if len(samples) != 3: raise RuntimeError('Three fresh polls required')
        if self.request(target['readiness_url']).get('success') is not True:
            raise RuntimeError('Backend readiness failed')


def read_pending_journals(folder):
    directory = Path(folder)
    if not directory.is_absolute() or not directory.is_dir() or directory.is_symlink():
        raise ValueError('Invalid recovery journal directory')
    pending = []
    for path in directory.glob('*.json'):
        if path.is_symlink(): raise ValueError('Recovery journal symlink refused')
        state = json.loads(path.read_text())
        _validate(state['resource'], state['owner'])
        if path.name != state['resource'] + '.json': raise ValueError('Recovery journal identity mismatch')
        if state['phase'] not in ('closed', 'skipped'):
            pending.append(state)
    return pending


def read_probe_journals(folder):
    """A completed client request may outlive its scheduler process/Redis."""
    directory = Path(folder)
    if not directory.is_absolute() or not directory.is_dir() or directory.is_symlink():
        raise ValueError('Invalid probe journal directory')
    records = []
    for path in directory.glob('*.probe'):
        if path.is_symlink(): raise ValueError('Probe journal symlink refused')
        state = json.loads(path.read_text())
        _validate(state['resource'], state['owner'])
        if path.name != state['resource'] + '.probe' or state['phase'] not in ('running', 'closed'):
            raise ValueError('Invalid probe journal')
        records.append(state)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--inspect', action='store_true')
    mode.add_argument('--reconcile', metavar='EXACT_RESOURCE')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    with open(Path(config['state_dir']) / 'coordinator.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.inspect:
            print(canonical([dict(resource=s['resource'],owner=s['owner'],phase=s['phase']) for s in read_pending_journals(config['state_dir'])])); return
        scheduler = redis.Redis.from_url(config['scheduler_redis_url'], socket_timeout=1, socket_connect_timeout=1)
        backend = redis.Redis.from_url(config['backend_redis_url'], socket_timeout=1, socket_connect_timeout=1)
        coordinator = Coordinator(scheduler, backend, config['state_dir'])
        targets = config['targets']
        if args.reconcile:
            targets = [t for t in targets if t['resource'] == args.reconcile]
            if len(targets) != 1: raise ValueError('One exact configured resource required')
        for target in targets:
            result = coordinator.reconcile(target) if args.reconcile else coordinator.run(target)
            print(canonical(dict(resource=target['resource'], result=result)))


if __name__ == '__main__': main()
