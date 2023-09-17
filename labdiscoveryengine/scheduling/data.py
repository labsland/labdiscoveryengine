import secrets
import datetime
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

    # Not 'labsland', but an anonymous user in the other system
    external_user_identifier: Optional[str] = None
