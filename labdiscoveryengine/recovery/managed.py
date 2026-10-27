"""Declarative supervised-host adapter for the common recovery coordinator.

All command vectors are root-owned deployment configuration, never HTTP input.
Boundary agents inventory precise services/namespaces locally or over SSH.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid
import redis
from .coordinator import Coordinator, DependenciesUnavailable
from .state import read, save, exclusive, validate_trusted_path


INTERRUPT = """
local run = string.match(redis.call('INFO','server'), 'run_id:([^\\r\\n]+)')
if run ~= ARGV[1] then return redis.error_reply('generation changed') end
local status = redis.call('HGET',KEYS[1],'status')
if status and status ~= 'finished' and status ~= 'unavailable' then
 redis.call('HSET',KEYS[1],'status','broken','recovery_interrupted',ARGV[2],
            'message','Laboratory restarted; please reserve again')
 redis.call('PUBLISH',KEYS[1] .. ':channel','broken')
end
return 1
"""
RELEASE = """
local run = string.match(redis.call('INFO','server'), 'run_id:([^\\r\\n]+)')
if run ~= ARGV[1] then return redis.error_reply('generation changed') end
if redis.call('GET',KEYS[1] .. ':recovery-hold') ~= ARGV[2] then return 0 end
if redis.call('EXISTS',KEYS[1] .. ':maintenance') == 1 or
 redis.call('EXISTS',KEYS[1] .. ':health-probe') == 1 then return 0 end
if redis.call('EXISTS',KEYS[1] .. ':assigned') == 1 then return 0 end
redis.call('SET',KEYS[1] .. ':recovery-epoch',ARGV[2])
redis.call('DEL',KEYS[1] .. ':recovery-hold')
redis.call('PUBLISH',KEYS[1] .. ':channel','laboratory-recovered')
return 1
"""


def execute(args, timeout=120):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        # Commands may emit credentials/configuration: never forward raw output.
        raise RuntimeError('Managed recovery command failed (exit %d)' % result.returncode)
    return result.stdout


HOLD = """
local run = string.match(redis.call('INFO','server'), 'run_id:([^\\r\\n]+)')
if run ~= ARGV[1] then return redis.error_reply('generation changed') end
if redis.call('EXISTS',KEYS[1] .. ':maintenance',KEYS[1] .. ':health-probe') > 0 then return 0 end
redis.call('SET',KEYS[1] .. ':recovery-hold',ARGV[2])
return 1
"""

INTERRUPT_RESOURCE = """
local run = string.match(redis.call('INFO','server'), 'run_id:([^\\r\\n]+)')
if run ~= ARGV[1] then return redis.error_reply('generation changed') end
if redis.call('GET',KEYS[1] .. ':recovery-hold') ~= ARGV[2] then return 0 end
local owner = redis.call('GET',KEYS[1] .. ':assigned')
local ids = {}
if owner then ids[owner] = true end
for _,priority in ipairs(redis.call('ZRANGE',KEYS[1] .. ':queues:priorities',0,-1)) do
 local queue = KEYS[1] .. ':queues:' .. priority
 for _,id in ipairs(redis.call('LRANGE',queue,0,-1)) do ids[id] = true end
 redis.call('DEL',queue)
end
redis.call('DEL',KEYS[1] .. ':queues:priorities')
for id,_ in pairs(ids) do
 local key = 'lde:reservations:' .. id
 if redis.call('EXISTS',key) == 1 then
  redis.call('HSET',key,'status','broken','recovery_interrupted',ARGV[2],
             'message','Laboratory restarted; please reserve again')
  redis.call('PUBLISH',key .. ':channel','broken')
 end
