import unittest
from types import SimpleNamespace
from unittest import mock

from labdiscoveryengine.scheduling.asyncio.redis import aioredis_store
from labdiscoveryengine.scheduling.asyncio.resource_worker import ResourceWorker


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
