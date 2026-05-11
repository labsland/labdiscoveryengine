import unittest
from types import SimpleNamespace
from unittest import mock

import aiohttp.client_exceptions

from labdiscoveryengine.data import Resource
from labdiscoveryengine.scheduling.asyncio.client import WebLabLibResourceClient
from labdiscoveryengine.scheduling.asyncio.healthcheck_worker import ResourceHealthchecksWorker
from labdiscoveryengine.scheduling.asyncio.processor import ResourceReservationProcessor
from labdiscoveryengine.scheduling.asyncio.redis import aioredis_store
from labdiscoveryengine.scheduling.asyncio.resource_worker import ResourceWorker
from labdiscoveryengine.scheduling.data import ReservationRequest, ResourceHealth


class ResourceWorkerTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_process_unfinished_reservation_ignores_empty_assignment(self):
        worker = object.__new__(ResourceWorker)
        worker.resource_name = "resource-1"
        worker.resource = SimpleNamespace(identifier="resource-1")

        mocked_get = mock.AsyncMock(return_value=None)
        aioredis_store.set_proxied_object(SimpleNamespace(get=mocked_get))

        with mock.patch(
            "labdiscoveryengine.scheduling.asyncio.resource_worker.ResourceReservationProcessor"
        ) as mocked_processor:
            await ResourceWorker.process_unfinished_reservation(worker)

        mocked_get.assert_awaited_once()
        mocked_processor.assert_not_called()
        aioredis_store.set_proxied_object(None)


class WebLabLibResourceClientTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_start_body_includes_client_initial_data(self):
        resource = Resource(
            identifier="resource-1",
            url="https://resource.example",
            login="user",
            password="pass",
            features=[],
            cameras=[],
            healthchecks=[],
            api="weblablib-v1.0",
        )
        reservation_request = ReservationRequest(
            identifier="reservation-1",
            laboratory="boolean-lab",
            features=[],
            resources=["resource-1"],
            user_identifier="external-system",
            user_role="external",
            locale="en",
            max_time=180,
            back_url="https://back.example",
            external_user_identifier="student-1",
            user_full_name="Student One",
            client_initial_data={"board_ui": "Boolean", "user_interface": "simulation:parking"},
        )
        fake_config = SimpleNamespace(
            laboratories={
                "boolean-lab": SimpleNamespace(identifier="booleanfpga", category="FPGA experiments")
            }
        )

        with mock.patch("labdiscoveryengine.scheduling.asyncio.client.lde_config", fake_config):
            client = WebLabLibResourceClient(resource)
            try:
                body = client._get_start_body(reservation_request)
            finally:
                await client.client_session.close()

        self.assertEqual(
            body["client_initial_data"],
            {
                "board_ui": "Boolean",
                "user_interface": "simulation:parking",
                "back": "https://back.example",
                "back_url": "https://back.example",
                "backUrl": "https://back.example",
            },
        )
        self.assertEqual(body["server_initial_data"]["request.locale"], "en")
        self.assertEqual(body["server_initial_data"]["request.username"], "student-1")
        self.assertEqual(body["back"], "https://back.example")

    async def test_start_body_preserves_explicit_client_back_values(self):
        resource = Resource(
            identifier="resource-1",
            url="https://resource.example",
            login="user",
            password="pass",
            features=[],
            cameras=[],
            healthchecks=[],
            api="weblablib-v1.0",
        )
        reservation_request = ReservationRequest(
            identifier="reservation-1",
            laboratory="boolean-lab",
            features=[],
            resources=["resource-1"],
            user_identifier="external-system",
            user_role="external",
            locale="en",
            max_time=180,
            back_url="https://back.example",
            client_initial_data={"back": "https://custom.example/back"},
        )
        fake_config = SimpleNamespace(
            laboratories={
                "boolean-lab": SimpleNamespace(identifier="booleanfpga", category="FPGA experiments")
            }
        )

        with mock.patch("labdiscoveryengine.scheduling.asyncio.client.lde_config", fake_config):
            client = WebLabLibResourceClient(resource)
            try:
                body = client._get_start_body(reservation_request)
            finally:
                await client.client_session.close()

        self.assertEqual(body["client_initial_data"]["back"], "https://custom.example/back")
        self.assertEqual(body["client_initial_data"]["back_url"], "https://back.example")
        self.assertEqual(body["client_initial_data"]["backUrl"], "https://back.example")


class ResourceReservationProcessorTestCase(unittest.IsolatedAsyncioTestCase):
    def build_processor(self):
        resource = Resource(
            identifier="resource-1",
            url="https://resource.example",
            login="user",
            password="pass",
            features=[],
            cameras=[],
            healthchecks=[],
            api="weblablib-v1.0",
        )
        processor = ResourceReservationProcessor(resource, "reservation-1")
        processor.status_poll_retry_delay = 0
        return processor

    async def test_get_should_finish_retries_transient_disconnect(self):
        processor = self.build_processor()
        processor.client = SimpleNamespace(
            get_should_finish=mock.AsyncMock(
                side_effect=[
                    aiohttp.client_exceptions.ServerDisconnectedError(),
                    7,
                ]
            )
        )

        with mock.patch("labdiscoveryengine.scheduling.asyncio.processor.asyncio.sleep", new=mock.AsyncMock()) as sleep:
            result = await processor.get_should_finish_with_retry("session-1")

        self.assertEqual(result, 7)
        self.assertEqual(processor.client.get_should_finish.await_count, 2)
        sleep.assert_awaited_once_with(0)

    async def test_get_should_finish_raises_after_retry_budget(self):
        processor = self.build_processor()
        processor.status_poll_max_attempts = 2
        processor.client = SimpleNamespace(
            get_should_finish=mock.AsyncMock(
                side_effect=aiohttp.client_exceptions.ServerDisconnectedError()
            )
        )

        with mock.patch("labdiscoveryengine.scheduling.asyncio.processor.asyncio.sleep", new=mock.AsyncMock()) as sleep:
            with self.assertRaises(aiohttp.client_exceptions.ServerDisconnectedError):
                await processor.get_should_finish_with_retry("session-1")

        self.assertEqual(processor.client.get_should_finish.await_count, 2)
        sleep.assert_awaited_once_with(0)


