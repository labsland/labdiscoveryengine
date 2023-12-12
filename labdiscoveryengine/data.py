import abc
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

class Healthcheck:
    __meta__ = abc.ABCMeta

    def __init__(self, identifier: str):
        self.identifier: str = identifier

class HttpHealthcheck(Healthcheck):
    """
    A healthcheck is a HTTP call to the laboratory.
    """
    def __init__(self, identifier: str, url: str):
        self.identifier: str = identifier
        self.url: str = url

class Camera:
    """
    A camera is a webcam that is connected to the laboratory.

    There can be an image camera and more options (timeout, etc.)
    """
    __meta__ = abc.ABCMeta

    def __init__(self, identifier: str):
        self.identifier: str = identifier

class ImageCamera(Camera):
    """
    This represents a webcam that uses imgage refresh (e.g., jpg's)
    """
    def __init__(self, identifier: str, url: str):
        self.identifier = identifier
        self.url = url

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

    cameras: List[Camera]
    healthchecks: List[Healthcheck]

    # Also acceptable: weblablib-v1.0
    api: str = "labdiscoverylib-v1.0"


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
    keywords: List[str]
    max_time: float
    resources: Set[str]
    image: str
    features: Set[str]
