import asyncio
import datetime
import json
import logging
import time
import math
from typing import Optional, Tuple

import aiohttp.web
import aiohttp.client_exceptions

from labdiscoveryengine.scheduling.data import ReservationRequest
from flask import current_app, has_app_context
from labdiscoveryengine.recovery.ownership import OwnershipLost, TRANSITION
from labdiscoveryengine.utils import is_mongo_active

logger = logging.getLogger(__name__)

from labdiscoveryengine.data import Resource
from labdiscoveryengine.scheduling.asyncio.client import AbstractResourceClient, LabDiscoveryLibResourceClient, WebLabLibResourceClient
from labdiscoveryengine.scheduling.keys import ReservationKeys, ResourceKeys
from labdiscoveryengine.scheduling.asyncio.redis import aioredis_store
from labdiscoveryengine.scheduling.asyncio.mongodb import async_mongo

# TODO: what to do when fail
# TODO: check if the user has cancelled the reservation


def _coerce_should_finish(value) -> float:
    result = float(value)
    if not math.isfinite(result) or isinstance(value, bool):
        raise ValueError('Invalid cleanup response')
    return result

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
        self.recovery_managed = bool(has_app_context() and current_app.config.get("LAB_RECOVERY_STATE_FILE"))
        self.recovery_epoch = None
        self.max_cleanup_finish_attempts = 60
        self.max_cleanup_finish_sleep = 30
        self.status_poll_max_attempts = 3
        self.status_poll_retry_delay = 1.0

    async def ownership(self, action='check', **fields):
        """Compare and mutate in one Redis operation; a stale processor exits."""
        if not self.recovery_managed:
            return True
        from labdiscoveryengine.scheduling.asyncio import redis as scheduler_redis
        args = [scheduler_redis.maintenance_run_id, self.recovery_epoch or '',
                self.reservation_id, action]
        for key, value in fields.items():
            args.extend([key, value])
        result = await aioredis_store.eval(TRANSITION, 3, self.resource_keys.base(),
            self.reservation_keys.base(), self.reservation_keys.channel(), *args)
        if result == 0:
            raise OwnershipLost()
        return result == 1

    async def transition(self, status, **fields):
        if self.recovery_managed:
            await self.ownership(status=status, **fields)
        elif not fields:
            await aioredis_store.hset(self.reservation_keys.base(), 'status', status)
            await aioredis_store.publish(self.reservation_keys.channel(), status)
        else:
            pipeline = aioredis_store.pipeline()
            for key, value in dict(fields, status=status).items():
                pipeline.hset(self.reservation_keys.base(), key, value)
            pipeline.publish(self.reservation_keys.channel(), status)
            await pipeline.execute()

    def get_client(self) -> AbstractResourceClient:
        """
        Returns a client that can be used to start/stop/check the reservation
        """
        cls = WebLabLibResourceClient if self.resource.api.startswith('weblablib') else LabDiscoveryLibResourceClient
        return cls(self.resource, recovery_epoch=self.recovery_epoch)
    
    async def fail(self, reservation_request: Optional[ReservationRequest] = None, session_id: Optional[str] = None):
        """
        Mark the reservation as failed
        """
        # TODO: what to do? should we pass it to another resource, if available? How do we know which ones have already been tested?
        # TODO: BUT WATCHOUT: we don't want to deassign if the problem was 
        if self.recovery_managed:
            await self.transition(ReservationKeys.states.broken)
            return await self.deassign(reservation_request)
        await self.deassign(reservation_request)
        await aioredis_store.hset(self.reservation_keys.base(), ReservationKeys.parameters.status, ReservationKeys.states.broken)
        await aioredis_store.publish(self.reservation_keys.channel(), ReservationKeys.states.broken)

    async def fail_closed(self, reason: str):
        """
        Mark this reservation as broken without releasing the physical resource.

        This is used after an uncertain cleanup. Releasing the resource here
        could expose hardware that may still be restoring or running user code.
        """
        if self.recovery_managed:
            await self.ownership('quarantine', reconciliation_required='1', status='broken')
            return
        logger.error(f"[{self.resource.identifier}] Reservation {self.reservation_id} failed closed: {reason}")
        # Also preserve an ownership marker created by an older worker that
        # still used an expiry. Never extend another reservation's marker.
        await aioredis_store.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('persist', KEYS[1]) end return 0",
            1, self.resource_keys.assigned(), self.reservation_id)
        await aioredis_store.hset(self.reservation_keys.base(), 'reconciliation_required', '1')
        await aioredis_store.hset(self.reservation_keys.base(), ReservationKeys.parameters.status, ReservationKeys.states.broken)
        await aioredis_store.persist(self.reservation_keys.base())
        await aioredis_store.persist('lde:external-request:' + self.reservation_id)
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

    async def deadline_remaining(self, field):
        if await aioredis_store.hget(self.reservation_keys.base(), 'deadline_expired'):
            return 0
        value = await aioredis_store.hget(self.reservation_keys.base(), field)
        if value is None:
            return None  # Pre-upgrade records get a bound when processing resumes.
        try:
            deadline = float(value)
            if not math.isfinite(deadline):
                return 0
        except (TypeError, ValueError, OverflowError):
            return 0
        seconds, micros = await aioredis_store.time()
        return max(0, deadline - (seconds + micros / 1000000))

    async def set_deadline_once(self, field, duration):
        if isinstance(duration, bool) or not math.isfinite(float(duration)) or float(duration) <= 0:
            raise ValueError('Invalid reservation duration')
        seconds, micros = await aioredis_store.time()
        await aioredis_store.hsetnx(self.reservation_keys.base(), field,
                                   seconds + micros / 1000000 + float(duration))

    async def expire_session(self, field):
        await self.ownership()
        await aioredis_store.hsetnx(self.reservation_keys.base(), 'deadline_expired', field)
        await aioredis_store.publish(self.reservation_keys.channel(), 'deadline-expired')

    async def process(self):
        """
        Process the reservations
        """
        # Note: if there is an IO exception we should do something, such as stop the reservation, resend it or whatever.
        # However! No try..finally for deassign just in case it's a valid reason for stopping (e.g., Cancel, etc.)
        #
        try:
            if self.recovery_managed:
                self.recovery_epoch = await aioredis_store.hget(self.reservation_keys.base(), 'recovery_epoch')
                await self.ownership()
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
                return await self.fail_closed("Missing reservation metadata; ownership needs reconciliation")

            metadata = json.loads(metadata_str)

            reservation_request: ReservationRequest = ReservationRequest.fromdict(metadata)

            await self.ownership()
            # Legacy records receive a one-time bound, never renewed on restart.
            timeout = current_app.config.get('RESERVATION_INITIALIZATION_TIMEOUT', 300) if has_app_context() else 300
            try:
                await self.set_deadline_once('initialization_deadline', timeout)
                if status == ReservationKeys.states.ready:
                    await self.set_deadline_once('session_deadline', reservation_request.max_time)
            except (ValueError, TypeError, OverflowError):
                return await self.fail_closed('Invalid reservation deadline')
            self.client: AbstractResourceClient = self.get_client()

            session_id = await aioredis_store.hget(self.reservation_keys.base(), ReservationKeys.parameters.session_id)

            async with self.client:
                # A restart during POST /start cannot prove whether the lab started.
                # Retain the assignment; never repeat start or dispose an unknown session.
                if (status in (ReservationKeys.states.initializing, ReservationKeys.states.broken)
                        or await aioredis_store.hget(self.reservation_keys.base(), 'reconciliation_required')):
                    return await self.fail_closed("Uncertain initialization or previous quarantine")
                # Go state by state, in order, and finish it when needed

                if await self.did_user_cancel():
                    return await self.cancelled(reservation_request=reservation_request, session_id=session_id)

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
                        await self.ownership()

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
        except OwnershipLost:
            logger.info("[%s] Interrupted reservation %s retired", self.resource.identifier, self.reservation_id)
            return
        except (aiohttp.web.HTTPException, aiohttp.client_exceptions.ClientError) as err:
            logger.error(f"[{self.resource.identifier}] Error: failed to process reservation {self.reservation_id}: {err}", exc_info=True)
            try:
                return await self.fail_closed("Transport failure while processing the laboratory")
            except OwnershipLost:
                return

    async def initialize_laboratory(self, reservation_request: ReservationRequest) -> Optional[Tuple[str, str]]:
        """
        Initialize the laboratory, calling LabDiscoveryLib and create a new session

        Return the new status
        """
        # Claim before any external start. A stale/replayed pending state must
        # never restart a session whose outcome was already attempted.
        if self.recovery_managed:
            claimed = await self.ownership('claim', status=ReservationKeys.states.initializing)
        else:
            claimed = await aioredis_store.hsetnx(self.reservation_keys.base(), 'start_attempted', '1')
            if claimed:
                await self.transition(ReservationKeys.states.initializing)
        if not claimed:
            return await self.fail_closed('Laboratory start was already attempted')
        await self.ownership()

        remaining = await self.deadline_remaining('initialization_deadline')
        if remaining is not None and remaining <= 0:
            await self.expire_session('initialization_deadline')
            return await self.fail_closed('Laboratory initialization deadline elapsed')
        try:
            await self.set_deadline_once('session_deadline', reservation_request.max_time)
            await self.ownership()
            remaining = await self.deadline_remaining('initialization_deadline')
            url, session_id = await asyncio.wait_for(self.client.start(reservation_request), timeout=remaining)
        except Exception as err:
            logger.error(f"[{self.resource.identifier}] Error: failed to start reservation {self.reservation_id}: {err}", exc_info=True)
            return await self.fail_closed("Laboratory start outcome is uncertain")

        logger.info(f"[{self.resource.identifier}] Successfully started reservation {self.reservation_id}: url {url} and session id {session_id}")

        if is_mongo_active():
            await async_mongo.sessions.update_one({
                "reservation_id": self.reservation_id,
            }, {
                "$set": {
                    "start": datetime.datetime.now(datetime.timezone.utc),
                }
            })
        
        remaining = await self.deadline_remaining('initialization_deadline')
        if remaining is not None and remaining <= 0:
            await self.expire_session('initialization_deadline')
        status = ReservationKeys.states.ready

        await self.transition(status, resource=self.resource.identifier, url=url, session_id=session_id)

        return status, session_id

    async def wait_for_reservation_being_over(self, session_id: str, max_time: float = 30) -> str:
        await self.ownership()
        remaining = await self.deadline_remaining('session_deadline')
        if remaining is not None and remaining <= 0:
            await self.expire_session('session_deadline')
            return ReservationKeys.states.finishing
        try:
            should_finish: int = await asyncio.wait_for(
                self.get_should_finish_with_retry(session_id), timeout=remaining)
        except asyncio.TimeoutError:
            await self.expire_session('session_deadline')
            return ReservationKeys.states.finishing
        # should_finish is either the time left or for next poll or -1 if it finished
        # if it is exactly zero, it might wait forever

        if should_finish < 0:
            return ReservationKeys.states.finishing
        
        if should_finish == 0:
            waiting_time = max_time
        else: # if positive, wait the time left or 
            waiting_time = min(should_finish, max_time)

        remaining = await self.deadline_remaining('session_deadline')
        if remaining is not None:
            waiting_time = min(waiting_time, remaining)

        # We have to wait for waiting_time. But we might wait shorter if there is activity
        # (e.g., cancellation), so we wait in a different way.
        t0 = time.monotonic()

        async with aioredis_store.pubsub() as pubsub:
            await pubsub.subscribe(self.reservation_keys.channel())
            elapsed = time.monotonic() - t0
            while elapsed <= waiting_time:
                message = await pubsub.get_message(timeout=waiting_time - elapsed)
                elapsed = time.monotonic() - t0
                await self.ownership()
                if await self.did_user_cancel():
                    return ReservationKeys.states.cancelling
        
        remaining = await self.deadline_remaining('session_deadline')
        if remaining is not None and remaining <= 0:
            await self.expire_session('session_deadline')
            return ReservationKeys.states.finishing
        return ReservationKeys.states.ready

    async def get_should_finish_with_retry(self, session_id: str) -> int:
        for attempt in range(1, self.status_poll_max_attempts + 1):
            await self.ownership()
            try:
                return await self.client.get_should_finish(session_id)
            except aiohttp.client_exceptions.ClientError:
                if attempt >= self.status_poll_max_attempts:
                    raise

                logger.warning(
                    "[%s] Transient error polling session %s status; retrying (%s/%s)",
                    self.resource.identifier,
                    session_id,
                    attempt,
                    self.status_poll_max_attempts,
                    exc_info=True,
                )
                await asyncio.sleep(self.status_poll_retry_delay)
    
    async def finish(self, reservation_request: Optional[ReservationRequest], session_id: Optional[str]):
        """
        Call the dispose method on the laboratory server and finish
        """
        await self.ownership()
        status: Optional[str] = await aioredis_store.hget(self.reservation_keys.base(), ReservationKeys.parameters.status)
        if (await aioredis_store.hget(self.reservation_keys.base(), 'reconciliation_required')
                or (session_id is None and await aioredis_store.hget(self.reservation_keys.base(), 'start_attempted'))):
            return await self.fail_closed('Cannot confirm cleanup of an uncertain session')
        if status == ReservationKeys.states.finished:
            logger.info(f"[{self.resource.identifier}] Reservation {self.reservation_id} was already finished")
            return await self.deassign(reservation_request)
        if status not in (ReservationKeys.states.initializing, ReservationKeys.states.ready, ReservationKeys.states.finishing, ReservationKeys.states.cancelling):
            return await self.fail_closed('Missing or unexpected cleanup state')

        if session_id is not None:
            # First, call the dispose method in the laboratory (as much as needed)
            await self.transition(ReservationKeys.states.finishing)
            try:
                should_finish = _coerce_should_finish(await self.client.finish(session_id))
            except Exception:
                return await self.fail_closed('Invalid or lost cleanup response')
            cleanup_attempts = 0
            while should_finish >= 0:
                cleanup_attempts += 1
                if cleanup_attempts > self.max_cleanup_finish_attempts:
                    return await self.fail_closed(
                        f"remote cleanup did not finish after {self.max_cleanup_finish_attempts} attempts"
                    )
                sleep_for = should_finish if should_finish > 0 else 1
                await asyncio.sleep(min(float(sleep_for), self.max_cleanup_finish_sleep))
                await self.ownership()
                try:
                    should_finish = _coerce_should_finish(await self.client.finish(session_id))
                except Exception:
                    return await self.fail_closed('Invalid or lost cleanup response')

        # Then mark that we are indeed finished
        status = ReservationKeys.states.finished
        await self.transition(status)

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
        if self.recovery_managed:
            return await self.ownership('release')
        # A delayed old processor must never release a newer owner's resource.
        await aioredis_store.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "redis.call('expire', KEYS[2], 3600); redis.call('expire', KEYS[3], 3600); "
            "redis.call('expire', KEYS[4], 604800); "
            "return redis.call('del', KEYS[1]) end return 0",
            4, self.resource_keys.assigned(), self.reservation_keys.base(),
            self.reservation_keys.base() + ':resources',
            'lde:external-request:' + self.reservation_id, self.reservation_id)
        logger.info(f"[{self.resource.identifier}] Reservation {self.reservation_id} deassigned")
