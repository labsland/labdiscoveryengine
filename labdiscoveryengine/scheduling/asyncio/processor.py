import json
import logging
from typing import Optional

from labdiscoveryengine.scheduling.data import ReservationRequest

logger = logging.getLogger(__name__)

from labdiscoveryengine.data import Resource
from labdiscoveryengine.scheduling.asyncio.client import GenericResourceClient, LabDiscoveryLibResourceClient, WebLabLibResourceClient
from labdiscoveryengine.scheduling.keys import ReservationKeys, ResourceKeys

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

    def get_client(self) -> GenericResourceClient:
        """
        Returns a client that can be used to start/stop/check the reservation
        """
        return WebLabLibResourceClient(self.resource) if self.resource.server_api.startswith('weblablib') else LabDiscoveryLibResourceClient(self.resource)
    
    async def fail(self):
        """
        Mark the reservation as failed
        """
        # TODO: what to do? should we pass it to another resource, if available? How do we know which ones have already been tested?
        # TODO: definitely, deassign it from this resource

    async def process(self):
        """
        Process the reservations
        """
        from labdiscoveryengine.scheduling.asyncio.redis import aioredis_store
        
        logger.info(f"[{self.resource.identifier}] Starting to process reservation: {self.reservation_id}")

        status: Optional[str] = await aioredis_store.hget(self.reservation_keys.base(), ReservationKeys.parameters.status)
        metadata_str: Optional[str] = await aioredis_store.hget(self.reservation_keys.base(), ReservationKeys.parameters.metadata)

        if status is None or metadata_str is None:
            logger.error(f"[{self.resource.identifier}] Error: reservation {self.reservation_id} not found")
            return

        metadata = json.loads(metadata_str)

        reservation_request: ReservationRequest = ReservationRequest.fromdict(metadata)

        client: GenericResourceClient = self.get_client()

        async with client:
            # Go state by state, in order, and finish it when needed

            # If it is queued, then go ahead and initialize it
            if status in (ReservationKeys.states.pending, ReservationKeys.states.queued):

                # First, let's initialize it
                await aioredis_store.hset(self.reservation_keys.base(), ReservationKeys.parameters.status, ReservationKeys.states.initializing)

                try:
                    url, session_id = await client.start(reservation_request)
                except Exception as err:
                    pass
                    # TODO
                    return
                else:
                    pass

