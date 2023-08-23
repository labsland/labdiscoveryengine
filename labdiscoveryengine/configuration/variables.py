import os
from typing import Optional

class Config:
    # By default labdiscoveryengine will look for the configuration files in the current directory
    # But if we provide a different LABDISCOVERYENGINE_DIRECTORY, it will search it elsewhere
    LABDISCOVERYENGINE_DIRECTORY: str = os.environ.get('LABDISCOVERYENGINE_DIRECTORY') or '.'

    DEFAULT_MAX_TIME: float = float(os.environ.get('DEFAULT_MAX_TIME') or '300')
    DEFAULT_RESOURCE_LOGIN: Optional[str] = os.environ.get('DEFAULT_RESOURCE_LOGIN')
    DEFAULT_RESOURCE_PASSWORD: Optional[str] = os.environ.get('DEFAULT_RESOURCE_PASSWORD')

class DevelopmentConfig(Config):
    DEBUG = True

class TestingConfig(Config):
    TESTING = True

class ProductionConfig(Config):
    DEBUG = False

configurations = {
    'default': DevelopmentConfig,
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
}
