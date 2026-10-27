"""Real Redis evidence for the assignment/interruption/late-processor boundary."""
import asyncio
import shutil
import unittest
from unittest.mock import AsyncMock, patch
import redis.asyncio
from tests.test_maintenance_redis import MaintenanceRedis, ASSIGN
from labdiscoveryengine.recovery.ownership import TRANSITION, OwnershipLost
from labdiscoveryengine.recovery.managed import INTERRUPT_RESOURCE, RELEASE
from labdiscoveryengine.scheduling.asyncio import redis as scheduler_redis
from tests.test_async_processor_cleanup import build_processor, processor_module


@unittest.skipUnless(shutil.which('redis-server'), 'Requires disposable Redis')
class RecoveryRedis(unittest.IsolatedAsyncioTestCase):
    setUp = MaintenanceRedis.setUp
    tearDown = MaintenanceRedis.tearDown
    queue = MaintenanceRedis.queue
    def ready(self):
        self.epoch = 'e' * 32
        self.run_id = self.r.info('server')['run_id']
        self.r.set('lde:running','true')
        self.r.set(self.base + ':recovery-epoch',self.epoch)
        self.queue()
    def assign(self, epoch=None):
        return self.r.eval(ASSIGN,0,self.resource,self.run_id,epoch or self.epoch)
    def transition(self, epoch=None, action='check', **fields):
        args=[]
        for key,value in fields.items():args.extend([key,value])
        return self.r.eval(TRANSITION,3,self.base,'lde:reservations:student',
            'lde:reservations:student:channel',self.run_id,epoch or self.epoch,'student',action,*args)
    def interrupt(self):
        self.r.set(self.base + ':recovery-hold','f'*32)
        return self.r.eval(INTERRUPT_RESOURCE,1,self.base,self.run_id,'f'*32)
    async def test_assignment_captures_epoch_and_old_processor_cannot_upgrade_it(self):
        self.ready();self.assertEqual(self.assign(),'student')
        self.assertEqual(self.r.hget('lde:reservations:student','recovery_epoch'),self.epoch)
        self.r.set(self.base+':recovery-epoch','f'*32)
        self.assertEqual(self.transition(),0)
        self.assertEqual(self.transition(epoch='f'*32),0)
    async def test_hold_or_stale_epoch_never_consumes_queue(self):
        self.ready(); self.assertIsNone(self.assign('f'*32))
        self.r.set(self.base+':recovery-hold','f'*32)
        self.assertIsNone(self.assign())
        self.assertEqual(self.r.lrange(self.base+':queues:1',0,-1),['student'])
    async def test_interruption_retires_owner_queue_and_preserves_maintenance(self):
        self.ready();self.assign();self.r.set(self.base+':maintenance','independent-owner')
        self.assertEqual(self.interrupt(),1)
        self.assertIsNone(self.r.get(self.base+':assigned'))
        self.assertEqual(self.r.get(self.base+':maintenance'),'independent-owner')
        self.assertEqual(self.r.hget('lde:reservations:student','status'),'broken')
        for action in ['claim','check','release','quarantine']:
            self.assertEqual(self.transition(action=action,status='ready'),0)
        self.assertEqual(self.r.hget('lde:reservations:student','status'),'broken')
    async def test_recovery_preserves_unassigned_requests_for_healthy_peer(self):
        self.ready()
        self.assertEqual(self.interrupt(),1)
        self.assertEqual(self.r.lrange(self.base+':queues:1',0,-1),['student'])
        self.assertNotEqual(self.r.hget('lde:reservations:student','status'),'broken')
        self.assertEqual(self.r.zrange(self.base+':queues:priorities',0,-1),['1'])
    async def test_release_requires_no_assignment_and_preserves_foreign_hold(self):
        self.ready();self.assign();self.r.set(self.base+':recovery-hold','f'*32)
        self.assertEqual(self.r.eval(RELEASE,1,self.base,self.run_id,'f'*32),0)
        self.interrupt()
        self.assertEqual(self.r.eval(RELEASE,1,self.base,self.run_id,'e'*32),0)
        self.assertEqual(self.r.eval(RELEASE,1,self.base,self.run_id,'f'*32),1)
    async def test_redis_generation_change_blocks_transition_without_mutating(self):
        self.ready();self.assign();self.run_id='0'*40
        self.assertEqual(self.transition(action='claim',status='initializing'),0)
        self.assertFalse(self.r.hexists('lde:reservations:student','start_attempted'))
    async def test_interruption_during_http_start_cannot_resurrect_ready(self):
        self.ready();self.assign()
        resource=build_processor().resource._replace(identifier=self.resource)
        p=processor_module.ResourceReservationProcessor(resource,'student')
        p.recovery_managed=True;p.recovery_epoch=self.epoch
        async def start(_request):
            self.interrupt()
            return 'https://lab.example','old-session'
        p.client=type('Client',(),{'start':AsyncMock(side_effect=start)})()
        connection=redis.asyncio.Redis.from_url('redis://127.0.0.1:%s/0' % self.r.connection_pool.connection_kwargs['port'],decode_responses=True)
        try:
            with patch.object(processor_module,'aioredis_store',connection), \
                 patch.object(scheduler_redis,'maintenance_run_id',self.run_id), \
                 patch.object(processor_module,'is_mongo_active',return_value=False):
                with self.assertRaises(OwnershipLost):await p.initialize_laboratory(None)
        finally:await connection.aclose()
        self.assertEqual(self.r.hget('lde:reservations:student','status'),'broken')
        self.assertIsNone(self.r.hget('lde:reservations:student','session_id'))
        self.assertIsNone(self.r.get(self.base+':assigned'))
    async def test_old_processor_cleanup_does_not_touch_new_owner(self):
        self.ready();self.assign();self.interrupt()
        self.r.set(self.base+':assigned','new-student')
        self.assertEqual(self.transition(action='release'),0)
        self.assertEqual(self.r.get(self.base+':assigned'),'new-student')


