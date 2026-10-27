"""Standalone compatibility suite for redis-py versions used by executors."""
import os,sys,shutil,unittest,redis
from unittest.mock import patch
@unittest.skipUnless(shutil.which('redis-server'), 'Requires disposable Redis')
class ConnectionGeneration(unittest.TestCase):
    def bindings(self, run_id=None):
        import json
        return json.dumps([dict(identity=dict(host='127.0.0.1',port=self.r.connection_pool.connection_kwargs['port'],db=0),
                                run_id=run_id or self.r.info('server')['run_id'])])
    def setUp(self):
        import socket, tempfile, subprocess, time
        self.tmp=tempfile.TemporaryDirectory()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        self.proc=subprocess.Popen(['redis-server','--port',str(port),'--bind','127.0.0.1',
            '--dir',self.tmp.name,'--save','','--appendonly','no'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.r=redis.Redis(host='127.0.0.1',port=port,decode_responses=True)
        for _ in range(100):
            try:self.r.ping();return
            except redis.ConnectionError:time.sleep(.02)
        self.fail('Disposable Redis did not start')
    def tearDown(self):
        self.r.connection_pool.disconnect();self.proc.terminate();self.proc.wait(timeout=5);self.tmp.cleanup()
    def test_restarted_redis_cannot_replay_restored_queue_or_pipeline(self):
        import os,subprocess,time
        from labsland_recovery.redis_connection import fenced_redis, RedisGenerationChanged
        port=self.r.connection_pool.connection_kwargs['port']
        self.r.rpush('jobs','old-job');self.r.save()
        with patch.dict(os.environ,LAB_RECOVERY_EXPECTED_REDIS_STORES=self.bindings()):
            old=fenced_redis('redis://127.0.0.1:%s/0' % port,decode_responses=True)
        self.addCleanup(old.connection_pool.disconnect)
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
        from labsland_recovery.redis_connection import fenced_redis
        with patch.dict(os.environ,LAB_RECOVERY_EXPECTED_REDIS_STORES=self.bindings()):
            client=fenced_redis('redis://127.0.0.1:%s/0' % self.r.connection_pool.connection_kwargs['port'])
        self.addCleanup(client.connection_pool.disconnect)
        client.connection_pool.disconnect()
        self.assertTrue(client.ping())
    def test_pubsub_reconnect_verifies_generation_before_resubscribing(self):
        import os,time
        from labsland_recovery.redis_connection import fenced_redis
        with patch.dict(os.environ,LAB_RECOVERY_EXPECTED_REDIS_STORES=self.bindings()):
            client=fenced_redis('redis://127.0.0.1:%s/0' % self.r.connection_pool.connection_kwargs['port'])
        self.addCleanup(client.connection_pool.disconnect)
        pubsub=client.pubsub();self.addCleanup(pubsub.close);pubsub.subscribe('qualification')
        self.assertEqual(pubsub.get_message(timeout=2)['type'],'subscribe')
        pubsub.connection.disconnect()
        self.assertEqual(pubsub.get_message(timeout=2)['type'],'subscribe')
        self.r.publish('qualification','after-reconnect')
        self.assertEqual(pubsub.get_message(timeout=2)['data'],b'after-reconnect')
    def test_wrong_resolved_store_fails_at_startup(self):
        import os
        from labsland_recovery.redis_connection import fenced_redis,RedisGenerationChanged
        with patch.dict(os.environ,LAB_RECOVERY_EXPECTED_REDIS_STORES=self.bindings('0'*40)):
            with self.assertRaises(RedisGenerationChanged):
                fenced_redis('redis://127.0.0.1:%s/0' % self.r.connection_pool.connection_kwargs['port'])

    def test_same_server_wrong_database_is_rejected(self):
        import os
        from labsland_recovery.redis_connection import fenced_redis
        with patch.dict(os.environ, LAB_RECOVERY_EXPECTED_REDIS_STORES=self.bindings()):
            with self.assertRaises(ValueError):
                fenced_redis('redis://127.0.0.1:%s/1' % self.r.connection_pool.connection_kwargs['port'])

if __name__=='__main__':unittest.main()
