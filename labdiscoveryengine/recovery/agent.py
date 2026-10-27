"""Qualified Linux/Supervisor cold boundary, usable locally or over SSH.

No host names, board models, service names or Redis namespaces are inferred.
A root-owned manifest inventories every producer/executor of one resource.
The caller closes upstream admission before invoking fence/retire/start.
"""
import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import redis
import http.client
import socket
import xmlrpc.client
from .state import read, save, exclusive, validate_trusted_path, trusted_read
from .process_boundary import ProcessBoundary

IDENTIFIER = re.compile(r'^[A-Za-z0-9_-]+:[A-Za-z0-9_-]+$')


def load(path):
    validate_trusted_path(path)
    info = Path(path).lstat()
    if info.st_uid != 0 or info.st_mode & 0o022 or Path(path).is_symlink():
        raise ValueError('Recovery manifests must be root-owned and non-writable by peers')
    plan = json.loads(Path(path).read_text())
    if plan.get('version') != 1 or not plan.get('programs'):
        raise ValueError('Explicit cold-boundary manifest required')
    if any(not IDENTIFIER.fullmatch(name) for name in plan['programs']):
        raise ValueError('Exact qualified Supervisor program names required')
    if not set(plan.get('start_programs', [])) <= set(plan['programs']):
        raise ValueError('Start programs must be inside the fenced boundary')
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', plan.get('id', '')):
        raise ValueError('Unique boundary identifier required')
    validate_trusted_path(plan['state_directory'], directory=True)
    validate_trusted_path(plan['shared_runtime'], directory=True)
    for filename, digest in plan['protected_files'].items():
        if hashlib.sha256(Path(filename).read_bytes()).hexdigest() != digest:
            raise ValueError('Cold-boundary configuration drift: ' + filename)
    for name in plan['programs']:
        config = plan['launch'][name]
        filename = config['supervisor_config']
        if filename not in plan['protected_files']:
            raise ValueError('Supervisor configuration must be protected')
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(filename)
        section = 'program:' + name.split(':')[1]
        for flag, expected in [('autostart', False), ('autorestart', False),
                               ('stopasgroup', True), ('killasgroup', True)]:
            if parser.getboolean(section, flag) != expected:
                raise ValueError('Unsafe Supervisor boundary configuration')
        if parser.get(section, 'user') != 'root' or config['user'] == 'root':
            raise ValueError('Root enrollment must drop to an application user')
        ProcessBoundary(config['cgroup'], plan['cgroup_version'])
        if 'store_indices' not in config or any(type(i) is not int or i < 0 or i >= len(plan.get('stores',[])) for i in config['store_indices']):
            raise ValueError('Every program requires explicit store bindings (or an explicit empty list)')
    for store in plan.get('stores', []):
        for prefix in store.get('prefixes', []):
            if not prefix or not prefix.endswith(':') or any(c in prefix for c in '*?[]'):
                raise ValueError('Only explicit delimited resource prefixes may be retired')
        for key in store.get('keys', []):
            if not key or any(c in key for c in '*?[]'):
                raise ValueError('Exact keys required')
    plan['_path'] = str(path)
    return plan


def command(args):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=90)
    return result.returncode, result.stdout


def process_table(root=Path('/proc')):
    result = {}
    for path in root.glob('[0-9]*/stat'):
        try:
            value = path.read_text().rsplit(')', 1)[1].split()
            # Values start at field3 (state); ppid4, pgrp5, starttime22.
            result[int(path.parent.name)] = dict(state=value[0], ppid=int(value[1]),
                                                group=int(value[2]), start=value[19])
        except (OSError, ValueError, IndexError):
            continue
    return result


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=90)
        self.path = path
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class UnixTransport(xmlrpc.client.Transport):
    def __init__(self, path):
        super().__init__(); self.path = path
    def make_connection(self, host): return UnixConnection(self.path)


def supervisor(plan):
    return xmlrpc.client.ServerProxy('http://localhost',
        transport=UnixTransport(plan['supervisor_socket'])).supervisor


def statuses(plan):
    rpc = supervisor(plan)
    result = {}
    for name in plan['programs']:
        row = rpc.getProcessInfo(name)
        result[name] = dict(state=row['statename'], pid=row['pid'])
    return result


def boundary(plan, name):
    return ProcessBoundary(plan['launch'][name]['cgroup'], plan['cgroup_version'])


def journal_path(plan):
    return Path(plan['state_directory']) / ('fence-' + plan['id'] + '.json')


def authorization_path(plan, name):
    return Path(plan['state_directory']) / (plan['id'] + '-' + name.replace(':','_') + '.start.json')


def local_generation(plan):
    stores = {}
    for name, store in enumerate(plan.get('stores', [])):
        connection = client(store)
        try: stores[str(name)] = connection.info('server')['run_id']
        finally: connection.close()
    return dict(boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(), stores=stores)


