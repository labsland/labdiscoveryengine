"""Real Redis races against the exact production allocation Lua; no deployment."""
import concurrent.futures
import importlib.util
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import redis

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('maintenance', ROOT / 'labdiscoveryengine/scheduling/maintenance.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
ASSIGN = (ROOT / 'labdiscoveryengine/lua/assign_reservation_to_resource.lua').read_text()

@unittest.skipUnless(shutil.which('redis-server'), 'Requires disposable Redis')
class MaintenanceRedis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        self.proc = subprocess.Popen(['redis-server', '--port', str(port), '--bind', '127.0.0.1',
            '--dir', self.tmp.name, '--save', '', '--appendonly', 'no'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.r = redis.Redis(port=port, decode_responses=True, socket_timeout=1)
        for _ in range(100):
            try: self.r.ping(); break
            except redis.ConnectionError: time.sleep(.02)
        else: self.fail('Redis failed to start')
        self.owner = 'a' * 32
        self.resource = 'board-1'; self.base = 'lde:resources:board-1'
    def tearDown(self):
        self.r.close(); self.proc.terminate(); self.proc.wait(timeout=5); self.tmp.cleanup()
    def queue(self):
        self.r.hset('lde:reservations:student', mapping={'status': 'pending', 'metadata': '{}'})
        self.r.zadd(self.base + ':queues:priorities', {'1': 1})
        self.r.rpush(self.base + ':queues:1', 'student')
    def assign(self): return self.r.eval(ASSIGN, 0, self.resource)
    def test_acquire_persistent_separate_key(self):
        self.assertTrue(m.acquire(self.r, self.resource, self.owner))
        self.assertEqual(self.r.ttl(self.base + ':maintenance'), -1)
        self.assertIsNone(self.r.get(self.base + ':assigned'))
        self.assertFalse(m.acquire(self.r, self.resource, self.owner))
        self.assertTrue(m.owned(self.r, self.resource, self.owner))
    def test_existing_assignment_and_foreign_maintenance_preserved(self):
        for suffix in ['assigned', 'maintenance']:
            self.r.flushdb(); self.r.set(self.base + ':' + suffix, 'foreign')
            self.assertFalse(m.acquire(self.r, self.resource, self.owner))
            self.assertFalse(m.release_verified(self.r, self.resource, self.owner))
            self.assertEqual(self.r.get(self.base + ':' + suffix), 'foreign')
    def test_existing_queue_is_not_consumed_or_displaced(self):
        self.queue(); self.assertFalse(m.acquire(self.r, self.resource, self.owner))
        self.assertEqual(self.r.lrange(self.base + ':queues:1', 0, -1), ['student'])
    def test_arriving_queue_waits_until_verified_owner_release(self):
        self.assertTrue(m.acquire(self.r, self.resource, self.owner)); self.queue()
        for _ in range(3): self.assertIsNone(self.assign())
        self.assertEqual(self.r.lrange(self.base + ':queues:1', 0, -1), ['student'])
        self.assertFalse(m.release_verified(self.r, self.resource, 'b' * 32))
        self.assertTrue(m.release_verified(self.r, self.resource, self.owner))
        self.assertEqual(self.assign(), 'student')
    def test_unexpected_assignment_blocks_release(self):
        m.acquire(self.r, self.resource, self.owner); self.r.set(self.base + ':assigned', 'foreign')
        self.assertFalse(m.owned(self.r, self.resource, self.owner))
        self.assertFalse(m.release_verified(self.r, self.resource, self.owner))
        self.assertEqual(self.r.get(self.base + ':maintenance'), self.owner)
    def test_crashed_coordinator_new_client_cannot_take_over(self):
        m.acquire(self.r, self.resource, self.owner)
        r2 = redis.Redis(connection_pool=self.r.connection_pool)
        self.assertFalse(m.acquire(r2, self.resource, 'b' * 32)); self.queue()
        self.assertIsNone(self.assign()); self.assertEqual(self.r.get(self.base + ':maintenance'), self.owner)
    def test_admission_and_acquire_race_never_both_win(self):
        for _ in range(40):
            self.r.flushdb(); barrier = threading.Barrier(2)
            def request():
                barrier.wait(); self.queue(); return self.assign()
            def maintenance():
                barrier.wait(); return m.acquire(self.r, self.resource, self.owner)
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                student, held = pool.submit(request), pool.submit(maintenance)
                assigned, acquired = student.result(), held.result()
            self.assertFalse(acquired and assigned)
            if acquired:
                self.assertEqual(self.r.lrange(self.base + ':queues:1', 0, -1), ['student'])
            else: self.assertEqual(assigned, 'student')
    def test_old_redis_generation_cannot_admit_even_if_markers_were_lost(self):
        self.queue(); self.r.set('lde:running', 'true')
        self.assertIsNone(self.r.eval(ASSIGN, 0, self.resource, '0'*40))
        self.assertEqual(self.r.lrange(self.base + ':queues:1', 0, -1), ['student'])
        self.assertEqual(self.r.eval(ASSIGN, 0, self.resource, self.r.info('server')['run_id']), 'student')
    def test_flushed_running_marker_blocks_recovery_enabled_admission(self):
        self.queue()
        self.assertIsNone(self.r.eval(ASSIGN, 0, self.resource, self.r.info('server')['run_id']))
        self.assertEqual(self.r.lrange(self.base + ':queues:1', 0, -1), ['student'])
    def test_invalid_identifiers_never_write(self):
        for name, owner in [('bad:resource', self.owner), (self.resource, ''), (self.resource, '../owner')]:
            with self.assertRaises(ValueError): m.acquire(self.r, name, owner)
        self.assertEqual(self.r.dbsize(), 0)

if __name__ == '__main__': unittest.main()
