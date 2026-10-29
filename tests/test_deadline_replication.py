"""Run with Redis 4 and current Redis on PATH; no shared server is used."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

import redis

LUA = Path(__file__).resolve().parents[1] / 'labdiscoveryengine/lua'


@unittest.skipUnless(shutil.which('redis-server'), 'redis-server required')
class DeadlineReplication(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='lde-deadline-aof-')
        self.addCleanup(self.tmp.cleanup)
        self.server = None
        self.store = None
        self.addCleanup(self.stop)
        self.socket = str(Path(self.tmp.name) / 'redis.sock')
        self.start()

    def start(self):
        self.server = subprocess.Popen(
            ['redis-server', '--port', '0', '--unixsocket', self.socket,
             '--save', '', '--appendonly', 'yes', '--appendfsync', 'always',
             '--dir', self.tmp.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.store = redis.Redis(unix_socket_path=self.socket, decode_responses=True)
        for _ in range(100):
            try:
                if self.store.ping():
                    return
            except redis.ConnectionError:
                time.sleep(.02)
        self.fail('Private Redis failed to start')

    def stop(self):
        if self.store is not None:
            self.store.close()
            self.store = None
        if self.server is not None:
            self.server.terminate()
            self.server.wait(timeout=5)
            self.server = None

    def script(self, name, args):
        return self.store.eval((LUA / name).read_text(), 0, *args)

    def test_claim_expiry_and_aof_reload_preserve_ownership(self):
        for managed in (False, True):
            with self.subTest(managed=managed):
                r = self.store
                r.flushdb()
                key = 'lde:reservations:test'
                resource = 'lde:resources:test'
                r.hset(key, mapping={'status': 'pending', 'metadata': '{}'})
                r.expire(key, 3600)
                r.sadd(key + ':resources', 'test')
                r.expire(key + ':resources', 3600)
                r.set('lde:external-request:test', 'fingerprint', ex=3600)
                r.zadd(resource + ':queues:priorities', {'normal': 0})
                r.rpush(resource + ':queues:normal', 'test')
                run_id = r.info('server')['run_id']
                if managed:
                    r.set('lde:running', 'true')
                    r.set(resource + ':recovery-epoch', 'epoch')
                args = ['test', run_id if managed else '', 'epoch' if managed else '']
                # Omitted timeout exercises the established 300-second default.
                before = r.time()[0]
                self.assertEqual(self.script('assign_reservation_to_resource.lua', args), 'test')
                deadline = r.hget(key, 'initialization_deadline')
                self.assertTrue(before + 299 <= float(deadline) <= r.time()[0] + 301)
                self.assertEqual(r.llen(resource + ':queues:normal'), 0)
                self.assertEqual(r.hget(key, ':assigned'), '1')
                self.assertEqual(r.get(resource + ':assigned'), 'test')
                for retained in (key, key + ':resources', 'lde:external-request:test', resource + ':assigned'):
                    self.assertEqual(r.ttl(retained), -1)
                if managed:
                    self.assertEqual(r.hget(key, 'recovery_epoch'), 'epoch')

                # An unexpired assignment must keep its exact original deadline.
                self.stop()
                self.start()
                r = self.store
                self.assertEqual(r.hget(key, 'initialization_deadline'), deadline)
                self.assertEqual(r.get(resource + ':assigned'), 'test')
                self.assertEqual(r.ttl(key), -1)

                # Legacy records without a session deadline fall back to init.
                r.hset(key, mapping={'status': 'ready', 'session_id': 'test-session',
                                     'url': 'https://offline.invalid', 'initialization_deadline': 1})
                pubsub = r.pubsub()
                try:
                    pubsub.subscribe(key + ':channel')
                    pubsub.get_message(timeout=1)
                    self.assertEqual(self.script('get_reservation_status.lua', ['test'])[:4],
                                     ['broken', None, None, None])
                    message = pubsub.get_message(timeout=1)
                    self.assertIsNotNone(message)
                    self.assertEqual(message['data'], 'deadline-expired')
                finally:
                    pubsub.close()
                self.assertEqual(r.hget(key, 'deadline_expired'), 'session_deadline')
                self.assertEqual(r.get(resource + ':assigned'), 'test')

                # Redis must replay effects, never re-run TIME during AOF load.
                self.stop()
                self.start()
                r = self.store
                self.assertEqual(r.hget(key, 'initialization_deadline'), '1')
                self.assertEqual(r.hget(key, 'deadline_expired'), 'session_deadline')
                self.assertEqual(r.get(resource + ':assigned'), 'test')
                self.assertEqual(r.ttl(key), -1)
                r.hset(key, mapping={'status': 'ready', 'initialization_deadline': time.time() + 300,
                                     'session_deadline': time.time() + 300})
                self.assertEqual(self.script('get_reservation_status.lua', ['test'])[:4],
                                 ['broken', None, None, None])
                self.assertIsNone(self.script('assign_reservation_to_resource.lua', args))
                if managed:
                    # Even if ownership were absent, the stale Redis run ID
                    # must not admit queued work after a server restart.
                    r.delete(resource + ':assigned')
                    r.rpush(resource + ':queues:normal', 'untouched')
                    self.assertIsNone(self.script('assign_reservation_to_resource.lua', args))
                    self.assertEqual(r.lrange(resource + ':queues:normal', 0, -1), ['untouched'])

    def test_legacy_records_without_deadlines_keep_status_semantics(self):
        self.assertEqual(self.script('get_reservation_status.lua', ['missing']), [None] * 6)
        key = 'lde:reservations:legacy'
        self.store.hset(key, mapping={'status': 'ready', 'session_id': 'session', 'url': 'https://offline.invalid'})
        self.assertEqual(self.script('get_reservation_status.lua', ['legacy'])[:4],
                         ['ready', 'session', None, 'https://offline.invalid'])
        self.assertFalse(self.store.hexists(key, 'deadline_expired'))
        self.store.hset(key, mapping={'recovery_interrupted': '1', 'session_deadline': 1})
        self.assertEqual(self.script('get_reservation_status.lua', ['legacy'])[:4],
                         ['broken', None, None, None])
        self.assertFalse(self.store.hexists(key, 'deadline_expired'))
