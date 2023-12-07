import re
import asyncio
from functools import wraps
from unicodedata import normalize
import motor

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

def is_mongo_active(app = None) -> bool:
    """
    Returns if we are using MongoDB or not
    """
    return (app or current_app).config.get('USING_MONGO')

def is_sql_active(app = None) -> bool:
    """
    Returns if we are using SQLAlchemy or not
    """
    return (app or current_app).config.get('USING_SQLALCHEMY')

def slugify(value):
    """
    Convert a string into a slug representation suitable for URLs.
    """
    value = normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value).strip().lower()
    return re.sub(r'[-\s]+', '-', value)

def create_proxied_instance(klass: type):
    """
    We want to use certain modules (e.g., SQLAlchemy, Redis, etc.) but that will be
    initialized later on (or maybe never). We want to import them as usual in Flask:

    from foo import whatever

    whatever.do()

    So we have this class that can wrap those classes. For example:

    redis_store = create_proxied_instance(Redis)

    and later, if we initialize it, we will do:

    redis_store.initialize_with_object(Redis(host="..."))

    and in any case we can do the following:
    redis_store.set("foo", "bar")
    """
    class ProxyClass:
        def __init__(self):
            self._obj = None
            self._method_cache = {}

        def set_proxied_object(self, obj: klass):
            self._obj = obj

        def get_proxied_object(self):
            return self._obj

        def _create_sync_wrapper(self, attr):
            @wraps(attr)
            def sync_method(*args, **kwargs):
                if self._obj is None:
                    raise Exception(f"{klass} is not initialized")
                return attr(*args, **kwargs)
            return sync_method

        def _create_async_wrapper(self, attr):
            @wraps(attr)
            async def async_method(*args, **kwargs):
                if self._obj is None:
                    raise Exception(f"{klass} is not initialized")
                return await attr(*args, **kwargs)
            return async_method

        def __getattr__(self, name):
            """
            Wrap all the methods of the class provided (klass)
            """
            if self._obj is None:
                raise Exception(f"{klass} is not initialized")

            # Check the cache first
            if name in self._method_cache:
                return self._method_cache[name]

            # Get the attribute from the instance
            attr = getattr(self._obj, name)

            # Create and cache the appropriate wrapper
            if isinstance(attr, motor.motor_asyncio.AsyncIOMotorCollection):
                return attr
            elif asyncio.iscoroutinefunction(attr):
                wrapper = self._create_async_wrapper(attr)
            elif callable(attr):
                wrapper = self._create_sync_wrapper(attr)
            else:
                wrapper = attr

            self._method_cache[name] = wrapper
            return wrapper
        
        def __repr__(self):
            return f"MimicClass of ({klass}) with obj: {self._obj}"
        
    ProxyClass.__doc__ = klass.__doc__
    return ProxyClass()