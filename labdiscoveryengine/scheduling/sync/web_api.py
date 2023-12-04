"""
Methods that should be called from the web interface (not running in asyncio, and not using asyncio libraries)
"""

import json
from typing import List, Dict

from flask import Flask
from flask_redis import FlaskRedis

from ..data import ReservationRequest

redis_store = FlaskRedis()

# Store all the script names
class ScriptNames:
    store_reservation = 'store_reservation'

# Store all the scripts here
_SCRIPT_FILES: Dict[str, str] = {
    ScriptNames.store_reservation: 'lua/store_reservation.lua'
}

class LuaScripts:
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
        for script_name, script_file in _SCRIPT_FILES.items():
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

        args = [reservation_id, reservation_metadata, laboratory, priority]
        # resources is passed as a list after
        args.extend(resources)

        self._run_lua_script(ScriptNames.store_reservation, args=args)

lua_scripts = LuaScripts()


def initialize_web(app: Flask):
    """
    Initializes the web interface
    """
    redis_store.init_app(app)

    lua_scripts.initialize_web_lua_scripts()
    

def add_reservation(reservation_request: ReservationRequest):
    lua_scripts.store_reservation(reservation_request)

def get_reservation_list(user_identifier: str, user_role: str) -> List[str]:
    pass

