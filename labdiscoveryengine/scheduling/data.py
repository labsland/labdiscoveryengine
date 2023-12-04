from typing import NamedTuple, Optional, List

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

    # default priority. 1 is maximum priority, 10 is lowest priority
    priority: Optional[int] = 5

    # Not 'labsland', but an anonymous user in the other system
    external_user_identifier: Optional[str] = None

    def todict(self):
        return {
            'identifier': self.identifier,
            'laboratory': self.laboratory,
            'features': self.features,
            'resources': self.resources,
            'user_identifier': self.user_identifier,
            'user_role': self.user_role,
            'priority': self.priority,
            'external_user_identifier': self.external_user_identifier,
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
        )

        if data.get('priority') is not None:
            kwargs['priority'] = data['priority']

        if data.get('external_user_identifier') is not None:
            kwargs['external_user_identifier'] = data['external_user_identifier']

        return ReservationRequest(**kwargs)