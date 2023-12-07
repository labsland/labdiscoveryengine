"""
Methods that should be called from the web interface (not running in asyncio, and not using asyncio libraries)
"""

import datetime
import json
import time
from typing import List, Dict, Optional

from flask import Flask
from flask_redis import FlaskRedis

from labdiscoveryengine.scheduling.keys import ReservationKeys, UserKeys
from labdiscoveryengine import mongo

from ..data import ReservationRequest, ReservationStatus
from ..redis_scripts import ScriptNames, SCRIPT_FILES

from labdiscoveryengine.utils import is_mongo_active, lde_config

redis_store = FlaskRedis(decode_responses=True)


class SyncLuaScripts:
    _SCRIPT_INSTANCES = {
        # Name of the script (ScriptName): instance of th escript
    }

    def _run_lua_script(self, script_name: str, keys: List[str] = None, args: List[str] = None):
        """
        Runs a lua script
        """
        if script_name not in self._SCRIPT_INSTANCES:
            raise ValueError(f"Script {script_name} not found")
        
        script_instance = self._SCRIPT_INSTANCES[script_name]

        if keys is None:
            keys = []
        if args is None:
            args = []

        return script_instance(keys=keys, args=args)
    
    def initialize_web_lua_scripts(self):
        """
        Loads all the lua scripts that are needed for the web interface,
        and store their hash in _SCRIPT_HASHES
        """
        for script_name, script_file in SCRIPT_FILES.items():
            with open(script_file, 'r') as f:
                script_content = f.read()
                self._SCRIPT_INSTANCES[script_name] = redis_store.register_script(script_content)

    def store_reservation(self, reservation_request: ReservationRequest):
        """
        Stores a reservation in the redis database in a single transaction
        """
        reservation_id = reservation_request.identifier
        reservation_metadata = json.dumps(reservation_request.todict())
        laboratory = reservation_request.laboratory
        priority = reservation_request.priority
        resources = reservation_request.resources
        user_identifier = reservation_request.user_identifier

        args = [reservation_id, reservation_metadata, laboratory, priority, user_identifier]
        # resources is passed as a list after
        args.extend(resources)

        self._run_lua_script(ScriptNames.store_reservation, args=args)

    def get_reservation_status(self, reservation_id: str) -> ReservationStatus:
        """
        Get the reservation status in an adequate class
        """
        result = self._run_lua_script(ScriptNames.get_reservation_status, args=[reservation_id])
        status, external_session_id, position, url = result
        return ReservationStatus(status=status, reservation_id=reservation_id, external_session_id=external_session_id, position=position, url=url)

sync_lua_scripts = SyncLuaScripts()


def initialize_web(app: Flask):
    """
    Initializes the web interface
    """
    redis_store.init_app(app)

    sync_lua_scripts.initialize_web_lua_scripts()
    

def add_reservation(reservation_request: ReservationRequest) -> ReservationStatus:
    if reservation_request.features:
        resources_to_remove = []
        for resource_name in reservation_request.resources:
            resource = lde_config.resources.get(resource_name)
            if not resource:
                resources_to_remove.append(resource_name)

            for feature in reservation_request.features:
                if feature not in resource.features:
                    resources_to_remove.append(resource_name)
                    break

        for resource_name in resources_to_remove:
            reservation_request.resources.remove(resource_name)

    if is_mongo_active():
        mongo.db.sessions.insert_one({
            "reservation_id": reservation_request.identifier,
            "user": reservation_request.user_identifier,
            "user_role": reservation_request.user_role,
            "group": reservation_request.group,
            "laboratory": reservation_request.laboratory,
            "resources": reservation_request.resources,
            "assigned_resource": None,
            "features": reservation_request.features,
            "priority": reservation_request.priority,
            "start_reservation": datetime.datetime.now(datetime.timezone.utc),
            "start": None,
            "min_end": None,
            "max_end": None,
            "queue_duration": None,
            "min_duration": None,
            "max_duration": None,
            "end_reservation": None,
        })

    sync_lua_scripts.store_reservation(reservation_request)
    return sync_lua_scripts.get_reservation_status(reservation_request.identifier)

def get_reservation_status(username: str, reservation_id: str, previous_reservation_status: Optional[ReservationStatus] = None, max_time: float = 20) -> Optional[ReservationStatus]:
    """
    Get the reservation status. If previous_reservation_status is provided, wait until it is different, waiting at maximum of max_time seconds.
    """
    reservation_identifiers = redis_store.smembers(UserKeys(username).reservations())
    if reservation_id not in reservation_identifiers:
        return None

    t0 = time.time()
    reservation_status: ReservationStatus = sync_lua_scripts.get_reservation_status(reservation_id)

    if not previous_reservation_status or reservation_status.has_changed_from(previous_reservation_status):
        return reservation_status

    # Wait while the reservation status is different. Instead of waits, rely on pubsub channels
    reservation_keys = ReservationKeys(reservation_id)
    with redis_store.pubsub() as pubsub:
        pubsub.subscribe(reservation_keys.channel())

        elapsed = time.time() - t0
        while elapsed < max_time and not reservation_status.has_changed_from(previous_reservation_status):
            pubsub.get_message(timeout=max_time - elapsed)
            reservation_status = sync_lua_scripts.get_reservation_status(reservation_id)
            elapsed = time.time() - t0

    return reservation_status

def cancel_reservation(user_identifier: str, reservation_id: str) -> bool:
    """
    Cancel a reservation.
    """
    reservation_identifiers = redis_store.smembers(UserKeys(user_identifier).reservations())
    if reservation_id not in reservation_identifiers:
        return False

    pipeline = redis_store.pipeline()
    pipeline.hset(ReservationKeys(reservation_id).base(), ReservationKeys.parameters.status, ReservationKeys.states.cancelling)
    pipeline.publish(ReservationKeys(reservation_id).channel(), ReservationKeys.states.cancelling)
    pipeline.execute()
    return True
        

def get_reservation_list(user_identifier: str, user_role: str) -> List[str]:
    pass
