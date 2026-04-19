import importlib
import unittest
from unittest import mock


class MongoImportTestCase(unittest.TestCase):
    def test_mongodb_module_can_be_reloaded_without_motor(self):
        module = importlib.import_module("labdiscoveryengine.scheduling.asyncio.mongodb")
        real_import = __import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name.startswith("motor"):
                raise ImportError("motor intentionally unavailable in test")
            return real_import(name, globals, locals, fromlist, level)

        with mock.patch("builtins.__import__", side_effect=fake_import):
            reloaded = importlib.reload(module)

        try:
            self.assertTrue(hasattr(reloaded, "initialize_mongodb"))
        finally:
            importlib.reload(reloaded)
