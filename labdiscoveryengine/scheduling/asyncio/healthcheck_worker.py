import asyncio
import ast
import datetime
import logging
from typing import Optional
import aiohttp
from redis.exceptions import WatchError

from labdiscoveryengine.data import Resource, RobotcheckerHealthcheck, JsonSuccessHealthcheck
from labdiscoveryengine.scheduling.data import ResourceHealth
from labdiscoveryengine.scheduling.asyncio.redis import aioredis_store
from labdiscoveryengine.scheduling.keys import ResourceKeys

from labdiscoveryengine.utils import lde_config

logger = logging.getLogger(__name__)


def format_robotchecker_message(message: Optional[str]) -> Optional[str]:
    if not message:
        return None

    if not isinstance(message, str):
        return str(message)

    stripped_message = message.strip()
    try:
        parsed_message = ast.literal_eval(stripped_message)
    except Exception:
        return message

    if not isinstance(parsed_message, dict):
        return message

    detail = parsed_message.get("message") or parsed_message.get("error")
    code = parsed_message.get("code")
    if detail and code:
        return f"{detail} ({code})"
    if detail:
        return str(detail)
    if code:
        return str(code)
    return message


class ResourceHealthchecksWorker:
    """
    A healthcheck worker task represents a worker, which handles exclusively the healthchecks
    of a resource of a laboratory (or multiple laboratories).

    This means that no other process or thread elsewhere representing or managing
    this resource.
    """
    def __init__(self, resource_name):
        self.task: Optional[asyncio.Task] = None
        self.resource_name: str = resource_name
        self.resource: Resource = lde_config.resources[resource_name]
        self.minimum_time_between_checks = 30 # seconds
        self.resource_keys = ResourceKeys(resource_name)

    async def run(self):
        while True:
            try:
                await self.check_robotchecker_health()
                await asyncio.sleep(self.minimum_time_between_checks)
            except asyncio.CancelledError:
                break
            except Exception as err:
                logger.error(f"Error checking health of resource {self.resource_name}: {err}", exc_info=True)
                await self.mark_as_unknown(str(err), source="healthcheck-worker")
                await asyncio.sleep(self.minimum_time_between_checks)

    async def check_robotchecker_health(self):
        # Some readiness endpoints deliberately fail during an owned session's
        # cleanup. Opt-in deployments must not cache that transient failure as
        # idle hardware health. WATCH detects even an acquire/release cycle that
        # occurs entirely during the HTTP checks; a before/after GET cannot.
        idle_only = any(isinstance(check, JsonSuccessHealthcheck) and check.idle_only
                        for check in self.resource.healthchecks)
        if idle_only:
            async with aioredis_store.pipeline() as pipeline:
                try:
                    await pipeline.watch(self.resource_keys.assigned())
                    if await pipeline.get(self.resource_keys.assigned()):
                        return  # Keep the old timestamp; do not pretend it was checked.
                    status, message, source = await self._collect_health()
                    pipeline.multi()
                    pipeline.hset(self.resource_keys.health(), mapping=self._health_mapping(status, message, source))
                    await pipeline.execute()
                except WatchError:
                    return  # Owned or changed while probing; discard the sample.
            return
        status, message, source = await self._collect_health()
        if status == ResourceHealth.states.broken:
            await self.mark_as_broken(message, source=source)
        elif status == ResourceHealth.states.healthy:
            await self.mark_as_fixed(source=source)
        else:
            await self.mark_as_unknown(message, source=source)

    async def _collect_health(self):
        robotchecker_healthchecks = [
            healthcheck
            for healthcheck in self.resource.healthchecks
            if isinstance(healthcheck, (RobotcheckerHealthcheck, JsonSuccessHealthcheck))
        ]

        if not robotchecker_healthchecks:
            return ResourceHealth.states.unknown, None, 'no-robotchecker'

        states = []
        source = ('configured-checks' if any(isinstance(check, JsonSuccessHealthcheck)
                  for check in robotchecker_healthchecks) else 'robotchecker')
        for healthcheck in robotchecker_healthchecks:
            if isinstance(healthcheck, JsonSuccessHealthcheck):
                states.append(await self._run_json_success_healthcheck(healthcheck))
            else:
                states.append(await self._run_robotchecker_healthcheck(healthcheck))

        broken_states = [state for state in states if state.status == ResourceHealth.states.broken]
        if broken_states:
            message = "; ".join(
                state.message or "checker reported the resource as broken"
                for state in broken_states
            )
            return ResourceHealth.states.broken, message, source

        if any(state.status == ResourceHealth.states.healthy for state in states):
            return ResourceHealth.states.healthy, None, source

        messages = [state.message for state in states if state.message]
        return ResourceHealth.states.unknown, "; ".join(messages) or None, source

    async def _run_json_success_healthcheck(self, healthcheck):
        # Explicit opt-in: do not reinterpret existing generic HTTP camera/debug
        # links as scheduler gates. Only passive JSON status endpoints belong here.
        healthy = False
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=healthcheck.timeout)) as session:
                async with session.get(healthcheck.url, allow_redirects=False) as response:
                    if response.status == 200:
                        payload = await response.json()
                        healthy = isinstance(payload, dict) and payload.get('success') is True
        except Exception:
            pass
        return ResourceHealth(resource=self.resource_name,
            status=ResourceHealth.states.healthy if healthy else ResourceHealth.states.broken,
            message=None if healthy else f'{healthcheck.identifier}: success was not confirmed',
            source='json-success')

    async def _run_robotchecker_healthcheck(self, healthcheck: RobotcheckerHealthcheck) -> ResourceHealth:
        timeout = aiohttp.ClientTimeout(total=healthcheck.timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(healthcheck.url) as response:
                    if response.status != 200:
                        return ResourceHealth(
                            resource=self.resource_name,
                            status=ResourceHealth.states.unknown,
                            message=f"{healthcheck.identifier}: HTTP {response.status}",
                            source="robotchecker",
                        )
                    payload = await response.json()
        except Exception as err:
            return ResourceHealth(
                resource=self.resource_name,
                status=ResourceHealth.states.unknown,
                message=f"{healthcheck.identifier}: {err}",
                source="robotchecker",
            )

        if payload.get("found") is not True:
            return ResourceHealth(
                resource=self.resource_name,
                status=ResourceHealth.states.unknown,
                message=f"{healthcheck.identifier}: checker status not found",
                source="robotchecker",
            )

        if payload.get("success") is True:
            return ResourceHealth(
                resource=self.resource_name,
                status=ResourceHealth.states.healthy,
                source="robotchecker",
            )

        return ResourceHealth(
            resource=self.resource_name,
            status=ResourceHealth.states.broken,
            message=format_robotchecker_message(payload.get("message")) or f"{healthcheck.identifier}: checker reported failure",
            source="robotchecker",
        )

    async def mark_as_broken(self, error_message: str, source: str = "healthcheck"):
        await self._write_health(ResourceHealth.states.broken, error_message, source=source)

    async def mark_as_fixed(self, source: str = "healthcheck"):
        await self._write_health(ResourceHealth.states.healthy, None, source=source)

    async def mark_as_unknown(self, message: Optional[str] = None, source: str = "healthcheck"):
        await self._write_health(ResourceHealth.states.unknown, message, source=source)

    async def _write_health(self, status: str, message: Optional[str], source: str):
        await aioredis_store.hset(self.resource_keys.health(), mapping=self._health_mapping(status, message, source))

    @staticmethod
    def _health_mapping(status, message, source):
        return {
            "status": status,
            "message": message or "",
            "source": source,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    async def start(self):
        if self.task is not None:
            self.task.cancel()
            await self.task
        self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            await self.task
            self.task = None
