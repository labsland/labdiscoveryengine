"""Portable protocol test; filesystem trust is qualified separately on Linux."""
import argparse,base64,datetime,importlib.util,json,os,shutil,socket,subprocess,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
parser=argparse.ArgumentParser();parser.add_argument('--weblab-source',type=Path,required=True);args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(args.weblab_source))
pkg=importlib.util.spec_from_file_location('labsland_recovery',root/'labdiscoveryengine/recovery/__init__.py',submodule_search_locations=[str(root/'labdiscoveryengine/recovery')]);module=importlib.util.module_from_spec(pkg);sys.modules[pkg.name]=module;pkg.loader.exec_module(module)
import redis
from flask import Flask,url_for
from weblablib import WebLab,requires_active
from labsland_recovery import admission
from labsland_recovery.agent import RETIRE
from labsland_recovery.redis_connection import fenced_redis
with tempfile.TemporaryDirectory() as directory:
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 process=subprocess.Popen([shutil.which('redis-server'),'--bind','127.0.0.1','--port',str(port),'--save','','--appendonly','no','--dir',directory],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 url='redis://127.0.0.1:%s/0'%port;connection=redis.Redis.from_url(url,decode_responses=True)
 try:
  for _ in range(100):
   try:connection.ping();break
   except redis.ConnectionError:time.sleep(.05)
  runid=connection.info('server')['run_id']
  # Match the actual serialized store identity rather than guessing its shape.
  from labsland_recovery.redis_connection import store_identity
  os.environ['LAB_RECOVERY_EXPECTED_REDIS_STORES']=json.dumps([dict(identity=store_identity(redis.ConnectionPool.from_url(url)),run_id=runid)])
  app=Flask(__name__);app.config.update(SECRET_KEY='fixture',WEBLAB_USERNAME='fixture',WEBLAB_PASSWORD='fixture',WEBLAB_REDIS_URL=url,WEBLAB_REDIS_BASE='fixture',WEBLAB_REDIS_CLIENT_FACTORY=fenced_redis,WEBLAB_AUTOCLEAN_THREAD=False,WEBLAB_TASK_THREADS_PROCESS=0)
  state=dict(version=1,phase='managed',scheduler_identity={'fixture':True},generation=dict(boot='fixture',stores={'backend':runid}),resources={'board':dict(phase='ready',epoch='a'*32)})
  boot=Path(directory)/'boot';boot.write_text('fixture')
  original=admission.snapshot
  with patch.object(admission,'trusted_read',lambda _:state),patch.object(admission,'snapshot',lambda path,stores,resource=None:original(path,stores,resource,boot_path=str(boot))):
   admission.install_backend_guard(app,'fixture',{'backend':url},'board')
   lab=WebLab(app)
   @lab.initial_url
   def initial_url():return url_for('control')
   @app.route('/control',methods=['GET','POST'])
   @requires_active
   def control():return 'ACTUATED'
   admin=app.test_client();old=app.test_client();fresh=app.test_client()
   def reserve(epoch):
    data=dict(client_initial_data={},server_initial_data={'priority.queue.slot.start.timestamp':str(time.time()),'priority.queue.slot.start':datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.0'),'priority.queue.slot.length':300,'request.username':'test','request.full_name':'Fixture','request.username.unique':'fixture','request.locale':'en','request.experiment_id.experiment_name':'fixture','request.experiment_id.category_name':'fixture'},back='https://example.invalid/')
    return admin.post('/weblab/sessions/',data=json.dumps(data),headers={'Authorization':'Basic '+base64.b64encode(b'fixture:fixture').decode(),'X-Lab-Recovery-Epoch':epoch})
   identity=state.pop('scheduler_identity')
   assert reserve('a'*32).status_code==503
   state['scheduler_identity']=identity
   response=reserve('a'*32);assert response.status_code==200,response.data
   launch=response.get_json()['url'];old.get(launch,follow_redirects=True)
   assert old.post('/control').data==b'ACTUATED'
   state['resources']['board']['phase']='recovering'
   assert old.post('/control').status_code==503
   # Actual retirement Lua, only this test's WebLab namespace and isolated Redis.
   count=0
   for key in list(connection.scan_iter('fixture:*')):count+=connection.eval(RETIRE,2,key,'lab-recovery-archive:'+'b'*32+':'+key,runid,604800)
   assert count>0
   state['resources']['board'].update(phase='ready',epoch='b'*32)
   assert old.post('/control').data!=b'ACTUATED'
   assert reserve('a'*32).status_code==503
   response=reserve('b'*32);assert response.status_code==200,response.data
   fresh.get(response.get_json()['url'],follow_redirects=True)
   assert fresh.post('/control').data==b'ACTUATED'
   lab._cleanup()
   print(json.dumps(dict(old_session_revoked=True,old_epoch_rejected=True,new_session_works=True,retired_keys=count)))
 finally:
  process.terminate();process.wait(timeout=10)
