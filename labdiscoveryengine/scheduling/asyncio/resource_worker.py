"""
Methods that should called from the worker (running in asyncio, and using asyncio libraries)
"""

import asyncio
import logging

from typing import Optional
from labdiscoveryengine.data import Resource
from labdiscoveryengine.scheduling.asyncio.processor import ResourceReservationProcessor
from labdiscoveryengine.scheduling.asyncio.redis import initialize_redis, aioredis_store

from labdiscoveryengine.scheduling.keys import ResourceKeys

from labdiscoveryengine.scheduling.asyncio.redis import lua_scripts

logger = logging.getLogger(__name__)

from flask import current_app

from labdiscoveryengine.utils import lde_config

async def initialize_worker():
    """
    Initialize worker
    """
    await initialize_redis()

class ResourceWorker:
    """
    A worker task represents a worker, which handles exclusively a resource of
    a laboratory (or multiple laboratories).

    This means that no other process or thread elsewhere representing or managing
    this resource.

    By default, all the resources are available, which means that the thread of each
    resource is looking for something to do in an endless loop (which can be interrupted).

    If a user comes in, and the reservation is assigned to a resource (it usually is
    assigned to many resources), then the resource coroutine will eventually find it, 
    call the device, start the reservation, and will take care of everything about it. 
    The resource will herefore not be checking other reservations or making other 
    requests to Redis, but just wait for the current reservation to finish.

    Given that reservations are assigned to multiple reservations, much of the process is
    just to reject reservations in other resources.
    """
    def __init__(self, resource_name):
        self.task: Optional[asyncio.Task] = None
        self.resource_name: str = resource_name
        self.resource: Resource = lde_config.resources[resource_name]
        self.minimum_time_between_checks = 10 # seconds

    async def run(self):
        pubsub = aioredis_store.pubsub()

        channel_name = ResourceKeys(self.resource_name).channel()

        logger.info(f"Starting worker for resource {self.resource_name}")
        try:
            await pubsub.subscribe(channel_name)

            # Retrieve existing reservations (e.g., in a restart process)
            await self.process_unfinished_reservation()

            # Process any reservation in case there were reservations before we subscribed...
            await self.process_all_existing_reservations()

            while True:
                message = await pubsub.get_message(timeout=self.minimum_time_between_checks)
                logger.debug(f"got message for {self.resource_name}: {message}")
                # The message does not matter. Whenever there is an event there was a change, and we have to check it.
                await self.process_all_existing_reservations()
            
        except asyncio.CancelledError:
            logger.info(f"Stopping worker for resource {self.resource_name}")
        except Exception as err:
            logger.error(f"Error in worker of {self.resource_name}: {err}", exc_info=True)
        finally:
            await pubsub.unsubscribe(channel_name)

    def running(self):
        return self.task is not None and not self.task.done()

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

    async def process_unfinished_reservation(self):
        """
        Check if a reservation was being processed before (maybe we restarted or similar),
        and take it from where we left it
        """        
        logger.info(f"[{self.resource.identifier}] Searching for an unfinished reservations...")
        # TODO: search for an unfinished reservation and call process_reservation
        reservation_id = await aioredis_store.get(ResourceKeys(self.resource_name).assigned())

    async def process_all_existing_reservations(self):
        """
        Process all existing reservations. We might have missed some reservations and we
        want to make sure we process them all.        
        """
        while True:
            reservation_id = await lua_scripts.assign_reservation_to_resource(self.resource_name)
            logging.info(f"{self.resource_name} - {reservation_id}")
            if reservation_id is None:
                break
            
            logger.info(f"Reservation {reservation_id} assigned to resource {self.resource_name}")

            processor = ResourceReservationProcessor(self.resource, reservation_id)
            
            # Now wait until the process is over
            await processor.process()