@unittest.skipUnless(shutil.which('redis-server'), 'Requires disposable Redis')
class ConnectionGeneration(unittest.TestCase):
    def bindings(self, run_id=None):
        import json
        return json.dumps([dict(identity=dict(host='127.0.0.1',port=self.r.connection_pool.connection_kwargs['port'],db=0),
                                run_id=run_id or self.r.info('server')['run_id'])])
    setUp = MaintenanceRedis.setUp
    tearDown = MaintenanceRedis.tearDown
    def test_restarted_redis_cannot_replay_restored_queue_or_pipeline(self):
        import os,subprocess,time
        from labdiscoveryengine.recovery.redis_connection import fenced_redis, RedisGenerationChanged
        port=self.r.connection_pool.connection_kwargs['port']
        self.r.rpush('jobs','old-job');self.r.save()
        with patch.dict(os.environ,LAB_RECOVERY_EXPECTED_REDIS_STORES=self.bindings()):
            old=fenced_redis('redis://127.0.0.1:%s/0' % port,decode_responses=True)
        self.addCleanup(old.close)
        self.assertEqual(old.llen('jobs'),1)
        self.proc.terminate();self.proc.wait(timeout=5)
        self.proc=subprocess.Popen(['redis-server','--port',str(port),'--bind','127.0.0.1',
            '--dir',self.tmp.name,'--save','','--appendonly','no'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:self.r.ping();break
            except redis.ConnectionError:time.sleep(.02)
        else:self.fail('Replacement Redis did not start')
        self.assertEqual(self.r.lrange('jobs',0,-1),['old-job'])
        with self.assertRaises(RedisGenerationChanged):old.lpop('jobs')
        with self.assertRaises(RedisGenerationChanged):old.pipeline().lpop('jobs').set('stale-write','yes').execute()
        self.assertEqual(self.r.lrange('jobs',0,-1),['old-job'])
        self.assertFalse(self.r.exists('stale-write'))
    def test_same_generation_reconnect_remains_usable(self):
        import os
        from labdiscoveryengine.recovery.redis_connection import fenced_redis
        with patch.dict(os.environ,LAB_RECOVERY_EXPECTED_REDIS_STORES=self.bindings()):
            client=fenced_redis('redis://127.0.0.1:%s/0' % self.r.connection_pool.connection_kwargs['port'])
        self.addCleanup(client.close)
        client.connection_pool.disconnect()
        self.assertTrue(client.ping())

    def test_pubsub_reconnect_verifies_generation_before_resubscribing(self):
        import os,time
        from labdiscoveryengine.recovery.redis_connection import fenced_redis
        with patch.dict(os.environ,LAB_RECOVERY_EXPECTED_REDIS_STORES=self.bindings()):
            client=fenced_redis('redis://127.0.0.1:%s/0' % self.r.connection_pool.connection_kwargs['port'])
        self.addCleanup(client.close)
        pubsub=client.pubsub();self.addCleanup(pubsub.close);pubsub.subscribe('qualification')
        self.assertEqual(pubsub.get_message(timeout=2)['type'],'subscribe')
        pubsub.connection.disconnect()
        self.assertEqual(pubsub.get_message(timeout=2)['type'],'subscribe')
        self.r.publish('qualification','after-reconnect')
        self.assertEqual(pubsub.get_message(timeout=2)['data'],b'after-reconnect')
    def test_wrong_resolved_store_fails_at_startup(self):
        import os
        from labdiscoveryengine.recovery.redis_connection import fenced_redis,RedisGenerationChanged
        with patch.dict(os.environ,LAB_RECOVERY_EXPECTED_REDIS_STORES=self.bindings('0'*40)):
            with self.assertRaises(RedisGenerationChanged):
                fenced_redis('redis://127.0.0.1:%s/0' % self.r.connection_pool.connection_kwargs['port'])

    def test_same_server_wrong_database_is_rejected(self):
        import os
        from labdiscoveryengine.recovery.redis_connection import fenced_redis
        with patch.dict(os.environ, LAB_RECOVERY_EXPECTED_REDIS_STORES=self.bindings()):
            with self.assertRaises(ValueError):
                fenced_redis('redis://127.0.0.1:%s/1' % self.r.connection_pool.connection_kwargs['port'])
