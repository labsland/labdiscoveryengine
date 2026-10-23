"""Real WATCH semantics on a disposable Redis, never a configured deployment."""
import asyncio
import shutil
import socket
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import redis.asyncio as redis
from labdiscoveryengine.data import JsonSuccessHealthcheck
from labdiscoveryengine.scheduling.keys import ResourceKeys
from labdiscoveryengine.scheduling.asyncio.healthcheck_worker import ResourceHealthchecksWorker


@unittest.skipUnless(shutil.which('redis-server'), 'Requires disposable redis-server')
class IdleHealthRedisTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder=tempfile.TemporaryDirectory()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        self.process=subprocess.Popen(['redis-server','--port',str(port),'--bind','127.0.0.1',
            '--dir',self.folder.name,'--save','','--appendonly','no'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.store=redis.Redis(port=port,decode_responses=True)
        for _ in range(100):
            try:await self.store.ping();break
            except redis.ConnectionError:await asyncio.sleep(.02)
        else:raise AssertionError('Disposable Redis failed to start')
        self.worker=object.__new__(ResourceHealthchecksWorker)
        self.worker.resource_name='board-1';self.worker.resource_keys=ResourceKeys('board-1')
        self.worker.resource=SimpleNamespace(healthchecks=[JsonSuccessHealthcheck('ready','http://unused.invalid',idle_only=True)])
        await self.store.hset(self.worker.resource_keys.health(),mapping={'status':'healthy','checked_at':'original'})
        self.mock=patch('labdiscoveryengine.scheduling.asyncio.healthcheck_worker.aioredis_store',self.store)
        self.mock.start()

    async def asyncTearDown(self):
        self.mock.stop()
        await self.store.aclose()
        self.process.terminate();self.process.wait(timeout=5);self.folder.cleanup()

    async def test_quarantined_owner_preserves_health_and_timestamp(self):
        await self.store.set(self.worker.resource_keys.assigned(),'quarantined-owner')
        self.worker._collect_health=AsyncMock()
        await self.worker.check_robotchecker_health()
        self.worker._collect_health.assert_not_called()
        self.assertEqual(await self.store.hgetall(self.worker.resource_keys.health()),{'status':'healthy','checked_at':'original'})

    async def test_complete_ownership_cycle_during_probe_discards_stale_failure(self):
        async def probe():
            await self.store.set(self.worker.resource_keys.assigned(),'owner')
            await self.store.delete(self.worker.resource_keys.assigned())
            return 'broken','cleanup in progress','configured-checks'
        self.worker._collect_health=probe
        await self.worker.check_robotchecker_health()
        self.assertEqual(await self.store.hgetall(self.worker.resource_keys.health()),{'status':'healthy','checked_at':'original'})

    async def test_real_idle_failure_still_blocks(self):
        self.worker._collect_health=AsyncMock(return_value=('broken','offline','configured-checks'))
        await self.worker.check_robotchecker_health()
        self.assertEqual(await self.store.hget(self.worker.resource_keys.health(),'status'),'broken')
        self.assertEqual(await self.store.hget(self.worker.resource_keys.health(),'message'),'offline')

    async def test_maintenance_preserves_health_without_probe(self):
        await self.store.set(self.worker.resource_keys.maintenance(), 'owner')
        self.worker._collect_health=AsyncMock()
        await self.worker.check_robotchecker_health()
        self.worker._collect_health.assert_not_called()
        self.assertEqual(await self.store.hgetall(self.worker.resource_keys.health()),{'status':'healthy','checked_at':'original'})

    async def test_maintenance_cycle_during_probe_discards_stale_sample(self):
        async def probe():
            await self.store.set(self.worker.resource_keys.maintenance(), 'owner')
            await self.store.delete(self.worker.resource_keys.maintenance())
            return 'healthy', None, 'configured-checks'
        self.worker._collect_health=probe
        await self.worker.check_robotchecker_health()
        self.assertEqual(await self.store.hgetall(self.worker.resource_keys.health()),{'status':'healthy','checked_at':'original'})

    async def test_active_probe_excludes_recovery_until_completion(self):
        from labdiscoveryengine.scheduling.maintenance import ACQUIRE
        run_id=(await self.store.info('server'))['run_id']
        await self.store.set('lde:running','true')
        async def collect():
            self.assertEqual(await self.store.eval(ACQUIRE,1,self.worker.resource_keys.base(),'a'*32),0)
            return 'healthy',None,'configured-checks'
        self.worker._collect_health=collect
        with patch('labdiscoveryengine.scheduling.asyncio.redis.maintenance_run_id',run_id), patch('labdiscoveryengine.scheduling.asyncio.redis.maintenance_resources',{'board-1'}), patch('labdiscoveryengine.scheduling.asyncio.redis.maintenance_state_dir',self.folder.name):
            await self.worker.check_robotchecker_health()
        self.assertEqual(await self.store.exists(self.worker.resource_keys.base()+':health-probe'),0)
        self.assertEqual(await self.store.eval(ACQUIRE,1,self.worker.resource_keys.base(),'a'*32),1)

    async def test_canceled_probe_keeps_unknown_hardware_outcome_excluded(self):
        from labdiscoveryengine.scheduling.maintenance import ACQUIRE
        run_id=(await self.store.info('server'))['run_id']
        await self.store.set('lde:running','true')
        self.worker._collect_health=AsyncMock(side_effect=asyncio.CancelledError)
        with patch('labdiscoveryengine.scheduling.asyncio.redis.maintenance_run_id',run_id), patch('labdiscoveryengine.scheduling.asyncio.redis.maintenance_resources',{'board-1'}), patch('labdiscoveryengine.scheduling.asyncio.redis.maintenance_state_dir',self.folder.name):
            with self.assertRaises(asyncio.CancelledError):await self.worker.check_robotchecker_health()
        self.assertEqual(await self.store.eval(ACQUIRE,1,self.worker.resource_keys.base(),'a'*32),0)
        self.assertEqual(await self.store.ttl(self.worker.resource_keys.base()+':health-probe'),-1)

    async def test_timed_out_probe_keeps_hold_even_after_returning_health(self):
        from labdiscoveryengine.scheduling.maintenance import ACQUIRE
        run_id=(await self.store.info('server'))['run_id']
        await self.store.set('lde:running','true')
        async def collect():
            self.worker._probe_transport_uncertain=True
            return 'broken','timeout','configured-checks'
        self.worker._collect_health=collect
        with patch('labdiscoveryengine.scheduling.asyncio.redis.maintenance_run_id',run_id), patch('labdiscoveryengine.scheduling.asyncio.redis.maintenance_resources',{'board-1'}), patch('labdiscoveryengine.scheduling.asyncio.redis.maintenance_state_dir',self.folder.name):
            await self.worker.check_robotchecker_health()
        self.assertEqual(await self.store.eval(ACQUIRE,1,self.worker.resource_keys.base(),'a'*32),0)

    async def test_backend_still_running_after_redis_loss_stays_excluded_on_reinit(self):
        from aiohttp import web
        from labdiscoveryengine.scheduling.asyncio import redis as scheduling_redis
        from labdiscoveryengine.scheduling.maintenance import ACQUIRE
        entered, complete = asyncio.Event(), asyncio.Event()
        async def backend(request):
            entered.set(); await complete.wait(); return web.json_response({'success':True})
        app=web.Application();app.router.add_get('/health',backend)
        runner=web.AppRunner(app);await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        self.worker.resource.healthchecks=[JsonSuccessHealthcheck('held',f'http://127.0.0.1:{port}/health',timeout=10,idle_only=True)]
        redis_port=self.store.connection_pool.connection_kwargs['port']
        cfg=SimpleNamespace(config={'REDIS_URL':f'redis://127.0.0.1:{redis_port}/0',
                                    'SERIAL_RECOVERY_STATE_DIR':self.folder.name,'SERIAL_RECOVERY_RESOURCES':'board-1'})
        task=None
        try:
            with patch.object(scheduling_redis,'current_app',cfg), \
                 patch('labdiscoveryengine.utils.lde_config',SimpleNamespace(resources={'board-1':self.worker.resource})), \
                 patch.object(scheduling_redis,'maintenance_run_id',''), \
                 patch.object(scheduling_redis,'maintenance_resources',set()), \
                 patch.object(scheduling_redis,'maintenance_state_dir',None):
                await scheduling_redis.initialize_redis()
                task=asyncio.create_task(self.worker.check_robotchecker_health())
                await asyncio.wait_for(entered.wait(),3)
                old_epoch=(await self.store.info('server'))['run_id']
                self.process.terminate();self.process.wait(timeout=5)
                self.process=subprocess.Popen(['redis-server','--port',str(redis_port),'--bind','127.0.0.1',
                    '--dir',self.folder.name,'--save','','--appendonly','no'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                for _ in range(100):
                    try:await self.store.ping();break
                    except redis.ConnectionError:await asyncio.sleep(.02)
                self.assertNotEqual((await self.store.info('server'))['run_id'],old_epoch)
                self.assertFalse(await self.store.exists(self.worker.resource_keys.base()+':health-probe'))
                await scheduling_redis.aioredis_store.aclose()
                await scheduling_redis.initialize_redis()
                self.assertEqual(await self.store.eval(ACQUIRE,1,self.worker.resource_keys.base(),'a'*32),0)
                self.assertFalse(complete.is_set())
                complete.set();await asyncio.wait_for(task,3)
                self.assertEqual(await self.store.eval(ACQUIRE,1,self.worker.resource_keys.base(),'a'*32),1)
                await scheduling_redis.aioredis_store.aclose()
        finally:
            complete.set()
            if task and not task.done():task.cancel()
            await runner.cleanup()

    async def test_old_epoch_paused_between_acquire_and_fsync_never_sends_http(self):
        from labdiscoveryengine.scheduling.asyncio import redis as scheduling_redis
        from labdiscoveryengine.scheduling.maintenance import BEGIN_PROBE
        before_save, resume = asyncio.Event(), asyncio.Event()
        real_eval=self.store.eval
        async def pause_after_acquire(script,*args):
            result=await real_eval(script,*args)
            if script==BEGIN_PROBE:
                before_save.set();await resume.wait()
            return result
        old_epoch=(await self.store.info('server'))['run_id']
        await self.store.set('lde:running','true')
        self.worker._collect_health=AsyncMock(return_value=('healthy',None,'configured-checks'))
        with patch.object(scheduling_redis,'maintenance_run_id',old_epoch), \
             patch.object(scheduling_redis,'maintenance_resources',{'board-1'}), \
             patch.object(scheduling_redis,'maintenance_state_dir',self.folder.name), \
             patch.object(self.store,'eval',pause_after_acquire):
            task=asyncio.create_task(self.worker.check_robotchecker_health())
            await asyncio.wait_for(before_save.wait(),3)
            # Restart before durable intention, then initialize the real new
            # scheduler. The old operation must retain its captured epoch.
            redis_port=self.store.connection_pool.connection_kwargs['port']
            self.process.terminate();self.process.wait(timeout=5)
            self.process=subprocess.Popen(['redis-server','--port',str(redis_port),'--bind','127.0.0.1',
                '--dir',self.folder.name,'--save','','--appendonly','no'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            for _ in range(100):
                try:await self.store.ping();break
                except redis.ConnectionError:await asyncio.sleep(.02)
            cfg=SimpleNamespace(config={'REDIS_URL':f'redis://127.0.0.1:{redis_port}/0',
                'SERIAL_RECOVERY_STATE_DIR':self.folder.name,'SERIAL_RECOVERY_RESOURCES':'board-1'})
            with patch.object(scheduling_redis,'current_app',cfg), patch('labdiscoveryengine.utils.lde_config',SimpleNamespace(resources={'board-1':self.worker.resource})):
                await scheduling_redis.initialize_redis()
            self.assertNotEqual(scheduling_redis.maintenance_run_id,old_epoch)
            await scheduling_redis.aioredis_store.aclose()
            resume.set();await asyncio.wait_for(task,3)
        self.worker._collect_health.assert_not_called()
        self.assertTrue(__import__('pathlib').Path(self.folder.name,'board-1.probe').exists())
