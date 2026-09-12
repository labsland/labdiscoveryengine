import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock
import aiohttp
from labdiscoveryengine.scheduling.asyncio.client import WebLabLibResourceClient

class Response:
    def __init__(self, value): self.value = value
    async def __aenter__(self):
        if isinstance(self.value, Exception): raise self.value
        return self
    async def __aexit__(self, *args): pass
    async def json(self): return self.value

class ResourcePollDisconnectTest(unittest.IsolatedAsyncioTestCase):
    def client(self, responses):
        client = object.__new__(WebLabLibResourceClient)
        client.resource = SimpleNamespace(url="https://lab.example/resource")
        client.client_session = MagicMock()
        client.client_session.get.side_effect = [Response(x) for x in responses]
        return client
    async def test_idle_disconnect_retries_status_without_recreating_session(self):
        client = self.client([aiohttp.ServerDisconnectedError(), {"should_finish": 10}])
        self.assertEqual(await client.get_should_finish("existing"), 10)
        self.assertEqual(client.client_session.get.call_count, 2)
        client.client_session.post.assert_not_called()
    async def test_repeated_disconnect_is_bounded(self):
        client = self.client([aiohttp.ServerDisconnectedError(), aiohttp.ServerDisconnectedError()])
        with self.assertRaises(aiohttp.ServerDisconnectedError):
            await client.get_should_finish("existing")
        self.assertEqual(client.client_session.get.call_count, 2)
    async def test_finished_response_keeps_negative_result(self):
        client = self.client([{"should_finish": -1}])
        self.assertEqual(await client.get_should_finish("existing"), -1)
        self.assertEqual(client.client_session.get.call_count, 1)
