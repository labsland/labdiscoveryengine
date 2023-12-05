import abc
from typing import NamedTuple, Optional, List

from labdiscoveryengine.scheduling.keys import ReservationKeys

class ReservationRequest(NamedTuple):
    """
    A reservation request represents a request to reserve a laboratory
    """
    identifier: str
    laboratory: str
    features: List[str]
    resources: List[str]
    user_identifier: str
    user_role: str
    locale: str
    max_time: float
    back_url: str

    # default priority. 1 is maximum priority, 10 is lowest priority
    priority: Optional[int] = 5

    # Not mandatory and often not provided
    user_full_name: Optional[str] = None

    # Not 'labsland', but an anonymous user in the other system
    external_user_identifier: Optional[str] = None

    @property
    def unique_username(self):
        if self.external_user_identifier:
            return f'{self.external_user_identifier}@{self.user_identifier}'
        return self.user_identifier

    def todict(self):
        return {
            'identifier': self.identifier,
            'laboratory': self.laboratory,
            'features': self.features,
            'resources': self.resources,
            'user_identifier': self.user_identifier,
            'user_role': self.user_role,
            'user_full_name': self.user_full_name,
            'locale': self.locale,
            'max_time': self.max_time,
            'priority': self.priority,
            'external_user_identifier': self.external_user_identifier,
            'back_url': self.back_url,
        }
    
    @staticmethod
    def fromdict(data) -> 'ReservationRequest':
        kwargs = dict(
            identifier=data['identifier'],
            laboratory=data['laboratory'],
            features=data['features'],
            resources=data['resources'],
            user_identifier=data['user_identifier'],
            user_role=data['user_role'],
            locale=data['locale'],
            max_time=data['max_time'],
            back_url=data['back_url'],
        )

        if data.get('priority') is not None:
            kwargs['priority'] = data['priority']

        if data.get('external_user_identifier') is not None:
            kwargs['external_user_identifier'] = data['external_user_identifier']

        if data.get('user_full_name') is not None:
            kwargs['user_full_name'] = data['user_full_name']

        return ReservationRequest(**kwargs)
    
class ReservationStatus(NamedTuple):
    status: str # See ReservationKeys.states for potential status
    reservation_id: str

    external_session_id: Optional[str] = None
    position: Optional[int] = None
    url: Optional[str] = None

    def has_changed_from(self, previous_status: 'ReservationStatus') -> bool:
        # We only care of this two really
        return self.status != previous_status.status or self.position != previous_status.position

    def todict(self):
        result = {
            'status': self.status,
            'reservation_id': self.reservation_id,
        }

        if self.status in (ReservationKeys.states.queued,):
            result['position'] = self.position

        if self.status in (ReservationKeys.states.ready, ReservationKeys.states.cancelling, ReservationKeys.states.finishing):
            result['url'] = self.url
            result['external_session_id'] = self.external_session_id

        return result
    
    @staticmethod
    def fromdict(data: dict) -> 'ReservationStatus':
        return ReservationStatus(
            status=data['status'],
            reservation_id=data['reservation_id'],
            external_session_id=data.get('external_session_id'),
            position=data.get('position'),
            url=data.get('url'),
        )
