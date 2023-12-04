import abc
from typing import Optional, Tuple
import aiohttp
from labdiscoveryengine.data import Resource
from labdiscoveryengine.scheduling.data import ReservationRequest
from labdiscoveryengine.scheduling.keys import ResourceKeys

class GenericResourceClient:
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
        ldl_session_id = result.get('url')
        return url, ldl_session_id

class LabDiscoveryLibResourceClient(GenericResourceClient):
    """
    HTTP Client wrapper of the LDL client
    """
    def _get_url(self, path: str):
        return f"{self.resource.url}/ldl{path}"
    
    def _get_start_body(self, reservation_request: ReservationRequest) -> dict:
        return {
            'client_initial_data': {},
            'server_initial_data': {
                'request.locale': 'en', # TODO
                'request.username.unique': '', # TODO
                'request.full_name': '', # TODO
                'request.experiment_id.experiment_name': '', # TODO
                'request.experiment_id.category_name': '', # TODO

                'reservation_id': reservation_request.identifier,

                'priority.queue.slot.length': '', # TODO: max session length
                'priority.queue.slot.start': '', # TODO (in UTC)
                'priority.queue.slot.start.timestamp': '', # TODO (timestamp)
                'priority.queue.slot.start.timezone': '', # TODO
            },
        }

class WebLabLibResourceClient(GenericResourceClient):
    """
    HTTP Client wrapper of the weblablib client
    """
    def _get_url(self, path: str):
        return f"{self.resource.url}/weblab{path}"

    def _get_start_body(self, reservation_request: ReservationRequest) -> dict:
        return {
            'client_initial_data': {},
            'server_initial_data': {
                'request.locale': 'en', # TODO
                'request.username.unique': '', # TODO
                'request.full_name': '', # TODO
                'request.experiment_id.experiment_name': '', # TODO
                'request.experiment_id.category_name': '', # TODO

                'reservation_id': reservation_request.identifier,

                'priority.queue.slot.length': '', # TODO: max session length
                'priority.queue.slot.start': '', # TODO (in UTC)
                'priority.queue.slot.start.timestamp': '', # TODO (timestamp)
                'priority.queue.slot.start.timezone': '', # TODO
            },
        }