def require_epoch(plan, epoch):
    if not isinstance(epoch,str) or not re.fullmatch(r'[0-9a-f]{32}',epoch):
        raise ValueError('Exact recovery epoch required')
    state = trusted_read(journal_path(plan))
    if state.get('epoch') != epoch or state.get('generation') != local_generation(plan):
        raise RuntimeError('Delayed command belongs to an obsolete boundary')
    return state


def assert_stopped(plan):
    rows = statuses(plan)
    if any(row['state'] not in ('STOPPED', 'EXITED', 'FATAL') for row in rows.values()):
        raise RuntimeError('A writer or executor is still active')


def fence(plan, epoch):
    if not isinstance(epoch,str) or not re.fullmatch(r'[0-9a-f]{32}',epoch):
        raise ValueError('Exact recovery epoch required')
    for name in plan['programs']:
        try: authorization_path(plan, name).unlink()
        except FileNotFoundError: pass
    save(journal_path(plan), dict(epoch=epoch, phase='fencing', generation=None))
    rows = statuses(plan)
    for name,row in rows.items():
        group = boundary(plan, name)
        if row['pid'] and row['pid'] not in group.pids():
            raise RuntimeError('Existing process was not enrolled; explicit migration required')
    # cgroup membership survives setsid/double-fork and agent/coordinator death.
    # The kernel, not a racy /proc descendant snapshot, proves the boundary.
    for name in plan['programs']:
        boundary(plan, name).terminate()
    rpc = supervisor(plan)
    for name,row in statuses(plan).items():
        if row['state'] not in ('STOPPED','EXITED','FATAL'):
            try: rpc.stopProcess(name, True)
            except xmlrpc.client.Fault as error:
                if error.faultCode != 70: raise  # Supervisor NOT_RUNNING
    assert_stopped(plan)
    if any(boundary(plan,name).pids() for name in plan['programs']):
        raise RuntimeError('Kernel boundary is not empty')
    try: generation = local_generation(plan)
    except (redis.ConnectionError, redis.TimeoutError, redis.BusyLoadingError): generation = None
    save(journal_path(plan),dict(epoch=epoch,phase='stopped',generation=generation))
    return {'stopped': True}


def client(store):
    return redis.Redis.from_url(store['url'], decode_responses=True,
                                socket_connect_timeout=3, socket_timeout=3)


def identity(plan):
    rows = statuses(plan)
    table = process_table()
    generation = local_generation(plan)
    return dict(generation, programs={name: dict(row, start=table.get(row['pid'], {}).get('start'))
                                     for name,row in rows.items()})


RETIRE = """
local run = string.match(redis.call('INFO','server'), 'run_id:([^\\r\\n]+)')
if run ~= ARGV[1] then return redis.error_reply('generation changed') end
if redis.call('EXISTS',KEYS[1]) == 0 then return 0 end
if redis.call('EXISTS',KEYS[2]) == 1 then return redis.error_reply('archive collision') end
local ttl = redis.call('TTL',KEYS[1])
if ttl < 0 then ttl = tonumber(ARGV[2]) else ttl = math.min(ttl,tonumber(ARGV[2])) end
redis.call('RENAME',KEYS[1],KEYS[2]); redis.call('EXPIRE',KEYS[2],ttl); return 1
"""


def retire(plan, epoch):
    if not re.fullmatch(r'[0-9a-f]{32}', epoch):
        raise ValueError('Exact recovery epoch required')
    state = require_epoch(plan, epoch)
    if state.get('phase') != 'stopped':
        raise RuntimeError('Retirement requires a fresh cold boundary')
    assert_stopped(plan)
    if any(boundary(plan,name).pids() for name in plan['programs']):
        raise RuntimeError('Executor still present')
    total = 0
    for store in plan.get('stores', []):
        connection = client(store)
        run_id = connection.info('server')['run_id']
        keys = set(store.get('keys', []))
        for prefix in store.get('prefixes', []):
            keys.update(connection.scan_iter(match=prefix + '*', count=100))
        for key in sorted(keys):
            archived = 'lab-recovery-archive:' + epoch + ':' + key
            total += connection.eval(RETIRE, 2, key, archived, run_id, 604800)
        if connection.info('server')['run_id'] != run_id:
            raise RuntimeError('Store restarted during retirement')
    require_epoch(plan, epoch)
    save(journal_path(plan), dict(state, phase='retired'))
    return {'retired_keys': total}


def start(plan, epoch):
    state = require_epoch(plan, epoch)
    expected_phase = 'stopped' if plan.get('role') == 'scheduler' else 'retired'
    if state['phase'] != expected_phase:
        raise RuntimeError('A new start requires a qualified retired boundary')
    rpc = supervisor(plan)
    for name in plan['programs']:
        config = plan['launch'][name]
        if name in plan.get('start_programs', []) or config.get('on_demand'):
            save(authorization_path(plan,name), dict(epoch=epoch, generation=state['generation']))
    for name in plan.get('start_programs', []):
        rpc.startProcess(name, True)
    rows = statuses(plan)
    if any(rows[name]['state'] != 'RUNNING' for name in plan.get('start_programs', [])):
        raise RuntimeError('Execution boundary did not start')
    return identity(plan)


