from typing import Iterable, List, NamedTuple, Optional, Dict, Set, Union

from flask import current_app
from werkzeug.security import check_password_hash

from labdiscoveryengine.configuration.exc import InvalidLaboratoryConfigurationError

class Administrator(NamedTuple):
    """
    An administrator is a user with full access to the system.
    The administrator can only be added by modifying the configuration files.    
    """
    login: str
    name: str
    email: Optional[str]
    hashed_password: str

    def check_password_hash(self, password: str) -> bool:
        """
        With a password hashed in disk, confirm if the provided password is the same or not.
        """
        return check_password_hash(self.hashed_password, password)

class ExternalUser(NamedTuple):
    """
    An external system is a federated system that connect to use the laboratories.
    For example, LabsLand will not use the standard web APIs but the HTTP ones and
    log in as an external user.
    """
    login: str
    name: str
    email: Optional[str]
    hashed_password: str
    laboratories: Iterable[str]

    def check_password_hash(self, password: str) -> bool:
        """
        With a password hashed in disk, confirm if the provided password is the same or not.
        """
        return check_password_hash(self.hashed_password, password)    

class Resource(NamedTuple):
    """
    A laboratory si composed of one or multiple resources. A resource represents only
    a single copy of one laboratory that can be accessed by a single user.
    """
    identifier: str
    url: str
    login: str
    password: str
    features: List[str]


class Laboratory(NamedTuple):
    """
    A laboratory is the standard way to access the remote laboratory. It has other fields
    of how the user will perceive the laboratory (e.g., name, image, etc.), and it is composed
    by a set of resources.
    """
    identifier: str
    display_name: str
    description: Optional[str]
    category: Optional[str]
    keywords: list[str]
    max_time: float
    resources: Set[str]
    image: str
