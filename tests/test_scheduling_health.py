import unittest
import json
from types import SimpleNamespace
from unittest import mock

from labdiscoveryengine.scheduling.data import ReservationRequest, ReservationStatus, ResourceHealth
from labdiscoveryengine.scheduling.keys import ReservationKeys
from labdiscoveryengine.scheduling.sync.web_api import add_reservation, cancel_reservation


def _reservation_request(resources):
    return ReservationRequest(
        identifier="reservation-1",
        laboratory="robot-lab",
        features=[],
        resources=list(resources),
        user_identifier="user-1",
        user_role="student",
        locale="en",
        max_time=180,
        back_url="https://back.example",
    )


class FakePipeline:
    def __init__(self):
        self.calls = []

    def hset(self, *args, **kwargs):
        self.calls.append(("hset", args, kwargs))
        return self

    def expire(self, *args, **kwargs):
        self.calls.append(("expire", args, kwargs))
        return self

    def sadd(self, *args, **kwargs):
        self.calls.append(("sadd", args, kwargs))
        return self

    def publish(self, *args, **kwargs):
        self.calls.append(("publish", args, kwargs))
        return self

    def execute(self):
        self.calls.append(("execute", (), {}))


class SchedulingHealthTestCase(unittest.TestCase):
    def _config(self, bypass=False):
        return SimpleNamespace(
            resources={
                "robot-1": SimpleNamespace(features=[]),
                "robot-2": SimpleNamespace(features=[]),
            },
            laboratories={
                "robot-lab": SimpleNamespace(
                    bypass_resource_health=bypass,
                    resources={"robot-1", "robot-2"},
                )
            },
        )

    def test_add_reservation_filters_broken_resources(self):
        statuses = {
            "robot-1": ResourceHealth(resource="robot-1", status=ResourceHealth.states.broken, message="no-loop"),
            "robot-2": ResourceHealth(resource="robot-2", status=ResourceHealth.states.healthy),
        }
        reservation_status = ReservationStatus(status=ReservationKeys.states.queued, reservation_id="reservation-1", position=0)

        with mock.patch("labdiscoveryengine.scheduling.sync.web_api.lde_config", self._config()), \
                mock.patch("labdiscoveryengine.scheduling.sync.web_api.get_resource_health", side_effect=lambda resource: statuses[resource]), \
                mock.patch("labdiscoveryengine.scheduling.sync.web_api.is_mongo_active", return_value=False), \
                mock.patch("labdiscoveryengine.scheduling.sync.web_api.sync_lua_scripts.store_reservation") as store_reservation, \
                mock.patch("labdiscoveryengine.scheduling.sync.web_api.sync_lua_scripts.get_reservation_status", return_value=reservation_status):
            result = add_reservation(_reservation_request(["robot-1", "robot-2"]))

        self.assertEqual(ReservationKeys.states.queued, result.status)
        stored_request = store_reservation.call_args.args[0]
        self.assertEqual(["robot-2"], stored_request.resources)

    def test_add_reservation_returns_broken_when_all_resources_are_broken(self):
        pipeline = FakePipeline()
        statuses = {
            "robot-1": ResourceHealth(resource="robot-1", status=ResourceHealth.states.broken, message="no-loop"),
            "robot-2": ResourceHealth(resource="robot-2", status=ResourceHealth.states.broken, message="no-movement"),
        }

        with mock.patch("labdiscoveryengine.scheduling.sync.web_api.lde_config", self._config()), \
                mock.patch("labdiscoveryengine.scheduling.sync.web_api.get_resource_health", side_effect=lambda resource: statuses[resource]), \
                mock.patch("labdiscoveryengine.scheduling.sync.web_api.is_mongo_active", return_value=False), \
                mock.patch("labdiscoveryengine.scheduling.sync.web_api.redis_store.pipeline", return_value=pipeline, create=True), \
                mock.patch("labdiscoveryengine.scheduling.sync.web_api.sync_lua_scripts.store_reservation") as store_reservation:
            result = add_reservation(_reservation_request(["robot-1", "robot-2"]))

        self.assertEqual(ReservationKeys.states.broken, result.status)
        self.assertIn("no-loop", result.message)
        self.assertIn("no-movement", result.message)
        hset_call = next(call for call in pipeline.calls if call[0] == "hset")
        metadata = json.loads(hset_call[2]["mapping"][ReservationKeys.parameters.metadata])
        self.assertEqual(["robot-1", "robot-2"], metadata["resources"])
        store_reservation.assert_not_called()

    def test_bypass_laboratory_keeps_broken_resources(self):
        statuses = {
            "robot-1": ResourceHealth(resource="robot-1", status=ResourceHealth.states.broken, message="no-loop"),
        }
        reservation_status = ReservationStatus(status=ReservationKeys.states.queued, reservation_id="reservation-1", position=0)

        with mock.patch("labdiscoveryengine.scheduling.sync.web_api.lde_config", self._config(bypass=True)), \
                mock.patch("labdiscoveryengine.scheduling.sync.web_api.get_resource_health", side_effect=lambda resource: statuses[resource]), \
                mock.patch("labdiscoveryengine.scheduling.sync.web_api.is_mongo_active", return_value=False), \
                mock.patch("labdiscoveryengine.scheduling.sync.web_api.sync_lua_scripts.store_reservation") as store_reservation, \
                mock.patch("labdiscoveryengine.scheduling.sync.web_api.sync_lua_scripts.get_reservation_status", return_value=reservation_status):
            result = add_reservation(_reservation_request(["robot-1"]))

        self.assertEqual(ReservationKeys.states.queued, result.status)
        stored_request = store_reservation.call_args.args[0]
        self.assertEqual(["robot-1"], stored_request.resources)

    def test_cancel_reservation_does_not_move_terminal_reservation_to_cancelling(self):
        with mock.patch("labdiscoveryengine.scheduling.sync.web_api.redis_store.smembers", return_value={"reservation-1"}, create=True), \
                mock.patch("labdiscoveryengine.scheduling.sync.web_api.redis_store.hget", return_value=ReservationKeys.states.broken, create=True), \
                mock.patch("labdiscoveryengine.scheduling.sync.web_api.redis_store.pipeline", create=True) as pipeline:
            result = cancel_reservation("user-1", "reservation-1")

        self.assertTrue(result)
        pipeline.assert_not_called()

    def test_cancel_reservation_removes_stale_user_entry(self):
        with mock.patch("labdiscoveryengine.scheduling.sync.web_api.redis_store.smembers", return_value={"reservation-1"}, create=True), \
                mock.patch("labdiscoveryengine.scheduling.sync.web_api.redis_store.hget", return_value=None, create=True), \
                mock.patch("labdiscoveryengine.scheduling.sync.web_api.redis_store.srem", create=True) as srem:
            result = cancel_reservation("user-1", "reservation-1")

        self.assertFalse(result)
        srem.assert_called_once()
