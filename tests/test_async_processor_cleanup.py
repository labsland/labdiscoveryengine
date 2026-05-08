import unittest
import sys
import types
from unittest.mock import patch

motor_module = types.ModuleType("motor")
motor_asyncio_module = types.ModuleType("motor.motor_asyncio")
motor_asyncio_module.AsyncIOMotorClient = object
motor_asyncio_module.AsyncIOMotorDatabase = object
motor_module.motor_asyncio = motor_asyncio_module
sys.modules.setdefault("motor", motor_module)
sys.modules.setdefault("motor.motor_asyncio", motor_asyncio_module)

aiohttp_module = types.ModuleType("aiohttp")
aiohttp_web_module = types.ModuleType("aiohttp.web")
aiohttp_client_exceptions_module = types.ModuleType("aiohttp.client_exceptions")
aiohttp_web_module.HTTPException = Exception
aiohttp_client_exceptions_module.ClientError = Exception
aiohttp_module.web = aiohttp_web_module
aiohttp_module.client_exceptions = aiohttp_client_exceptions_module
aiohttp_module.BasicAuth = lambda login, password: (login, password)
aiohttp_module.ClientSession = object
sys.modules.setdefault("aiohttp", aiohttp_module)
sys.modules.setdefault("aiohttp.web", aiohttp_web_module)
sys.modules.setdefault("aiohttp.client_exceptions", aiohttp_client_exceptions_module)

from labdiscoveryengine.data import Resource
from labdiscoveryengine.scheduling.asyncio import processor as processor_module
from labdiscoveryengine.scheduling.asyncio.processor import ResourceReservationProcessor
from labdiscoveryengine.scheduling.keys import ReservationKeys, ResourceKeys


class FakeAsyncRedis:
    def __init__(self):
        self.values = {}
        self.published = []

    async def hget(self, key, field):
        return self.values.get(key, {}).get(field)

    async def hset(self, key, field, value):
        self.values.setdefault(key, {})[field] = value
        return 1

    async def publish(self, channel, value):
        self.published.append((channel, value))
        return 1

    async def delete(self, key):
        existed = key in self.values
        self.values.pop(key, None)
        return 1 if existed else 0


class FakeClient:
    def __init__(self, finish_values):
        self.finish_values = list(finish_values)
        self.finish_calls = []

    async def finish(self, session_id):
        self.finish_calls.append(session_id)
        if self.finish_values:
            return self.finish_values.pop(0)
        return -1


def build_processor():
    resource = Resource(
        identifier="resource-1",
        url="https://lab.example",
        login="user",
        password="pass",
        features=[],
        cameras=[],
        healthchecks=[],
    )
    return ResourceReservationProcessor(resource, "reservation-1")


class AsyncProcessorCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_should_finish_coercion_handles_bad_values(self):
        self.assertEqual(processor_module._coerce_should_finish("bad"), -1.0)

    async def test_finish_waits_until_remote_cleanup_is_done(self):
        store = FakeAsyncRedis()
        processor = build_processor()
        processor.client = FakeClient([2, -1])
        reservation_key = ReservationKeys(processor.reservation_id).base()
        resource_key = ResourceKeys(processor.resource.identifier).assigned()
        store.values[reservation_key] = {ReservationKeys.parameters.status: ReservationKeys.states.ready}
        store.values[resource_key] = {"reservation_id": processor.reservation_id}
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        with patch.object(processor_module, "aioredis_store", store), \
                patch.object(processor_module, "is_mongo_active", return_value=False), \
                patch.object(processor_module.asyncio, "sleep", side_effect=fake_sleep):
            await processor.finish(reservation_request=None, session_id="session-1")

        self.assertEqual(processor.client.finish_calls, ["session-1", "session-1"])
        self.assertEqual(sleeps, [2.0])
        self.assertEqual(store.values[reservation_key][ReservationKeys.parameters.status], ReservationKeys.states.finished)
        self.assertNotIn(resource_key, store.values)

    async def test_finish_timeout_fails_closed_without_deassigning_resource(self):
        store = FakeAsyncRedis()
        processor = build_processor()
        processor.client = FakeClient([1, 1, 1])
        processor.max_cleanup_finish_attempts = 2
        reservation_key = ReservationKeys(processor.reservation_id).base()
        resource_key = ResourceKeys(processor.resource.identifier).assigned()
        store.values[reservation_key] = {ReservationKeys.parameters.status: ReservationKeys.states.ready}
        store.values[resource_key] = {"reservation_id": processor.reservation_id}

        async def fake_sleep(_seconds):
            return None

        with patch.object(processor_module, "aioredis_store", store), \
                patch.object(processor_module, "is_mongo_active", return_value=False), \
                patch.object(processor_module.asyncio, "sleep", side_effect=fake_sleep):
            await processor.finish(reservation_request=None, session_id="session-1")

        self.assertEqual(store.values[reservation_key][ReservationKeys.parameters.status], ReservationKeys.states.broken)
        self.assertIn(resource_key, store.values)


if __name__ == "__main__":
    unittest.main()
