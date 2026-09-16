import base64
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from labdiscoveryengine import create_app
from labdiscoveryengine.scheduling.data import ReservationStatus


class ExternalTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._previous_lde_directory = os.environ.get("LABDISCOVERYENGINE_DIRECTORY")
        os.environ["LABDISCOVERYENGINE_DIRECTORY"] = str(
            Path(__file__).resolve().parents[1] / "deployments" / "simple"
        )
        cls.app = create_app("testing")
        cls.app_context = cls.app.app_context()
        cls.app_context.push()
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.app_context.pop()
        if cls._previous_lde_directory is None:
            os.environ.pop("LABDISCOVERYENGINE_DIRECTORY", None)
        else:
            os.environ["LABDISCOVERYENGINE_DIRECTORY"] = cls._previous_lde_directory

    def _auth_headers(self):
        token = base64.b64encode(b"labsland:password").decode("ascii")
        return {"Authorization": f"Basic {token}"}

    @patch('labdiscoveryengine.views.external.add_reservation')
    def test_server_data_rejected_by_default_before_admission(self, add):
        response = self.client.post('/external/v1/reservations/', headers=self._auth_headers(), json=dict(
            laboratory='dummy', resources=['fpga-1'], userIdentifier='tester', backUrl='https://example.invalid',
            serverInitialData={'example.authorization': 'PRIVATE-TEST-TOKEN'}))
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('PRIVATE-TEST-TOKEN', response.get_data(as_text=True))
        add.assert_not_called()

    @patch('labdiscoveryengine.views.external.add_reservation')
    def test_authorized_server_data_stored_but_not_echoed(self, add):
        from labdiscoveryengine.utils import lde_config
        original_resource = lde_config.resources['fpga-1']
        with patch.dict(lde_config.variables, {'EXTERNAL_SERVER_INITIAL_DATA_KEYS': {'labsland': {'dummy': ['example.authorization']}}}), \
                patch.dict(lde_config.resources, {'fpga-1': original_resource._replace(api='weblablib-v1.0')}):
            add.return_value = ReservationStatus(status='queued', reservation_id='reservation-1', position=0)
            response = self.client.post('/external/v1/reservations/', headers=self._auth_headers(), json=dict(
                laboratory='dummy', resources=['fpga-1'], userIdentifier='tester', backUrl='https://example.invalid',
                serverInitialData={'example.authorization': 'PRIVATE-TEST-TOKEN'}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(add.call_args.kwargs['reservation_request'].server_initial_data, {'example.authorization': 'PRIVATE-TEST-TOKEN'})
        self.assertNotIn('PRIVATE-TEST-TOKEN', response.get_data(as_text=True))

    @patch('labdiscoveryengine.views.external.discover_resources')
    def test_discovery_requires_auth_and_scope(self, discover):
        self.assertEqual(self.client.get('/external/v1/laboratories/dummy/resources').status_code, 401)
        response = self.client.get('/external/v1/laboratories/secret/resources', headers=self._auth_headers())
        self.assertEqual(response.status_code, 404)
        discover.assert_not_called()

    @patch('labdiscoveryengine.views.external.discover_resources')
    def test_discovery_is_private_read_only(self, discover):
        discover.return_value = dict(version=1, resources=[dict(id='fpga-1', state='unknown')])
        with patch('labdiscoveryengine.views.external.add_reservation') as add:
            response = self.client.get('/external/v1/laboratories/dummy/resources', headers=self._auth_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        add.assert_not_called()

    @patch('labdiscoveryengine.views.external.redis_store')
    @patch('labdiscoveryengine.views.external.sync_lua_scripts')
    @patch('labdiscoveryengine.views.external.add_reservation')
    def test_idempotent_post_does_not_admit_twice(self, add, scripts, store):
        add.return_value = ReservationStatus(status='queued', reservation_id='reservation-1', position=0)
        data = dict(laboratory='dummy', resources=['fpga-1'], userIdentifier='tester',
                    backUrl='https://example.invalid', requestId='request-1234567890123')
        store.eval.return_value = 1
        response = self.client.post('/external/v1/reservations/', headers=self._auth_headers(), json=data)
        self.assertEqual(response.status_code, 200)
        store.eval.return_value = 0
        scripts.get_reservation_status.return_value = add.return_value
        response = self.client.post('/external/v1/reservations/', headers=self._auth_headers(), json=data)
        self.assertEqual(response.status_code, 200); add.assert_called_once()
        scripts.get_reservation_status.return_value = ReservationStatus(status=None, reservation_id='uncertain')
        response = self.client.post('/external/v1/reservations/', headers=self._auth_headers(), json=data)
        self.assertEqual(response.status_code, 409); add.assert_called_once()
        store.eval.return_value = -1
        response = self.client.post('/external/v1/reservations/', headers=self._auth_headers(), json=data)
        self.assertEqual(response.status_code, 409); add.assert_called_once()

        store.eval.return_value = -2
        response = self.client.post('/external/v1/reservations/', headers=self._auth_headers(), json=data)
        self.assertEqual(response.status_code, 409); add.assert_called_once()

    @patch("labdiscoveryengine.views.external.add_reservation")
    def test_create_reservation_rejects_unknown_resource(self, add_reservation):
        response = self.client.post(
            "/external/v1/reservations/",
            headers=self._auth_headers(),
            json={
                "laboratory": "dummy",
                "resources": ["unknown-resource"],
                "userIdentifier": "tester",
                "backUrl": "https://example.invalid/back",
            },
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual("invalid-request", response.json["code"])
        self.assertIn("unknown-resource", response.json["message"])
        add_reservation.assert_not_called()

    @patch("labdiscoveryengine.views.external.add_reservation")
    def test_create_reservation_unknown_laboratory_message_includes_name(self, add_reservation):
        response = self.client.post(
            "/external/v1/reservations/",
            headers=self._auth_headers(),
            json={
                "laboratory": "missing-lab",
                "userIdentifier": "tester",
                "backUrl": "https://example.invalid/back",
            },
        )

        self.assertEqual(400, response.status_code)
        self.assertIn("missing-lab", response.json["message"])
        self.assertNotIn("{laboratory}", response.json["message"])
        add_reservation.assert_not_called()

    @patch("labdiscoveryengine.views.external.add_reservation")
    def test_create_reservation_accepts_known_resource(self, add_reservation):
        add_reservation.return_value = ReservationStatus(
            status="queued",
            reservation_id="reservation-1",
            position=0,
        )

        response = self.client.post(
            "/external/v1/reservations/",
            headers=self._auth_headers(),
            json={
                "laboratory": "dummy",
                "resources": ["fpga-1"],
                "userIdentifier": "tester",
                "backUrl": "https://example.invalid/back",
            },
        )

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json["success"])
        add_reservation.assert_called_once()
        reservation_request = add_reservation.call_args.kwargs["reservation_request"]
        self.assertEqual(["fpga-1"], reservation_request.resources)

    @patch("labdiscoveryengine.views.external.add_reservation")
    def test_create_reservation_with_broken_status_returns_checker_message(self, add_reservation):
        add_reservation.return_value = ReservationStatus(
            status="broken",
            reservation_id="reservation-1",
            message="checker says broken",
        )

        response = self.client.post(
            "/external/v1/reservations/",
            headers=self._auth_headers(),
            json={
                "laboratory": "dummy",
                "resources": ["fpga-1"],
                "userIdentifier": "tester",
                "backUrl": "https://example.invalid/back",
            },
        )

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json["success"])
        self.assertEqual("broken", response.json["status"])
        self.assertEqual("checker says broken", response.json["message"])
