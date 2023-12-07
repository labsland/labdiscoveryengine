from typing import List

from flask import current_app
from redis.asyncio.client import Redis

from labdiscoveryengine.scheduling.redis_scripts import ScriptNames, SCRIPT_FILES
from labdiscoveryengine.utils import create_proxied_instance

aioredis_store: Redis = create_proxied_instance(Redis)

async def is_redis_flushed():
    """
    If Redis is flushed (e.g., restarted), we have to restart everything (channels and everything).

    It's not ideal, but if happens we do not want to keep running normally.
    """
    return await aioredis_store.get('lde:running') != 'true'

async def initialize_redis():
    redis_obj = await Redis.from_url(current_app.config['REDIS_URL'], decode_responses=True)
    aioredis_store.set_proxied_object(redis_obj)

    await aioredis_store.set("lde:running", "true")
    await async_lua_scripts.initialize_asyncio_lua_scripts()

class AsyncLuaScripts:
    """
    Here we have the Lua scripts that can be called from asyncio context
    """
    _SCRIPT_INSTANCES = {
        # Name of the script (ScriptName): instance of th escript
    }

    async def _run_lua_script(self, script_name: str, keys: List[str] = None, args: List[str] = None):
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

        return await script_instance(keys=keys, args=args)
    
    async def initialize_asyncio_lua_scripts(self):
        """
        Loads all the lua scripts that are needed for the web interface,
        and store their hash in _SCRIPT_HASHES
        """
        for script_name, script_file in SCRIPT_FILES.items():
            with open(script_file, 'r') as f:
                script_content = f.read()
                self._SCRIPT_INSTANCES[script_name] = aioredis_store.register_script(script_content)

    async def assign_reservation_to_resource(self, resource_name: str):
        """
        Stores a reservation in the redis database in a single transaction
        """
        return await self._run_lua_script(ScriptNames.assign_reservation_to_resource, args=[resource_name])

async_lua_scripts = AsyncLuaScripts()
