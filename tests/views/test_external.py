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
