from werkzeug.local import LocalProxy
from flask import current_app

from labdiscoveryengine.configuration.storage import StoredConfiguration

def get_lde_config() -> StoredConfiguration:
    # This is added in __init__.py
    config = current_app.config.get('LDE_CONFIG')
    if config is None:
        raise RuntimeError("LDE_CONFIG is not set")
    
    return config

lde_config: StoredConfiguration = LocalProxy(get_lde_config)