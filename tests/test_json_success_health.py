import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from labdiscoveryengine.configuration.storage import _parse_healthchecks_config
from labdiscoveryengine.data import JsonSuccessHealthcheck, HttpHealthcheck
from labdiscoveryengine.scheduling.asyncio.healthcheck_worker import ResourceHealthchecksWorker
from labdiscoveryengine.scheduling.data import ResourceHealth


class JsonSuccessHealthTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.worker = object.__new__(ResourceHealthchecksWorker)
        self.worker.resource_name = 'board-1'
        self.check = JsonSuccessHealthcheck('controller', 'http://example.invalid/status', timeout=3)

    def test_only_explicit_type_activates_new_health_gate(self):
        checks = _parse_healthchecks_config({'legacy': 'http://example.invalid',
            'controller': {'type': 'json-success', 'url': self.check.url}})
        self.assertIs(type(checks[0]), HttpHealthcheck)
        self.assertIs(type(checks[1]), JsonSuccessHealthcheck)

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
