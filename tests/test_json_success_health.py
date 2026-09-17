import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from labdiscoveryengine.configuration.storage import _parse_healthchecks_config
from labdiscoveryengine.data import JsonSuccessHealthcheck, HttpHealthcheck
from labdiscoveryengine.scheduling.asyncio.healthcheck_worker import ResourceHealthchecksWorker
from labdiscoveryengine.scheduling.data import ResourceHealth
from labdiscoveryengine.scheduling.keys import ResourceKeys
from labdiscoveryengine.configuration.exc import InvalidConfigurationValueError
from redis.exceptions import WatchError


class JsonSuccessHealthTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.worker = object.__new__(ResourceHealthchecksWorker)
        self.worker.resource_name = 'board-1'
        self.worker.resource_keys = ResourceKeys('board-1')
        self.check = JsonSuccessHealthcheck('controller', 'http://example.invalid/status', timeout=3)

    def test_only_explicit_type_activates_new_health_gate(self):
        checks = _parse_healthchecks_config({'legacy': 'http://example.invalid',
            'controller': {'type': 'json-success', 'url': self.check.url}})
        self.assertIs(type(checks[0]), HttpHealthcheck)
        self.assertIs(type(checks[1]), JsonSuccessHealthcheck)
        self.assertFalse(checks[1].idle_only)

    def test_idle_only_is_explicit_and_strictly_boolean(self):
        check = _parse_healthchecks_config({'ready': {'type':'json-success', 'url':self.check.url, 'idle_only':True}})[0]
        self.assertTrue(check.idle_only)
        for value in ('false', 0, 1, None):
            with self.assertRaises(InvalidConfigurationValueError):
                _parse_healthchecks_config({'ready': {'type':'json-success', 'url':self.check.url, 'idle_only':value}})

    async def test_idle_only_does_not_probe_owned_or_quarantined_resource(self):
        self.worker.resource = SimpleNamespace(healthchecks=[JsonSuccessHealthcheck('ready',self.check.url,idle_only=True)])
        self.worker._collect_health = AsyncMock()
        pipeline = MagicMock()
        pipeline.watch = AsyncMock()
        pipeline.get = AsyncMock(return_value='existing-owner')
        pipeline.execute = AsyncMock()
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=pipeline)
        with patch('labdiscoveryengine.scheduling.asyncio.healthcheck_worker.aioredis_store',SimpleNamespace(pipeline=factory)):
            await self.worker.check_robotchecker_health()
        self.worker._collect_health.assert_not_called()
        pipeline.execute.assert_not_called()

    async def test_idle_only_publishes_idle_failure_but_discards_racing_ownership(self):
        self.worker.resource = SimpleNamespace(healthchecks=[JsonSuccessHealthcheck('ready',self.check.url,idle_only=True)])
        self.worker._collect_health = AsyncMock(return_value=('broken','real idle failure','configured-checks'))
        for conflict in (False,True):
            pipeline=MagicMock()
            pipeline.watch=AsyncMock()
            pipeline.get=AsyncMock(return_value=None)
            pipeline.execute=AsyncMock(side_effect=WatchError() if conflict else None)
            factory=MagicMock()
            factory.return_value.__aenter__=AsyncMock(return_value=pipeline)
            with patch('labdiscoveryengine.scheduling.asyncio.healthcheck_worker.aioredis_store',SimpleNamespace(pipeline=factory)):
                await self.worker.check_robotchecker_health()
            pipeline.watch.assert_awaited_once_with('lde:resources:board-1:assigned')
            pipeline.execute.assert_awaited_once()
            self.assertEqual(pipeline.hset.call_args.kwargs['mapping']['status'],'broken')

    async def test_requires_exact_success_and_http_200(self):
        for status, payload, healthy in [(200, {'success': True}, True),
                (200, {'success': False}, False), (200, {'success': 1}, False),
                (200, {}, False), (200, [], False), (503, {'success': True}, False)]:
            session = MagicMock()
            response = MagicMock(status=status)
            response.json = AsyncMock(return_value=payload)
            session.get.return_value.__aenter__ = AsyncMock(return_value=response)
            factory = MagicMock()
            factory.return_value.__aenter__ = AsyncMock(return_value=session)
            with self.subTest(status=status, payload=payload), patch(
                    'labdiscoveryengine.scheduling.asyncio.healthcheck_worker.aiohttp.ClientSession', factory):
                result = await self.worker._run_json_success_healthcheck(self.check)
                self.assertEqual(result.status, ResourceHealth.states.healthy if healthy else ResourceHealth.states.broken)
                session.get.assert_called_once_with(self.check.url, allow_redirects=False)

    async def test_timeout_marks_broken_without_echoing_exception(self):
        with patch('labdiscoveryengine.scheduling.asyncio.healthcheck_worker.aiohttp.ClientSession',
                   side_effect=TimeoutError('PRIVATE-DIAGNOSTIC')):
            result = await self.worker._run_json_success_healthcheck(self.check)
        self.assertEqual(result.status, ResourceHealth.states.broken)
        self.assertNotIn('PRIVATE-DIAGNOSTIC', result.message)

    async def test_failed_controller_overrides_healthy_pdls(self):
        self.worker.resource = SimpleNamespace(healthchecks=[self.check, self.check])
        self.worker._run_json_success_healthcheck = AsyncMock(side_effect=[
            ResourceHealth('board-1', ResourceHealth.states.healthy),
            ResourceHealth('board-1', ResourceHealth.states.broken, message='controller')])
        self.worker.mark_as_broken = AsyncMock()
        self.worker.mark_as_fixed = AsyncMock()
        await self.worker.check_robotchecker_health()
        self.worker.mark_as_broken.assert_called_once()
        self.worker.mark_as_fixed.assert_not_called()
