import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from labdiscoveryengine.scheduling.sync import discovery


class ResourceDiscoveryTest(unittest.TestCase):
    def test_scoped_safe_inventory_and_quarantine(self):
        config=SimpleNamespace(
            laboratories={'lab':SimpleNamespace(resources={'r1','r2'},max_time=300,bypass_resource_health=False)},
            resources={name:SimpleNamespace(features=['mode-a'],url='https://private.invalid',
                api='labdiscoverylib-v1.0',login='private-login',password='private-password') for name in ('r1','r2','other')})
        store=MagicMock()
        store.pipeline.return_value.execute.side_effect=[
            [None,dict(status='healthy',checked_at='date'), 'owner',dict(status='healthy')],
            [dict(status='broken',reconciliation_required='1')]]
        with patch.object(discovery,'lde_config',config),patch.object(discovery,'redis_store',store):
            result=discovery.discover_resources('lab')
        self.assertEqual([r['id'] for r in result['resources']],['r1','r2'])
        self.assertEqual([r['state'] for r in result['resources']],['unknown','unavailable'])
        for secret in ('private.invalid','private-login','private-password','owner','other'):
            self.assertNotIn(secret,json.dumps(result))

    def test_bypass_health_does_not_advertise_safe_selection(self):
        config=SimpleNamespace(laboratories={'lab':SimpleNamespace(resources=set(),max_time=300,bypass_resource_health=True)},resources={})
        store=MagicMock(); store.pipeline.return_value.execute.return_value=[]
        with patch.object(discovery,'lde_config',config),patch.object(discovery,'redis_store',store):
            result=discovery.discover_resources('lab')
        self.assertFalse(result['selection_safe'])
