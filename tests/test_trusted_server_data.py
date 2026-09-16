import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from labdiscoveryengine.scheduling.data import ReservationRequest, ReservationStatus
from labdiscoveryengine.scheduling.trusted_data import validate_server_data, validate_request, TrustedDataError
from labdiscoveryengine.scheduling.asyncio.client import WebLabLibResourceClient


class TrustedServerDataTest(unittest.TestCase):
    def setUp(self):
        self.data = {'example.authorization': {'token': 'PRIVATE-TEST-TOKEN'}}
        self.resource = SimpleNamespace(identifier='r1', api='weblablib-v1.0')
        self.config = SimpleNamespace(
            variables={'EXTERNAL_SERVER_INITIAL_DATA_KEYS': {'provider': {'lab': ['example.authorization']}},
                       'REQUIRED_SERVER_INITIAL_DATA_KEYS': {'lab': ['example.authorization']}},
            external_users={'provider': SimpleNamespace(laboratories=['lab'])},
            laboratories={'lab': SimpleNamespace(identifier='lab', category='labs', resources={'r1'})},
            resources={'r1': self.resource})
        self.request = ReservationRequest(identifier='reservation', laboratory='lab', features=[], resources=['r1'],
            user_identifier='provider', user_role='external', locale='en', max_time=180,
            back_url='https://example.invalid/back', server_initial_data=self.data)

    def validate(self, data=None, **kwargs):
        args = dict(username='provider', laboratory='lab', resources=['r1'], config=self.config)
        args.update(kwargs)
        return validate_server_data(self.data if data is None else data, **args)

    def test_roundtrip_is_private_and_not_status(self):
        restored = ReservationRequest.fromdict(self.request.todict())
        self.assertEqual(restored.server_initial_data, self.data)
        self.assertNotIn('PRIVATE-TEST-TOKEN', json.dumps(ReservationStatus('queued', 'reservation').todict()))
        self.assertIsNot(self.validate(), self.data)

    def test_scoped_account_lab_role_and_adapter(self):
        for changes in [dict(username='unknown'), dict(laboratory='other'), dict(role='admin'),
                        dict(resources=[]), dict(resources=['other'])]:
            with self.subTest(changes=changes), self.assertRaises(TrustedDataError):
                self.validate(**changes)
        self.resource.api = 'labdiscoverylib-v1.0'
        with self.assertRaises(TrustedDataError): self.validate()

    def test_missing_required_data_is_not_ordinary_fallback(self):
        with self.assertRaises(TrustedDataError): self.validate({})
        self.config.variables = {}
        self.assertEqual(self.validate({}), {})
        with self.assertRaises(TrustedDataError): self.validate()

    def test_identity_and_timing_never_overridden_even_if_allowlisted(self):
        self.config.variables['REQUIRED_SERVER_INITIAL_DATA_KEYS'] = {}
        for key in ['reservation_id', 'request.username', 'priority.queue.slot.length', 'lde.identity']:
            self.config.variables['EXTERNAL_SERVER_INITIAL_DATA_KEYS']['provider']['lab'] = [key]
            with self.subTest(key=key), self.assertRaises(TrustedDataError): self.validate({key: 'override'})

    def test_size_depth_structure_and_nonfinite_rejected_without_echo(self):
        nested = 'PRIVATE-TEST-TOKEN'
        for _ in range(10): nested = [nested]
        for value in ['PRIVATE-TEST-TOKEN' * 1000, nested, [0] * 300, float('nan')]:
            with self.subTest(kind=type(value).__name__), self.assertRaises(TrustedDataError) as error:
                self.validate({'example.authorization': value})
            self.assertNotIn('PRIVATE-TEST-TOKEN', str(error.exception))
        with self.assertRaises(TrustedDataError): self.validate(['not-an-object'])

    def test_dispatch_revalidates_permissions_and_physical_resource(self):
        validate_request(self.request, self.resource, self.config)
        with self.assertRaises(TrustedDataError):
            validate_request(self.request, SimpleNamespace(identifier='other'), self.config)
        self.config.variables['EXTERNAL_SERVER_INITIAL_DATA_KEYS'] = {}
        with self.assertRaises(TrustedDataError): validate_request(self.request, self.resource, self.config)

    def test_malformed_policy_is_payload_free_and_fail_closed(self):
        for policy in [dict(REQUIRED_SERVER_INITIAL_DATA_KEYS=None),
                       dict(EXTERNAL_SERVER_INITIAL_DATA_KEYS=[]),
                       dict(EXTERNAL_SERVER_INITIAL_DATA_KEYS={'provider': None})]:
            with self.subTest(policy=policy), self.assertRaises(TrustedDataError):
                self.config.variables = policy
                self.validate()

    def test_adapter_preserves_scheduler_fields_and_separates_client_data(self):
        client = object.__new__(WebLabLibResourceClient)
        client.resource = self.resource
        with patch('labdiscoveryengine.scheduling.asyncio.client.lde_config', self.config):
            body = client._get_start_body(self.request)
        self.assertEqual(body['server_initial_data']['example.authorization'], self.data['example.authorization'])
        self.assertEqual(body['server_initial_data']['reservation_id'], 'reservation')
        self.assertEqual(body['server_initial_data']['priority.queue.slot.length'], 180)
        self.assertNotIn('PRIVATE-TEST-TOKEN', json.dumps(body['client_initial_data']))