def enter(plan, name):
    import pwd
    if os.geteuid() != 0 or name not in plan['programs']:
        raise RuntimeError('Root-managed program enrollment required')
    config = plan['launch'][name]
    authorization = trusted_read(authorization_path(plan,name))
    state = require_epoch(plan, authorization['epoch'])
    if authorization['generation'] != state['generation']:
        raise RuntimeError('Stale process startup authorization')
    if not config.get('on_demand'):
        authorization_path(plan,name).unlink()  # one launch per cold boundary
    boundary(plan,name).enter()
    user = pwd.getpwnam(config['user'])
    if user.pw_uid == 0: raise RuntimeError('Application must not run as root')
    os.initgroups(user.pw_name,user.pw_gid)
    os.setgid(user.pw_gid); os.setuid(user.pw_uid)
    from .redis_connection import store_identity
    os.environ.update(config.get('environment', {}))
    for variable in ['LAB_RECOVERY_EXPECTED_REDIS_RUN_ID','LAB_RECOVERY_EXPECTED_REDIS_STORES']:
        os.environ.pop(variable,None)
    bindings = []
    for index in config['store_indices']:
        pool = redis.ConnectionPool.from_url(plan['stores'][index]['url'])
        bindings.append(dict(identity=store_identity(pool),run_id=state['generation']['stores'][str(index)]))
    os.environ['LAB_RECOVERY_EXPECTED_REDIS_STORES'] = json.dumps(bindings)
    os.environ['LAB_RECOVERY_MANAGED'] = '1'
    os.environ['LAB_RECOVERY_EPOCH'] = authorization['epoch']
    shared = plan['shared_runtime']
    os.environ['PYTHONPATH'] = shared + (os.pathsep + os.environ['PYTHONPATH'] if os.environ.get('PYTHONPATH') else '')
    os.environ['HOME'] = user.pw_dir
    os.environ['USER'] = os.environ['LOGNAME'] = user.pw_name
    os.chdir(config['directory'])
    os.execvpe(config['command'][0],config['command'],os.environ)


def run_transient(plan, name, epoch):
    """Run a bounded component operation inside the same recoverable boundary.

    A coordinator/SSH death cannot orphan the restorer outside the next fence.
    Only the manifest's fixed executable vector is accepted, never caller code.
    """
    config = plan['launch'][name]
    if not config.get('transient'):
        raise ValueError('Program is not a qualified transient operation')
    state = require_epoch(plan,epoch)
    if state['phase'] != 'retired':
        raise RuntimeError('Transient restore requires retired old work')
    save(authorization_path(plan,name),dict(epoch=epoch,generation=state['generation']))
    import sys
    process = subprocess.Popen([sys.executable,'-I','-m',__package__+'.agent','--plan',plan['_path'],
                                '--program',name,'enter'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True)
    try:
        output,_ = process.communicate(timeout=300)
        if process.returncode:
            raise RuntimeError('Qualified transient restore failed')
        records = [json.loads(line.split('=',1)[1]) for line in output.splitlines()
                   if line.startswith('LAB_RECOVERY_RESULT=')]
        if len(records) != 1: raise RuntimeError('Missing transient restore receipt')
        require_epoch(plan,epoch)
        return records[0]
    finally:
        # Also applies on an exception/timeout. A hard agent crash leaves the
        # persistent cgroup for the next fence rather than losing its identity.
        boundary(plan,name).terminate()
        process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True)
    parser.add_argument('--epoch')
    parser.add_argument('--program')
    parser.add_argument('action', choices=['inspect', 'fence', 'retire', 'start', 'enter', 'verify-gpio', 'run'])
    args = parser.parse_args()
    plan = load(args.plan)
    if args.action == 'enter':
        return enter(plan,args.program)  # startProcess waits; cannot hold agent lock
    with exclusive(Path(plan['state_directory']) / (plan['id'] + '.lock')):
        if args.action == 'inspect': result = identity(plan)
        elif args.action == 'fence': result = fence(plan,args.epoch)
        elif args.action == 'retire': result = retire(plan,args.epoch)
        elif args.action == 'run': result = run_transient(plan,args.program,args.epoch)
        elif args.action == 'verify-gpio':
            from .gpio_readback import verify
            connection = client(plan['stores'][plan['gpio_readback']['store_index']])
            try:
                result = verify(plan,args.epoch,lambda:identity(plan),connection,lambda epoch:require_epoch(plan,epoch))
            finally: connection.close()
        else: result = start(plan,args.epoch)
    print(json.dumps(result))


if __name__ == '__main__': main()
