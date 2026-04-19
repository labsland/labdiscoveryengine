import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import redis


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "labdiscoveryengine" / "lua" / "get_reservation_status.lua"
REDIS_SERVER = "/opt/homebrew/bin/redis-server"


def _find_free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ReservationStatusLuaScriptTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.port = _find_free_port()
        cls.process = subprocess.Popen(
            [
                REDIS_SERVER,
                "--port",
                str(cls.port),
                "--save",
                "",
                "--appendonly",
                "no",
                "--dir",
                cls._tmpdir.name,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cls.redis = redis.Redis(host="127.0.0.1", port=cls.port, decode_responses=True)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                if cls.redis.ping():
                    break
            except redis.RedisError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Temporary redis-server did not start for test_reservation_status_script")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "redis"):
            try:
                cls.redis.close()
            except Exception:
                pass
        if hasattr(cls, "process"):
            cls.process.terminate()
            cls.process.wait(timeout=10)
        if hasattr(cls, "_tmpdir"):
            cls._tmpdir.cleanup()

    def setUp(self):
        self.redis.flushdb()
        self.script = self.redis.register_script(SCRIPT_PATH.read_text(encoding="utf-8"))

    def test_pending_status_is_preserved_when_reservation_leaves_queue_before_hash_updates(self):
        reservation_id = "res-1"
        reservation_key = f"lde:reservations:{reservation_id}"

        self.redis.hset(reservation_key, mapping={"status": "pending"})
        self.redis.sadd(f"{reservation_key}:resources", "boolean-s1i3")
        self.redis.zadd("lde:resources:boolean-s1i3:queues:priorities", {"normal": 0})

        status, external_session_id, position, url = self.script(args=[reservation_id])

        self.assertEqual("pending", status)
        self.assertFalse(external_session_id)
        self.assertFalse(position)
        self.assertFalse(url)
