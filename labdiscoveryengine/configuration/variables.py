import os
from typing import Optional

class Config:
    # By default labdiscoveryengine will look for the configuration files in the current directory
    # But if we provide a different LABDISCOVERYENGINE_DIRECTORY, it will search it elsewhere
    LABDISCOVERYENGINE_DIRECTORY: str = os.environ.get('LABDISCOVERYENGINE_DIRECTORY') or '.'

    THEME: str = os.environ.get('THEME') or 'default'
    SECRET_KEY: str = os.environ.get('SECRET_KEY') or 'secret'

    REDIS_URL: str = os.environ.get('REDIS_URL') or 'redis://localhost:6379/0'

    DEFAULT_MAX_TIME: float = float(os.environ.get('DEFAULT_MAX_TIME') or '300')
    DEFAULT_RESOURCE_LOGIN: Optional[str] = os.environ.get('DEFAULT_RESOURCE_LOGIN')
    DEFAULT_RESOURCE_PASSWORD: Optional[str] = os.environ.get('DEFAULT_RESOURCE_PASSWORD')
    DEFAULT_LAB_VISIBILITY: str = os.environ.get('DEFAULT_LAB_VISIBILITY') or 'public'
    TESTING = False
    DEBUG = False
    
    DEBUG_TB_INTERCEPT_REDIRECTS = False

    SQLALCHEMY_DATABASE_URI: Optional[str] = os.environ.get('SQLALCHEMY_DATABASE_URI')
    MONGO_URI: Optional[str] = os.environ.get('MONGO_URI')

    # These two variables are set on the create_app depening on whether we have SQLALCHEMY_DATABASE_URI or MONGO_URI or not
    USING_MONGO = None
    USING_SQLALCHEMY = None

class DevelopmentConfig(Config):
    DEBUG = True
    LABDISCOVERYENGINE_DIRECTORY: str = os.environ.get('LABDISCOVERYENGINE_DIRECTORY') or 'tests/deployments/simple'

class TestingConfig(Config):
    TESTING = True
    LABDISCOVERYENGINE_DIRECTORY: str = os.environ.get('LABDISCOVERYENGINE_DIRECTORY') or 'tests/deployments/simple'

class ProductionConfig(Config):
    DEBUG = False
    SECRET_KEY: str = os.environ.get('SECRET_KEY')

configurations = {
    'default': DevelopmentConfig,
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
}
