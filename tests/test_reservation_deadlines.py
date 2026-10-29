"""Real Redis checks of user deadlines versus persistent hardware ownership."""
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import redis
import redis.asyncio

from tests.test_async_processor_cleanup import build_processor, processor_module
from tests.test_scheduling_health import _reservation_request
from labdiscoveryengine.scheduling.asyncio import redis as scheduler_redis

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('redis-server'), 'redis-server required')
class ReservationDeadlines(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='lde-deadlines-')
        cls.socket = str(Path(cls.tmp.name) / 'redis.sock')
        cls.server = subprocess.Popen(['redis-server', '--port', '0', '--unixsocket', cls.socket,
                                      '--save', '', '--appendonly', 'no'],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.store = redis.Redis(unix_socket_path=cls.socket, decode_responses=True)
        for _ in range(100):
            try:
                if cls.store.ping(): break
            except redis.ConnectionError: time.sleep(.02)
        else: raise RuntimeError('Temporary Redis did not start')
        cls.assign = cls.store.register_script((ROOT / 'labdiscoveryengine/lua/assign_reservation_to_resource.lua').read_text())
        cls.status = cls.store.register_script((ROOT / 'labdiscoveryengine/lua/get_reservation_status.lua').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.store.close(); cls.server.terminate(); cls.server.wait(timeout=5); cls.tmp.cleanup()

    async def asyncSetUp(self):
        self.store.flushdb()
        self.connection = redis.asyncio.Redis(unix_socket_path=self.socket, decode_responses=True)
        self.p = build_processor()
        self.key = self.p.reservation_keys.base()
        self.owner = self.p.resource_keys.assigned()
        self.request = _reservation_request(['resource-1'])._replace(identifier=self.p.reservation_id, max_time=.15)
        self.run_id = self.store.info('server')['run_id']

    async def asyncTearDown(self):
        await self.connection.aclose()

    def claim(self, timeout=.3, managed=False):
        self.store.hset(self.key, mapping=dict(status='pending', metadata=json.dumps(self.request.todict())))
        self.store.sadd(self.key + ':resources', 'resource-1')
        self.store.zadd('lde:resources:resource-1:queues:priorities', {'normal': 0})
        self.store.rpush('lde:resources:resource-1:queues:normal', self.p.reservation_id)
        self.store.set('lde:external-request:' + self.p.reservation_id, 'fingerprint')
        if managed:
            self.p.recovery_managed = True
            self.store.set('lde:running', 'true')
            self.store.set('lde:resources:resource-1:recovery-epoch', 'epoch-1')
        self.assertEqual(self.assign(args=['resource-1', self.run_id if managed else '', 'epoch-1' if managed else '', timeout]), self.p.reservation_id)

    def assert_terminal_retained(self):
        state = self.status(args=[self.p.reservation_id])
        self.assertEqual(state[0], 'broken', state)
        self.assertFalse(state[1]); self.assertFalse(state[3])
        self.assertEqual(self.store.get(self.owner), self.p.reservation_id)
        self.assertEqual(self.store.ttl(self.owner), -1)
        self.assertEqual(self.store.ttl(self.key), -1)
        self.assertEqual(self.store.ttl('lde:external-request:' + self.p.reservation_id), -1)
        self.assertFalse(self.assign(args=['resource-1']))

    async def run_processor(self, client):
        with patch.object(processor_module, 'aioredis_store', self.connection), \
             patch.object(scheduler_redis, 'maintenance_run_id', self.run_id), \
             patch.object(processor_module, 'is_mongo_active', return_value=False), \
             patch.object(self.p, 'get_client', return_value=client):
            await asyncio.wait_for(self.p.process(), 2)

    async def test_worker_lost_after_claim_is_terminal_without_expiring_ownership(self):
        self.claim(timeout=.05)
        self.assertEqual(self.status(args=[self.p.reservation_id])[0], 'pending')
        await asyncio.sleep(.07)  # No processor runs after the atomic claim.
        self.assert_terminal_retained()
        # A delayed HTTP success / worker write must not resurrect its URL.
        self.store.hset(self.key, mapping=dict(status='ready', session_id='late', url='https://late.invalid', session_deadline=time.time()+100))
        self.assert_terminal_retained()

    async def test_initialization_timeout_cancels_wait_but_keeps_uncertain_hardware(self):
        # Allow startup work before exercising cancellation of the hung call.
        # A 50ms budget could expire before start() on a loaded test host.
        self.claim(timeout=1, managed=True)
        client = Client(start_delay=60)
        await self.run_processor(client)
        self.assertEqual(client.starts, 1)
        self.assertTrue(client.start_cancelled)
        self.assertEqual(client.finishes, 0)
        self.assert_terminal_retained()
        self.assertEqual(self.store.hget(self.key, 'reconciliation_required'), '1')
        await self.run_processor(client)
        self.assertEqual(client.starts, 1)

    async def test_expired_initialization_never_starts_the_backend(self):
        self.claim(timeout=.01)
        await asyncio.sleep(.03)
        client = Client()
        await self.run_processor(client)
        self.assertEqual(client.starts, 0)
        self.assert_terminal_retained()

    async def test_zero_and_hung_status_end_session_and_release_only_after_cleanup(self):
        for status_delay in (0, 60):
            for managed in (False, True):
                with self.subTest(status_delay=status_delay, managed=managed):
                    self.store.flushdb(); self.p = build_processor()
                    self.claim(managed=managed)
                    client = Client(status_delay=status_delay)
                    await self.run_processor(client)
                    self.assertEqual(client.starts, 1); self.assertEqual(client.finishes, 1)
                    self.assertFalse(self.store.exists(self.owner))
                    self.assertEqual(self.store.hget(self.key, 'status'), 'finished')
                    self.assertGreater(self.store.ttl(self.key), 0)
                    self.assertEqual(self.status(args=[self.p.reservation_id])[0], 'broken')

    async def test_cleanup_failure_after_deadline_preserves_assignment_and_epoch(self):
        self.claim(managed=True)
        client = Client(cleanup_result='invalid')
        await self.run_processor(client)
        self.assertEqual(client.finishes, 1)
        self.assert_terminal_retained()
        self.assertEqual(self.store.get('lde:resources:resource-1:recovery-epoch'), 'epoch-1')
        self.assertEqual(self.store.hget(self.key, 'reconciliation_required'), '1')

    async def test_restart_does_not_renew_session_deadline_or_repeat_start(self):
        self.claim()
        deadline = time.time() - 1
        self.store.hset(self.key, mapping=dict(status='ready', session_id='existing', start_attempted='1', session_deadline=deadline))
        client = Client()
        await self.run_processor(client)
        self.assertEqual(client.starts, 0); self.assertEqual(client.finishes, 1)
        self.assertEqual(float(self.store.hget(self.key, 'session_deadline')), deadline)
        self.assertEqual(self.status(args=[self.p.reservation_id])[0], 'broken')

    async def test_status_expiry_covers_cleanup_and_rejects_corrupt_deadlines(self):
        self.claim()
        for status in ('pending', 'initializing', 'ready', 'cancelling', 'finishing'):
            for value in ('nan', 'inf', 'bad', '-inf', '0'):
                with self.subTest(status=status, value=value):
                    self.store.hdel(self.key, 'deadline_expired')
                    self.store.hset(self.key, mapping=dict(status=status, initialization_deadline=value, session_deadline=value))
                    self.assert_terminal_retained()
        self.store.hdel(self.key, 'deadline_expired')
        self.store.hset(self.key, 'status', 'finished')
        self.assertEqual(self.status(args=[self.p.reservation_id])[0], 'finished')

    async def test_real_http_expiry_hides_session_while_cleanup_is_still_running(self):
        from aiohttp import web
        entered_cleanup, allow_cleanup = asyncio.Event(), asyncio.Event()
        async def status(request):
            return web.json_response({'should_finish': 0})
        async def finish(request):
            entered_cleanup.set()
            await allow_cleanup.wait()
            return web.json_response({'message': 'Deleted'})
        app = web.Application()
        app.router.add_get('/ldl/sessions/existing/status', status)
        app.router.add_delete('/ldl/sessions/existing', finish)
        server = web.AppRunner(app)
        await server.setup()
        site = web.TCPSite(server, '127.0.0.1', 0)
        await site.start()
        self.p.resource = self.p.resource._replace(url='http://127.0.0.1:' + str(server.addresses[0][1]))
        self.claim()
        self.store.hset(self.key, mapping=dict(status='ready', session_id='existing',
                       session_deadline=time.time()+.1, url='https://offline.invalid'))
        task = None
        try:
            with patch.object(processor_module, 'aioredis_store', self.connection), \
                 patch.object(processor_module, 'is_mongo_active', return_value=False):
                task = asyncio.create_task(self.p.process())
                await asyncio.wait_for(entered_cleanup.wait(), 2)
                self.assert_terminal_retained()
                self.assertFalse(task.done())
                allow_cleanup.set()
                await asyncio.wait_for(task, 2)
                self.assertFalse(self.store.exists(self.owner))
                self.assertEqual(self.status(args=[self.p.reservation_id])[0], 'broken')
        finally:
            allow_cleanup.set()
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await server.cleanup()

    async def test_bad_initialization_config_does_not_pop_queue(self):
        self.store.zadd('lde:resources:resource-1:queues:priorities', {'normal': 0})
        self.store.rpush('lde:resources:resource-1:queues:normal', 'untouched')
        for bad in ('NaN', 'inf', 'bad', '0', '-1'):
            with self.subTest(timeout=bad), self.assertRaises(redis.ResponseError):
                self.assign(args=['resource-1', '', '', bad])
        self.assertEqual(self.store.lrange('lde:resources:resource-1:queues:normal', 0, -1), ['untouched'])


class Client:
    def __init__(self, start_delay=0, status_delay=0, cleanup_result=-1):
        self.start_delay, self.status_delay, self.cleanup_result = start_delay, status_delay, cleanup_result
        self.starts = self.finishes = 0
        self.start_cancelled = False
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def start(self, request):
        self.starts += 1
        try: await asyncio.sleep(self.start_delay)
        except asyncio.CancelledError:
            self.start_cancelled = True
            raise
        return 'https://offline.invalid/session', 'session'
    async def get_should_finish(self, session):
        await asyncio.sleep(self.status_delay)
        return 0
    async def finish(self, session):
        self.finishes += 1
        return self.cleanup_result
