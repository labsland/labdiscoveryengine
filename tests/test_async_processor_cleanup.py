import unittest
import sys
import types
from unittest.mock import patch

# Prefer installed dependencies. Stubs are only for minimal test environments;
# installing them unconditionally poisons later HTTP/client regression modules.
try:
    import motor.motor_asyncio
    import aiohttp.web
    import aiohttp.client_exceptions
except ImportError:
    pass

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

    async def hsetnx(self, key, field, value):
        if field in self.values.get(key, {}):
            return 0
        return await self.hset(key, field, value)

    async def delete(self, key):
        existed = key in self.values
        self.values.pop(key, None)
        return 1 if existed else 0

    async def persist(self, key):
        return int(key in self.values)

    async def eval(self, script, count, *args):
        key, owner = args[0], args[count]
        value = self.values.get(key)
        if value == owner or value == {'reservation_id': owner}:
            if "'persist'" in script:
                return 1
            return await self.delete(key)
        return 0

    def pipeline(self):
        store = self
        class Pipeline:
            def __init__(self): self.calls = []
            def hset(self, *args): self.calls.append(('hset', args)); return self
            def publish(self, *args): self.calls.append(('publish', args)); return self
            async def execute(self):
                return [await getattr(store, method)(*args) for method, args in self.calls]
        return Pipeline()


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
    async def test_missing_owned_metadata_never_releases_without_cleanup(self):
        store=FakeAsyncRedis(); processor=build_processor()
        processor.client=FakeClient([-1])
        owner=processor.resource_keys.assigned(); store.values[owner]=processor.reservation_id
        with patch.object(processor_module,'aioredis_store',store):
            await processor.finish(None,'known-session')
        self.assertIn(owner,store.values)
        self.assertEqual(processor.client.finish_calls,[])
        self.assertEqual(store.values[processor.reservation_keys.base()]['status'],'broken')

    async def test_restart_cancellation_uses_known_session(self):
        import json
        from unittest.mock import AsyncMock
        from tests.test_scheduling_health import _reservation_request
        store=FakeAsyncRedis(); processor=build_processor()
        key=ReservationKeys(processor.reservation_id).base()
        store.values[key]=dict(status='cancelling',session_id='existing-session',
                               start_attempted='1',metadata=json.dumps(_reservation_request(['resource-1']).todict()))
        client=AsyncMock()
        with patch.object(processor_module,'aioredis_store',store), \
             patch.object(processor_module,'is_mongo_active',return_value=False), \
             patch.object(processor,'get_client',return_value=client), \
             patch.object(processor,'cancelled',new_callable=AsyncMock) as cancelled:
            await processor.process()
        self.assertEqual(cancelled.call_args.kwargs['session_id'],'existing-session')

    async def test_old_processor_cannot_release_another_owner(self):
        store=FakeAsyncRedis(); processor=build_processor()
        owner=ResourceKeys(processor.resource.identifier).assigned()
        store.values[owner]='new-reservation'
        with patch.object(processor_module,'aioredis_store',store),patch.object(processor_module,'is_mongo_active',return_value=False):
            await processor.deassign(None)
        self.assertEqual(store.values[owner],'new-reservation')

    async def test_lost_start_response_retains_assignment_and_never_repeats(self):
        store = FakeAsyncRedis(); processor = build_processor()
        from unittest.mock import AsyncMock
        processor.client = type('Client', (), {'start': AsyncMock(side_effect=TimeoutError('lost response'))})()
        owner = ResourceKeys(processor.resource.identifier).assigned()
        key = ReservationKeys(processor.reservation_id).base()
        store.values[owner] = 'reservation-1'
        with patch.object(processor_module, 'aioredis_store', store):
            await processor.initialize_laboratory(None)
        self.assertIn(owner, store.values)
        self.assertEqual(store.values[key]['reconciliation_required'], '1')
        self.assertEqual(store.values[key]['status'], 'broken')
        processor.client.start.assert_awaited_once()

    async def test_stale_pending_cannot_repeat_an_attempted_start(self):
        from unittest.mock import AsyncMock
        store=FakeAsyncRedis();processor=build_processor()
        store.values[processor.reservation_keys.base()]=dict(status='pending',start_attempted='1')
        store.values[processor.resource_keys.assigned()]=processor.reservation_id
        processor.client=type('Client',(),{'start':AsyncMock()})()
        with patch.object(processor_module,'aioredis_store',store):
            await processor.initialize_laboratory(None)
        processor.client.start.assert_not_awaited()
        self.assertEqual(store.values[processor.reservation_keys.base()]['status'],'broken')
        self.assertEqual(store.values[processor.resource_keys.assigned()],processor.reservation_id)

    async def test_cancel_cannot_release_uncertain_start(self):
        store = FakeAsyncRedis(); processor = build_processor()
        owner = ResourceKeys(processor.resource.identifier).assigned()
        key = ReservationKeys(processor.reservation_id).base()
        store.values[owner] = 'reservation-1'
        store.values[key] = dict(status='cancelling', start_attempted='1')
        with patch.object(processor_module, 'aioredis_store', store):
            await processor.finish(None, None)
        self.assertIn(owner, store.values)
        self.assertEqual(store.values[key]['status'], 'broken')

    async def test_malformed_cleanup_retains_assignment(self):
        store = FakeAsyncRedis(); processor = build_processor()
        processor.client = FakeClient(['bad'])
        owner = ResourceKeys(processor.resource.identifier).assigned()
        key = ReservationKeys(processor.reservation_id).base()
        store.values[owner] = 'reservation-1'; store.values[key] = dict(status='ready')
        with patch.object(processor_module, 'aioredis_store', store):
            await processor.finish(None, 'session-1')
        self.assertIn(owner, store.values)
        self.assertEqual(store.values[key]['status'], 'broken')

    async def test_cleanup_should_finish_coercion_handles_bad_values(self):
        with self.assertRaises(ValueError):
            processor_module._coerce_should_finish("bad")

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