end
redis.call('DEL',KEYS[1] .. ':assigned')
redis.call('PUBLISH',KEYS[1] .. ':channel','interrupted')
return 1
"""


class ManagedAdapter:
    def __init__(self, plan):
        self.plan = plan
        self.resources = sorted(plan['resources'])
        self.scheduler = redis.Redis.from_url(plan['stores']['scheduler'], decode_responses=True,
                                              socket_timeout=3, socket_connect_timeout=3)
        self.run_id = None

    def boundary(self, config, action, epoch=None):
        args = list(config['command'])
        if epoch is not None:
            args += ['--epoch', epoch]
        args += [action]
        return json.loads(execute(args, timeout=330 if action == 'run' else 120))

    def generation(self):
        stores = {}
        for name, url in self.plan['stores'].items():
            connection = redis.Redis.from_url(url, socket_timeout=3, socket_connect_timeout=3,
                                               decode_responses=True)
            try:
                stores[name] = connection.info('server')['run_id']
                if connection.info('memory').get('maxmemory_policy') != 'noeviction':
                    raise RuntimeError('Managed execution stores must not evict ownership or archived state')
            except (redis.ConnectionError, redis.TimeoutError, redis.BusyLoadingError) as error:
                raise DependenciesUnavailable() from error
            finally:
                connection.close()
        execute(self.plan['storage_validate_command'])
        self.store_run_ids = stores
        self.run_id = stores['scheduler']
        return dict(boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(), stores=stores,
                    plan_sha256=hashlib.sha256(json.dumps(self.plan, sort_keys=True).encode()).hexdigest())

    def ensure_dependencies(self, epoch):
        # This runs behind a durable closed receipt, including on first boot.
        # Never restart an active but unhealthy store or erase its data.
        self.fence_scheduler(epoch)
        for name in self.resources:
            self.fence_central(name, epoch)
        for unit in self.plan['dependency_units']:
            if not re.fullmatch(r'[a-zA-Z0-9@_.-]+\.service', unit):
                raise ValueError('Exact dependency service required')
            state = execute(['systemctl','show',unit,'--property=ActiveState','--value']).strip()
            if state in ('inactive','failed'):
                execute(['systemctl','start',unit])

    def fence_scheduler(self, epoch):
        self.boundary(self.plan['scheduler_boundary'], 'fence', epoch)

    def fence_central(self, name, epoch):
        # Only writers to the changed central store belong in the global fence.
        # Remote queues are retired behind their own per-resource boundary.
        for boundary in self.plan['resources'][name]['boundaries']:
            if boundary['host'] == 'local':
                self.boundary(boundary, 'fence', epoch)

    def fence(self, name, epoch):
        for boundary in self.plan['resources'][name]['boundaries']:
            self.boundary(boundary, 'fence', epoch)

    def interrupt_sessions(self, epoch):
        # Only a dedicated ownership store whose writers are stopped is accepted.
        for name in self.resources:
            self.scheduler.set('lde:resources:' + name + ':recovery-hold', epoch)
        for key in self.scheduler.scan_iter('lde:reservations:*', count=100):
            if self.scheduler.type(key) == 'hash':
                self.scheduler.eval(INTERRUPT, 1, key, self.run_id, epoch)

    def hold(self, name, epoch):
        if self.scheduler.eval(HOLD,1,'lde:resources:' + name,self.run_id,epoch) != 1:
            raise RuntimeError('Independent maintenance/probe requires reconciliation')

    def interrupt_resource_sessions(self, name, epoch):
        if self.scheduler.eval(INTERRUPT_RESOURCE, 1, 'lde:resources:' + name, self.run_id, epoch) != 1:
            raise RuntimeError('Recovery no longer owns interruption')

    def retire(self, name, epoch):
        for boundary in self.plan['resources'][name]['boundaries']:
            self.boundary(boundary, 'retire', epoch)

    def restore(self, name, epoch):
        resource = self.plan['resources'][name]
        for boundary in resource['execution_boundaries']:
            self.boundary(boundary, 'start', epoch)
        # This is a component-provided, qualified family adapter, not a generic
        # healthy HTTP endpoint or a shell fragment derived from numbering.
        receipt = self.boundary(resource['restore_boundary'], 'run', epoch)
        if receipt.get('profile') != resource['profile']:
            raise RuntimeError('Unexpected restore profile')
        if receipt.get('default_sha256') != resource['default_sha256']:
            raise RuntimeError('Restored artifact differs from qualified baseline')
        gpio = self.boundary(resource['controller_boundary'], 'verify-gpio', epoch)
        if gpio.get('profile') != 'standard-sysfs-gpio-v1':
            raise RuntimeError('Physical controller baseline not verified')
        self.boundary(resource['backend_boundary'], 'start', epoch)
        return dict(programming=receipt, gpio=gpio)

    def ready_identity(self, name):
        resource = self.plan['resources'][name]
        owner = self.scheduler.get('lde:resources:' + name + ':assigned')
        if owner and self.scheduler.hget('lde:reservations:' + owner, 'reconciliation_required'):
            raise RuntimeError('Session requires a new laboratory boundary')
        identities = [self.boundary(boundary, 'inspect') for boundary in resource['boundaries']]
        for identity, boundary in zip(identities, resource['boundaries']):
            for program in boundary['required_running']:
                if identity['programs'][program]['state'] != 'RUNNING':
                    raise RuntimeError('Execution process is not running')
            # Virtual peripherals are intentionally started/stopped by an active
            # session. Only long-lived executors define the resource generation.
            identity['programs'] = {name: identity['programs'][name]
                                    for name in boundary['required_running']}
        return identities

    def verify(self, name):
        resource = self.plan['resources'][name]
        identity = self.ready_identity(name)
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            try:
                request = urllib.request.Request(resource['health_url'], headers={'User-Agent': 'LabsLand-Recovery/1'})
                with urllib.request.urlopen(request, timeout=10) as response:
                    health = json.load(response)
                if health.get('success') is True:
                    # Backend initialization can queue its own reset. Recheck
                    # the controller after that initialization has completed.
                    epoch = self.scheduler.get('lde:resources:' + name + ':recovery-hold')
                    self.boundary(resource['controller_boundary'], 'verify-gpio', epoch)
                    if self.ready_identity(name) != identity:
                        raise RuntimeError('Executor changed during baseline verification')
                    return identity
            except (OSError, ValueError):
                pass
            time.sleep(2)
        raise RuntimeError('Native laboratory health did not recover')

    def release(self, name, epoch):
        if self.scheduler.eval(RELEASE, 1, 'lde:resources:' + name, self.run_id, epoch) != 1:
            raise RuntimeError('Recovery no longer owns release')

    def scheduler_identity(self):
        return self.boundary(self.plan['scheduler_boundary'], 'inspect')

    def start_scheduler(self, epoch):
        # Existing storage validation remains authoritative for AOF/service/data
        # health. Approval now follows a proven cold boundary and scoped holds.
        identity = self.scheduler_identity()
        if all(row['state'] == 'RUNNING' for row in identity['programs'].values()):
            return identity
        # A previous attempt can have started the web process but failed to
        # start its worker. Re-establish an empty scheduler boundary before
        # approval, rather than waiting forever on assert-stopped.
        self.fence_scheduler(epoch)
        execute(self.plan['storage_approve_command'] + ['--reason',
            'Managed cold-boundary laboratory recovery; interrupted sessions terminated; epoch ' + epoch,
            'approve'])
        return self.boundary(self.plan['scheduler_boundary'], 'start', epoch)


def load_plan(path):
    path = Path(path)
    validate_trusted_path(path)
    info = path.lstat()
    if path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Root-owned recovery plan required')
    plan = json.loads(path.read_text())
    if plan.get('version') != 1 or plan.get('policy') != 'terminate-sessions':
        raise ValueError('Unsupported recovery policy')
    if not 1 <= plan.get('poll_interval', 15) <= 60:
        raise ValueError('Bounded recovery observation interval required')
    validate_trusted_path(plan['state_directory'], directory=True)
    import yaml
    for filename in plan['drift_files']:
        if yaml.safe_load(Path(filename).read_text()).get('status') != 'clean':
            raise ValueError('Unreconciled deployment drift')
    for filename, digest in plan['protected_files'].items():
        if hashlib.sha256(Path(filename).read_bytes()).hexdigest() != digest:
            raise ValueError('Recovery deployment drift: ' + filename)
    return plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True)
    parser.add_argument('--state-directory', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    while True:
        try:
            try:
                plan = load_plan(args.plan)
                if plan['state_directory'] != args.state_directory:
                    raise ValueError('State directory differs from the installed service contract')
            except Exception:
                # A corrupt or drifted plan is not permission to keep serving a
                # previously ready receipt. Do not execute its hardware commands.
                close_admission(args.state_directory)
                raise
            adapter = ManagedAdapter(plan)
            state = Coordinator(adapter, plan['state_directory']).step()
            print(json.dumps(dict(event='recovery-observation',
                  resources={k: v['phase'] for k, v in state['resources'].items()})), flush=True)
        except Exception as error:
            print(json.dumps(dict(event='recovery-waiting', error=type(error).__name__)), flush=True)
            if args.once:
                raise
        if args.once:
            return
        time.sleep(plan.get('poll_interval', 15) if 'plan' in locals() else 15)


def close_admission(directory):
    validate_trusted_path(directory, directory=True)
    with exclusive(Path(directory) / 'coordinator.lock'):
        save(Path(directory) / 'state.json', dict(version=1, phase='fencing',
             epoch=uuid.uuid4().hex, generation=None, resources={},
             error='configuration-unavailable', updated_at=time.time()))


if __name__ == '__main__': main()
