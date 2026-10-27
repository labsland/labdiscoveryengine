from typing import List
import os

from flask import current_app
from redis.asyncio.client import Redis

from labdiscoveryengine.scheduling.redis_scripts import ScriptNames, SCRIPT_FILES
from labdiscoveryengine.utils import create_proxied_instance

aioredis_store: Redis = create_proxied_instance(Redis)
maintenance_run_id = ''
maintenance_resources = set()
maintenance_state_dir = None

async def is_redis_flushed():
    """
    Detect a missing worker marker; this is NOT proof that hardware is idle.

    Production assumes durable Redis and operator-gated recovery after state
    loss (docs/source/redis_durability.rst). Reinitializing channels/this marker
    cannot reconstruct ownership or authorize new reservations by itself.
    """
    return (await aioredis_store.get('lde:running') != 'true' or
            bool(maintenance_run_id and (await aioredis_store.info('server'))['run_id'] != maintenance_run_id))

async def initialize_redis():
    global maintenance_run_id, maintenance_resources, maintenance_state_dir
    redis_obj = await Redis.from_url(current_app.config['REDIS_URL'], decode_responses=True)
    aioredis_store.set_proxied_object(redis_obj)

    maintenance_run_id = ''
    maintenance_resources = set()
    maintenance_state_dir = None
    folder = current_app.config.get('SERIAL_RECOVERY_STATE_DIR')
    if folder:
        from labdiscoveryengine.scheduling.serial_recovery import read_pending_journals, read_probe_journals
        from labdiscoveryengine.scheduling.maintenance import _validate
        from labdiscoveryengine.utils import lde_config
        resources = current_app.config.get('SERIAL_RECOVERY_RESOURCES', '').split(',')
        for resource in resources:
            _validate(resource, '0' * 32)
            if resource not in lde_config.resources:
                raise ValueError('Serial recovery requires an exact configured resource')
        run_id = (await redis_obj.info('server'))['run_id']
        for state in read_probe_journals(folder):
            if state['resource'] not in resources:
                raise RuntimeError('Probe history removed from configuration; reconcile first')
            key = 'lde:resources:' + state['resource'] + ':health-probe'
            if state['phase'] == 'running':
                restored = await redis_obj.eval("local old=redis.call('get',KEYS[1]); "
                    "if old and old ~= ARGV[1] then return 0 end "
                    "redis.call('set',KEYS[1],ARGV[1]); return 1",1,key,state['owner'])
                if restored != 1: raise RuntimeError('Conflicting probe ownership')
            else:
                await redis_obj.eval("if redis.call('get',KEYS[1]) == ARGV[1] then "
                    "return redis.call('del',KEYS[1]) end return 0",1,key,state['owner'])
        for state in read_pending_journals(folder):
            if state['resource'] not in resources:
                raise RuntimeError('Pending maintenance removed from configuration; reconcile first')
            key = 'lde:resources:' + state['resource'] + ':maintenance'
            # Never replace another maintenance owner or modify reservations.
            restored = await redis_obj.eval("local old=redis.call('get',KEYS[1]); "
                "if old and old ~= ARGV[1] then return 0 end "
                "redis.call('set',KEYS[1],ARGV[1]); return 1", 1, key, state['owner'])
            if restored != 1:
                raise RuntimeError('Conflicting maintenance journal; reconciliation required')
        if (await redis_obj.info('server'))['run_id'] != run_id:
            raise RuntimeError('Redis restarted during maintenance reconciliation')
        maintenance_run_id = run_id
        maintenance_resources = set(resources)
        maintenance_state_dir = folder

    recovery_file = current_app.config.get('LAB_RECOVERY_STATE_FILE')
    if os.environ.get('LAB_RECOVERY_MANAGED') == '1' and not recovery_file:
        raise RuntimeError('Managed scheduler requires a recovery receipt')
    if recovery_file:
        import json
        from pathlib import Path
        from labdiscoveryengine.recovery.state import trusted_read
        state = trusted_read(recovery_file)
        run_id = (await redis_obj.info('server'))['run_id']
        if state.get('phase') != 'managed' or state['generation']['stores']['scheduler'] != run_id:
            raise RuntimeError('Laboratory recovery has not fenced this Redis generation')
        maintenance_run_id = run_id
    await aioredis_store.set("lde:running", "true")
    await async_lua_scripts.initialize_asyncio_lua_scripts()
    if folder and maintenance_run_id:
        await redis_obj.set("lde:serial-recovery-resources", ','.join(sorted(maintenance_resources)))
        await redis_obj.set("lde:serial-recovery-protocol", "cdc-v1:" + maintenance_run_id)

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
        epoch = ''
        recovery_file = current_app.config.get('LAB_RECOVERY_STATE_FILE')
        if recovery_file:
            import json
            from pathlib import Path
            from labdiscoveryengine.recovery.state import trusted_read
            state = trusted_read(recovery_file)
            item = state.get('resources', {}).get(resource_name, {})
            if state.get('phase') != 'managed' or item.get('phase') != 'ready':
                return None
            epoch = item['epoch']
        return await self._run_lua_script(ScriptNames.assign_reservation_to_resource,
                                         args=[resource_name, maintenance_run_id, epoch])

async_lua_scripts = AsyncLuaScripts()
