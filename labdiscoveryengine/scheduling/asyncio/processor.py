import asyncio
import datetime
import json
import logging
import time
from typing import Optional, Tuple

import aiohttp.web
import aiohttp.client_exceptions

from labdiscoveryengine.scheduling.data import ReservationRequest
from labdiscoveryengine.utils import is_mongo_active

logger = logging.getLogger(__name__)

from labdiscoveryengine.data import Resource
from labdiscoveryengine.scheduling.asyncio.client import GenericResourceClient, LabDiscoveryLibResourceClient, WebLabLibResourceClient
from labdiscoveryengine.scheduling.keys import ReservationKeys, ResourceKeys
from labdiscoveryengine.scheduling.asyncio.redis import aioredis_store
from labdiscoveryengine.scheduling.asyncio.mongodb import async_mongo

# TODO: what to do when fail
# TODO: check if the user has cancelled the reservation

class ResourceReservationProcessor:
    """
    A resource reservations processor takes a reservation and tries to process it.

    This means first making sure that it was not started already (if that's the case, process it),
    then 
    """
    def __init__(self, resource: Resource, reservation_id: str):
        self.resource = resource
        self.reservation_id = reservation_id
        self.resource_keys = ResourceKeys(resource.identifier)
        self.reservation_keys = ReservationKeys(reservation_id)
        self.client = None

    def get_client(self) -> GenericResourceClient:
        """
        Returns a client that can be used to start/stop/check the reservation
        """
        return WebLabLibResourceClient(self.resource) if self.resource.api.startswith('weblablib') else LabDiscoveryLibResourceClient(self.resource)
    
    async def fail(self, reservation_request: Optional[ReservationRequest] = None, session_id: Optional[str] = None):
        """
        Mark the reservation as failed
        """
        # TODO: what to do? should we pass it to another resource, if available? How do we know which ones have already been tested?
        # TODO: BUT WATCHOUT: we don't want to deassign if the problem was 
        await self.deassign(reservation_request)
        await aioredis_store.hset(self.reservation_keys.base(), ReservationKeys.parameters.status, ReservationKeys.states.broken)
        await aioredis_store.publish(self.reservation_keys.channel(), ReservationKeys.states.broken)

    async def did_user_cancel(self) -> bool:
        """
        Check if the user requested to cancel the request
        """
        return await aioredis_store.hget(self.reservation_keys.base(), ReservationKeys.parameters.status) == ReservationKeys.states.cancelling

    async def cancelled(self, reservation_request: Optional[ReservationRequest], session_id: Optional[str]):
        """
        Mark the reservation as cancelled and wait until it is deleted on the other side
        """
        return await self.finish(reservation_request=reservation_request, session_id=session_id)

    async def process(self):
        """
        Process the reservations
        """
        # Note: if there is an IO exception we should do something, such as stop the reservation, resend it or whatever.
        # However! No try..finally for deassign just in case it's a valid reason for stopping (e.g., Cancel, etc.)
        #
        try:
            logger.info(f"[{self.resource.identifier}] Starting to process reservation: {self.reservation_id}")
            if is_mongo_active():
                existing_reservation = await async_mongo.sessions.find_one({
                    "reservation_id": self.reservation_id,
                })
                if existing_reservation is not None:
                    record = {
                        "assigned_resource": self.resource.identifier,
                    }
                    if existing_reservation.get('start_reservation'):
                        record['queue_duration'] = (datetime.datetime.utcnow() - existing_reservation['start_reservation']).total_seconds()

                    await async_mongo.sessions.update_one({
                        "reservation_id": self.reservation_id,
                    }, {
                        "$set": record
                    })

            status: Optional[str] = await aioredis_store.hget(self.reservation_keys.base(), ReservationKeys.parameters.status)
            metadata_str: Optional[str] = await aioredis_store.hget(self.reservation_keys.base(), ReservationKeys.parameters.metadata)

            if status is None or metadata_str is None:
                logger.error(f"[{self.resource.identifier}] Error: reservation {self.reservation_id} not found")
                return await self.fail()

            metadata = json.loads(metadata_str)

            reservation_request: ReservationRequest = ReservationRequest.fromdict(metadata)

            self.client: GenericResourceClient = self.get_client()

            session_id: str = None

            async with self.client:
                # Go state by state, in order, and finish it when needed

                if await self.did_user_cancel():
                    return await self.cancelled(reservation_request=reservation_request, session_id=None)

                # If it is queued, then go ahead and initialize it
                if status in (ReservationKeys.states.pending, ReservationKeys.states.queued):

                    initialization_response = await self.initialize_laboratory(reservation_request)
                    if initialization_response is None:
                        return 
                    status, session_id = initialization_response

                if await self.did_user_cancel():
                    return await self.cancelled(reservation_request=reservation_request, session_id=session_id)

                if status == ReservationKeys.states.ready:

                    if session_id is None:
                        session_id = await aioredis_store.hget(self.reservation_keys.base(), ReservationKeys.parameters.session_id)

                    while status == ReservationKeys.states.ready:

                        if await self.did_user_cancel():
                            return await self.cancelled(reservation_request=reservation_request, session_id=session_id)

                        status = await self.wait_for_reservation_being_over(session_id, max_time=10)
                    
                    if status == ReservationKeys.states.cancelling:
                        return await self.cancelled(reservation_request=reservation_request, session_id=session_id)

                if status == ReservationKeys.states.finished:
                    # TODO: what should we do in this case?
                    logger.info(f"[{self.resource.identifier}] Successfully finished reservation {self.reservation_id}")
                    await self.deassign(reservation_request=reservation_request)

                await self.finish(reservation_request, session_id)
        except (aiohttp.web.HTTPException, aiohttp.client_exceptions.ClientError) as err:
            logger.error(f"[{self.resource.identifier}] Error: failed to process reservation {self.reservation_id}: {err}", exc_info=True)
            return await self.fail()

    async def initialize_laboratory(self, reservation_request: ReservationRequest) -> Optional[Tuple[str, str]]:
        """
        Initialize the laboratory, calling LabDiscoveryLib and create a new session

        Return the new status
        """
        # First, let's report that we are initializing
        status = ReservationKeys.states.initializing

        initialization_pipeline = aioredis_store.pipeline()
        initialization_pipeline.hset(self.reservation_keys.base(), ReservationKeys.parameters.status, ReservationKeys.states.initializing)
        # Notify potential clients
        initialization_pipeline.publish(self.reservation_keys.channel(), status)
        await initialization_pipeline.execute()

        try:
            url, session_id = await self.client.start(reservation_request)
        except Exception as err:
            logger.error(f"[{self.resource.identifier}] Error: failed to start reservation {self.reservation_id}: {err}", exc_info=True)
            return await self.fail()

        logger.info(f"[{self.resource.identifier}] Successfully started reservation {self.reservation_id}: url {url} and session id {session_id}")

        if is_mongo_active():
            await async_mongo.sessions.update_one({
                "reservation_id": self.reservation_id,
            }, {
                "$set": {
                    "start": datetime.datetime.now(datetime.timezone.utc),
                }
            })
        
        status = ReservationKeys.states.ready

        initialized_pipeline = aioredis_store.pipeline()
        initialized_pipeline.hset(self.reservation_keys.base(), ReservationKeys.parameters.resource, self.resource.identifier)
        initialized_pipeline.hset(self.reservation_keys.base(), ReservationKeys.parameters.url, url)
        initialized_pipeline.hset(self.reservation_keys.base(), ReservationKeys.parameters.session_id, session_id)
        initialized_pipeline.hset(self.reservation_keys.base(), ReservationKeys.parameters.status, ReservationKeys.states.ready)
        # Notify potential clients
        initialized_pipeline.publish(self.reservation_keys.channel(), status)
        await initialized_pipeline.execute()

        return status, session_id

    async def wait_for_reservation_being_over(self, session_id: str, max_time: float = 30) -> str:
        should_finish: int = await self.client.get_should_finish(session_id)
        # should_finish is either the time left or for next poll or -1 if it finished
        # if it is exactly zero, it might wait forever

        if should_finish < 0:
            return ReservationKeys.states.finishing
        
        if should_finish == 0:
            waiting_time = max_time
        else: # if positive, wait the time left or 
            waiting_time = min(should_finish, max_time)

        # We have to wait for waiting_time. But we might wait shorter if there is activity
        # (e.g., cancellation), so we wait in a different way.
        t0 = time.time()

        async with aioredis_store.pubsub() as pubsub:
            await pubsub.subscribe(self.reservation_keys.channel())
            elapsed = time.time() - t0
            while elapsed <= waiting_time:
                message = await pubsub.get_message(timeout=waiting_time - elapsed)
                elapsed = time.time() - t0
                if await self.did_user_cancel():
                    return ReservationKeys.states.cancelling
        
        return ReservationKeys.states.ready
    
    async def finish(self, reservation_request: Optional[ReservationRequest], session_id: Optional[str]):
        """
        Call the dispose method on the laboratory server and finish
        """
        status: Optional[str] = await aioredis_store.hget(self.reservation_keys.base(), ReservationKeys.parameters.status)
        if status not in (ReservationKeys.states.initializing, ReservationKeys.states.ready, ReservationKeys.states.finishing, ReservationKeys.states.cancelling):
            logger.info(f"[{self.resource.identifier}] Reservation {self.reservation_id} was already finished")
            return await self.deassign(reservation_request)

        if session_id is not None:
            # First, call the dispose method in the laboratory (as much as needed)
            should_finish: float = await self.client.finish(session_id)
            while should_finish > 0:
                asyncio.sleep(should_finish)
                should_finish = await self.client.finish(session_id)

        # Then mark that we are indeed finished
        status = ReservationKeys.states.finished
        await aioredis_store.hset(self.reservation_keys.base(), ReservationKeys.parameters.status, status)
        await aioredis_store.publish(self.reservation_keys.channel(), status)

        # We do not expect anything else about this reservation anymore
        logger.info(f"[{self.resource.identifier}] Reservation {self.reservation_id} finished")

        if is_mongo_active():
            existing_record = await async_mongo.sessions.find_one({
                "reservation_id": self.reservation_id,
            })

            if existing_record is not None:
                now = datetime.datetime.now(datetime.timezone.utc)
                utcnow = datetime.datetime.utcnow()
                record = {
                    "end_reservation": now,
                }
                if existing_record.get("start") is not None:
                    record['min_duration'] = (utcnow - existing_record['start']).total_seconds()
                    record['max_duration'] = (utcnow - existing_record['start']).total_seconds()
                await async_mongo.sessions.update_one({
                    "reservation_id": self.reservation_id,
                }, {
                    "$set": record
                })
        
        await self.deassign(reservation_request)

    async def deassign(self, reservation_request: Optional[ReservationRequest]):
        """
        At resource level, make sure that the laboratory does not have this
        reservation identifier assigned anymore
        """
        await aioredis_store.delete(self.resource_keys.assigned())
        logger.info(f"[{self.resource.identifier}] Reservation {self.reservation_id} deassigned")