class ReservationRequestTestCase(unittest.TestCase):
    def test_roundtrip_preserves_client_initial_data(self):
        original = ReservationRequest(
            identifier="reservation-1",
            laboratory="boolean-lab",
            features=["f1"],
            resources=["resource-1"],
            user_identifier="external-system",
            user_role="external",
            locale="en",
            max_time=180,
            back_url="https://back.example",
            client_initial_data={"board_ui": "Boolean", "showSerial": True},
        )

        restored = ReservationRequest.fromdict(original.todict())

        self.assertEqual(restored.client_initial_data, {"board_ui": "Boolean", "showSerial": True})
        self.assertEqual(restored.identifier, original.identifier)


class ResourceHealthchecksWorkerTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_robotchecker_success_payload_is_healthy(self):
        worker = object.__new__(ResourceHealthchecksWorker)
        worker.resource_name = "resource-1"

        healthcheck = SimpleNamespace(identifier="checker", url="https://checker.example", timeout=10)
        response = mock.AsyncMock()
        response.status = 200
        response.json.return_value = {"found": True, "success": True}
        session = mock.MagicMock()
        session.get.return_value.__aenter__.return_value = response

        with mock.patch("labdiscoveryengine.scheduling.asyncio.healthcheck_worker.aiohttp.ClientSession") as client_session:
            client_session.return_value.__aenter__.return_value = session
            health = await worker._run_robotchecker_healthcheck(healthcheck)

        self.assertEqual(ResourceHealth.states.healthy, health.status)

    async def test_robotchecker_failure_payload_is_broken(self):
        worker = object.__new__(ResourceHealthchecksWorker)
        worker.resource_name = "resource-1"

        healthcheck = SimpleNamespace(identifier="checker", url="https://checker.example", timeout=10)
        response = mock.AsyncMock()
        response.status = 200
        response.json.return_value = {"found": True, "success": False, "message": "no-loop"}
        session = mock.MagicMock()
        session.get.return_value.__aenter__.return_value = response

        with mock.patch("labdiscoveryengine.scheduling.asyncio.healthcheck_worker.aiohttp.ClientSession") as client_session:
            client_session.return_value.__aenter__.return_value = session
            health = await worker._run_robotchecker_healthcheck(healthcheck)

        self.assertEqual(ResourceHealth.states.broken, health.status)
        self.assertEqual("no-loop", health.message)

    async def test_robotchecker_failure_payload_formats_dict_repr_message(self):
        worker = object.__new__(ResourceHealthchecksWorker)
        worker.resource_name = "resource-1"

        healthcheck = SimpleNamespace(identifier="checker", url="https://checker.example", timeout=10)
        response = mock.AsyncMock()
        response.status = 200
        response.json.return_value = {
            "found": True,
            "success": False,
            "message": "{'message': 'The robot has not done a single loop.', 'result': 'error', 'code': 'no-loop'}",
        }
        session = mock.MagicMock()
        session.get.return_value.__aenter__.return_value = response

        with mock.patch("labdiscoveryengine.scheduling.asyncio.healthcheck_worker.aiohttp.ClientSession") as client_session:
            client_session.return_value.__aenter__.return_value = session
            health = await worker._run_robotchecker_healthcheck(healthcheck)

        self.assertEqual(ResourceHealth.states.broken, health.status)
        self.assertEqual("The robot has not done a single loop. (no-loop)", health.message)

    async def test_robotchecker_failure_payload_keeps_plain_message(self):
        worker = object.__new__(ResourceHealthchecksWorker)
        worker.resource_name = "resource-1"

        healthcheck = SimpleNamespace(identifier="checker", url="https://checker.example", timeout=10)
        response = mock.AsyncMock()
        response.status = 200
        response.json.return_value = {
            "found": True,
            "success": False,
            "message": "Expecting value: line 1 column 1 (char 0)",
        }
        session = mock.MagicMock()
        session.get.return_value.__aenter__.return_value = response

        with mock.patch("labdiscoveryengine.scheduling.asyncio.healthcheck_worker.aiohttp.ClientSession") as client_session:
            client_session.return_value.__aenter__.return_value = session
            health = await worker._run_robotchecker_healthcheck(healthcheck)

        self.assertEqual(ResourceHealth.states.broken, health.status)
        self.assertEqual("Expecting value: line 1 column 1 (char 0)", health.message)

    async def test_robotchecker_missing_payload_is_unknown(self):
        worker = object.__new__(ResourceHealthchecksWorker)
        worker.resource_name = "resource-1"

        healthcheck = SimpleNamespace(identifier="checker", url="https://checker.example", timeout=10)
        response = mock.AsyncMock()
        response.status = 200
        response.json.return_value = {"found": False}
        session = mock.MagicMock()
        session.get.return_value.__aenter__.return_value = response

        with mock.patch("labdiscoveryengine.scheduling.asyncio.healthcheck_worker.aiohttp.ClientSession") as client_session:
            client_session.return_value.__aenter__.return_value = session
            health = await worker._run_robotchecker_healthcheck(healthcheck)

        self.assertEqual(ResourceHealth.states.unknown, health.status)
