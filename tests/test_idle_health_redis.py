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
