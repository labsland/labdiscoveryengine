import asyncio
from typing import Optional
from labdiscoveryengine.data import Resource

from labdiscoveryengine.utils import lde_config

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

    async def run(self):
        while True:
            try:
                
                # TODO
                self.resource.healthchecks

                # TODO
                self.resource.cameras

                asyncio.sleep(self.minimum_time_between_checks)
            except asyncio.CancelledError:
                break

    async def mark_as_broken(self, error_message: str):
        pass

    async def mark_as_fixed(self):
        pass

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
