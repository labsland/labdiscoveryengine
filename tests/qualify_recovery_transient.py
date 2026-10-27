"""Root-only transient/orphan qualification in a disposable Linux VM."""
import hashlib,json,os,signal,subprocess,sys,tempfile,time
from pathlib import Path
from labsland_recovery import agent

assert os.environ.get('LAB_RECOVERY_DISPOSABLE_VM')=='yes' and os.geteuid()==0
version=1 if Path('/sys/fs/cgroup/freezer/cgroup.procs').exists() else 2
root='/sys/fs/cgroup/freezer/labsland-recovery/' if version==1 else '/sys/fs/cgroup/labsland-recovery/'
name='ll-transient-qualification:restore'
config=Path('/etc/supervisor/conf.d/ll-transient-qualification.conf');assert not config.exists()
tmp=Path(tempfile.mkdtemp(prefix='ll-transient-',dir='/var/tmp'));tmp.chmod(0o755)
state=tmp/'state';state.mkdir(mode=0o755)
child=tmp/'restore.py';child.write_text('''import os,time,json
if os.fork()==0:
 os.setsid()
 dev=os.open('/dev/null',os.O_RDWR)
 for fd in (0,1,2):os.dup2(dev,fd)
 while True:time.sleep(.1)
if os.environ.get('QUALIFY_DELAY')=='1':time.sleep(120)
print('LAB_RECOVERY_RESULT='+json.dumps({'profile':'test-only'}),flush=True)
''');child.chmod(0o644)
planfile=tmp/'plan.json'
config.write_text('''[program:restore]
command=/opt/ll-recovery-test/bin/python -I -m labsland_recovery.agent --plan %s --program %s enter
directory=/
user=root
autostart=false
autorestart=false
stopasgroup=true
killasgroup=true
[group:ll-transient-qualification]
programs=restore
'''%(planfile,name))
plan=dict(version=1,id='transient-qualification',programs=[name],start_programs=[],stores=[],
 state_directory=str(state),shared_runtime='/var/tmp/ll-recovery-shared',supervisor_socket='/var/run/supervisor.sock',
 cgroup_version=version,protected_files={str(config):hashlib.sha256(config.read_bytes()).hexdigest()},
 launch={name:dict(supervisor_config=str(config),cgroup=root+'transient-qualification',directory='/',
    user='nobody',store_indices=[],transient=True,command=['/usr/bin/python3',str(child)])})
planfile.write_text(json.dumps(plan));planfile.chmod(0o644)
subprocess.run(['supervisorctl','reread'],check=True,stdout=subprocess.DEVNULL)
subprocess.run(['supervisorctl','update','ll-transient-qualification'],check=True,stdout=subprocess.DEVNULL)
results={}
try:
 plan=agent.load(planfile);e1='d'*32;e2='e'*32
 agent.fence(plan,e1);agent.retire(plan,e1)
 assert agent.run_transient(plan,name,e1)==dict(profile='test-only')
 assert not agent.boundary(plan,name).pids();results['successful_operation_leaves_no_descendants']=True
 assert (state/'restore-transient-qualification.log').stat().st_mode & 0o777 == 0o600
 results['component_diagnostic_is_private']=True
 agent.fence(plan,e2);agent.retire(plan,e2)
 process=subprocess.Popen([sys.executable,'-I','-m','labsland_recovery.agent','--plan',str(planfile),
    '--program',name,'--epoch',e2,'run'],env=dict(os.environ,QUALIFY_DELAY='1'),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 try:
  deadline=time.monotonic()+10
  while len(agent.boundary(plan,name).pids())<2:
   assert time.monotonic()<deadline,'Transient did not start';time.sleep(.05)
  process.kill();process.wait(timeout=5)
  assert agent.boundary(plan,name).pids();results['agent_crash_leaves_trackable_kernel_membership']=True
  agent.fence(plan,'f'*32)
  assert not agent.boundary(plan,name).pids();results['next_boundary_removes_crashed_agent_operation']=True
 finally:
  if process.poll() is None:process.kill();process.wait(timeout=5)
 print(json.dumps(dict(cgroup_version=version,results=results)))
finally:
 try:agent.boundary(plan,name).terminate()
 except Exception:pass
 config.unlink();subprocess.run(['supervisorctl','reread'],stdout=subprocess.DEVNULL)
 subprocess.run(['supervisorctl','update','ll-transient-qualification'],stdout=subprocess.DEVNULL)
 try:Path(root+'transient-qualification').rmdir()
 except OSError:pass
