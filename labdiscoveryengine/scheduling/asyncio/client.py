import abc
import datetime
import aiohttp
from typing import Optional, Tuple

from labdiscoveryengine.data import Resource
from labdiscoveryengine.scheduling.data import ReservationRequest
from labdiscoveryengine.scheduling.keys import ResourceKeys

from labdiscoveryengine.utils import lde_config

class AbstractResourceClient:
    """
    HTTP client wrapper for LabDiscoveryLib and WebLabLib (backwards compatibility) 
    """

    __meta__ = abc.ABCMeta

    def __init__(self, resource: Resource):
        self.resource = resource
        self.base_url = resource.url
        if self.base_url.endswith('/'):
            self.base_url = self.base_url[:-1]
        self.resource_keys = ResourceKeys(resource.identifier)
        self.auth = aiohttp.BasicAuth(resource.login, resource.password)
        self.client_session: Optional[aiohttp.ClientSession] = aiohttp.ClientSession(auth=self.auth)

    async def __aenter__(self):
        await self.client_session.__aenter__()
        return self
    
    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.client_session.__aexit__(exc_type, exc_value, traceback)

    @staticmethod
    def create(resource: Resource) -> "AbstractResourceClient":
        """
        Create a client for the given resource
        """
        if resource.api.startswith('weblablib'):
            return WebLabLibResourceClient(resource)
        else:
            return LabDiscoveryLibResourceClient(resource)

    @abc.abstractmethod
    def _get_url(self, path: str):
        "Get the URL using the proper suffix"

    @abc.abstractmethod
    def _get_start_body(self, reservation_request: ReservationRequest) -> dict:
        "Create the body for the POST /sessions/ request in the appropriate format"
    
    async def start(self, reservation_request: ReservationRequest) -> Tuple[str, str]:
        """
        Start a reservation in labdiscoverylib
        """
        url = self._get_url("/sessions/")

        body = self._get_start_body(reservation_request)

        async with self.client_session.post(url, json=body) as response:
            result: dict = await response.json()
        
        if result.get('error') or result.get('success', True) == False:
            raise Exception(f"Error starting reservation {reservation_request.identifier}: {result}")
        
        url = result.get('url')
        ldl_session_id = result.get('session_id')
        return url, ldl_session_id
    
    async def get_should_finish(self, session_id: str) -> int:
        """
        Return the time left in the lab
        """
        url = self._get_url(f"/sessions/{session_id}/status")

        async with self.client_session.get(url) as response:
            result: dict = await response.json()

        return result.get('should_finish') or 0
    
    delete_on_finish = True

    async def finish(self, session_id: str) -> float:
        """
        Call dispose on the laboratory. If the laboratory has not finished
        cleaning resources, it will return {"should_finish": 10} with a
        positive number, and we should wait fo that one and call again
        until it is a negative number.
        """
        url = self._get_url(f"/sessions/{session_id}")

        if self.delete_on_finish:
            async with self.client_session.delete(url) as response:
                result: dict = await response.json()
        else:
            body = {
                "action": "delete"
            }
            async with self.client_session.post(url, json=body) as response:
                result: dict = await response.json()

        return result.get('should_finish', -1)
        
class LabDiscoveryLibResourceClient(AbstractResourceClient):
    """
    HTTP Client wrapper of the LDL client
    """
    def _get_url(self, path: str):
        return f"{self.resource.url}/ldl{path}"
    
    def _get_start_body(self, reservation_request: ReservationRequest) -> dict:
        now = datetime.datetime.now(datetime.timezone.utc)
        laboratory = lde_config.laboratories[reservation_request.laboratory]
        return {
            'request': {
                'locale': reservation_request.locale,
                'ldeReservationId': reservation_request.identifier,
                'user': {
                    # User data
                },
                'server': {
                    # Server data
                },
                'backUrl': reservation_request.back_url,
            },
            'laboratory': {
                'name': laboratory.identifier,
                'category': laboratory.category,
            },
            'user': {
                'username': reservation_request.external_user_identifier or reservation_request.user_identifier,
                'unique': reservation_request.unique_username,
                'fullName': reservation_request.user_full_name,
            },
            'schedule': {
                'start': now.isoformat(),
                'length': reservation_request.max_time,
            }
        }

class WebLabLibResourceClient(AbstractResourceClient):
    """
    HTTP Client wrapper of the weblablib client
    """
    # call POST on finish
    delete_on_finish = False

    def _get_url(self, path: str):
        return f"{self.resource.url}/weblab{path}"

    def _get_start_body(self, reservation_request: ReservationRequest) -> dict:
        now = datetime.datetime.now(datetime.timezone.utc)
        laboratory = lde_config.laboratories[reservation_request.laboratory]
        return {
            'client_initial_data': {},
            'server_initial_data': {
                'request.locale': reservation_request.locale,
                'request.username': reservation_request.external_user_identifier or reservation_request.user_identifier,
                'request.username.unique': reservation_request.unique_username,
                'request.full_name': reservation_request.user_full_name,
                'request.experiment_id.experiment_name': laboratory.identifier,
                'request.experiment_id.category_name': laboratory.category,

                'reservation_id': reservation_request.identifier,

                'priority.queue.slot.length': reservation_request.max_time,
                'priority.queue.slot.start': now.isoformat(),
                'priority.queue.slot.start.utc': now.isoformat(),
                'priority.queue.slot.start.timestamp': now.timestamp(),
            },
            'back': reservation_request.back_url,
        }