import unittest
from types import SimpleNamespace
from unittest import mock

from labdiscoveryengine.data import Resource
from labdiscoveryengine.scheduling.asyncio.client import WebLabLibResourceClient
from labdiscoveryengine.scheduling.asyncio.redis import aioredis_store
from labdiscoveryengine.scheduling.asyncio.resource_worker import ResourceWorker
from labdiscoveryengine.scheduling.data import ReservationRequest


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
            {"board_ui": "Boolean", "user_interface": "simulation:parking"},
        )
        self.assertEqual(body["server_initial_data"]["request.locale"], "en")
        self.assertEqual(body["server_initial_data"]["request.username"], "student-1")
        self.assertEqual(body["back"], "https://back.example")


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
