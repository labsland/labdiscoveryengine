from typing import Any

from flask import current_app

from labdiscoveryengine.utils import is_mongo_active, create_proxied_instance

async_mongo: Any = create_proxied_instance(object)

async def initialize_mongodb():
    """
    Initialize the MongoDB client.
    """
    if is_mongo_active():
        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient(current_app.config['MONGO_URI'])
        async_mongo.set_proxied_object(client.get_default_database())
