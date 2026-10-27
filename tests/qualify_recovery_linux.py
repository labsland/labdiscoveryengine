"""Isolated root-only Linux qualification; never run on a laboratory host.

Requires a disposable VM with Supervisor/Redis installed. Generates only a
qualification Supervisor group and removes it on exit. No hardware access.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from labsland_recovery import agent
from labsland_recovery.state import save

assert os.environ.get('LAB_RECOVERY_DISPOSABLE_VM') == 'yes'
assert os.geteuid() == 0
version = 1 if Path('/sys/fs/cgroup/freezer/cgroup.procs').exists() else 2
root = '/sys/fs/cgroup/freezer/labsland-recovery/' if version == 1 else '/sys/fs/cgroup/labsland-recovery/'
name = 'll-recovery-qualification:executor'
config = Path('/etc/supervisor/conf.d/ll-recovery-qualification.conf')
assert not config.exists()
tmp = Path(tempfile.mkdtemp(prefix='ll-recovery-qualification-',dir='/var/tmp'))
tmp.chmod(0o755)
state_dir = tmp/'state';state_dir.mkdir(mode=0o755)
plan_file = tmp/'plan.json'; child = tmp/'child.py'
child.write_text('''import os,time
if os.fork() == 0:
 os.setsid()
 if os.fork() == 0:
  while True: time.sleep(.1)
 os._exit(0)
while True: time.sleep(.1)
''')
child.chmod(0o644)
config.write_text('''[program:executor]
command=/opt/ll-recovery-test/bin/python -I -m labsland_recovery.agent --plan %s --program %s enter
directory=/
user=root
autostart=false
autorestart=false
stopasgroup=true
killasgroup=true
startsecs=1
stopwaitsecs=2
stdout_logfile=%s
stderr_logfile=%s
[group:ll-recovery-qualification]
programs=executor
''' % (plan_file,name,tmp/'out.log',tmp/'err.log'))
config.chmod(0o644)
plan=dict(version=1,id='qualification',programs=[name],start_programs=[name],
          state_directory=str(state_dir),shared_runtime='/var/tmp/ll-recovery-shared',supervisor_socket='/var/run/supervisor.sock',
          cgroup_version=version,stores=[],
          protected_files={str(config):hashlib.sha256(config.read_bytes()).hexdigest()},
          launch={name:dict(supervisor_config=str(config),cgroup=root+'qualification-executor',
                           directory='/',user='nobody',store_indices=[],command=['/usr/bin/python3',str(child)])})
plan_file.write_text(json.dumps(plan));plan_file.chmod(0o644)
subprocess.run(['supervisorctl','reread'],check=True,stdout=subprocess.DEVNULL)
subprocess.run(['supervisorctl','update'],check=True,stdout=subprocess.DEVNULL)
results={}
try:
 plan=agent.load(plan_file)
 e1='a'*32;e2='b'*32
 agent.fence(plan,e1);agent.retire(plan,e1);agent.start(plan,e1)
 time.sleep(.3)
 group=agent.boundary(plan,name)
 assert len(group.pids()) >= 2, group.pids()
 results['detached_child_enrolled']=True
 parent=agent.statuses(plan)[name]['pid'];os.kill(parent,signal.SIGKILL)
 time.sleep(.3)
 assert group.pids(), 'Detached child should survive direct parent death'
 agent.fence(plan,e2)
 assert group.pids()==[]
 results['orphan_removed_after_parent_crash']=True
 try:agent.start(plan,e1)
 except RuntimeError:results['delayed_start_rejected']=True
 else:raise AssertionError('Old start accepted')
 agent.retire(plan,e2);agent.start(plan,e2)
 agent.fence(plan,'c'*32)
 assert group.pids()==[]
 results['repeated_boundary_clean']=True
 # Supervisor cannot silently restart an old generation after the boundary.
 try:agent.supervisor(plan).startProcess(name,True)
 except Exception:pass
 time.sleep(.2)
 assert group.pids()==[]
 results['manual_unapproved_start_closed']=True
 print(json.dumps(dict(cgroup_version=version,results=results),sort_keys=True))
except BaseException:
 print((tmp/'err.log').read_text()[-4000:] if (tmp/'err.log').exists() else 'No error log',file=sys.stderr)
 raise
finally:
 try:agent.boundary(plan,name).terminate()
 except Exception:pass
 subprocess.run(['supervisorctl','stop',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 config.unlink()
 subprocess.run(['supervisorctl','reread'],stdout=subprocess.DEVNULL)
 subprocess.run(['supervisorctl','update'],stdout=subprocess.DEVNULL)
 try:Path(root+'qualification-executor').rmdir()
 except OSError:pass
