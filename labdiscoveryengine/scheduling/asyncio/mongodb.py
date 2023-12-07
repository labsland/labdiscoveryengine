from flask import current_app

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from labdiscoveryengine.utils import is_mongo_active, create_proxied_instance

async_mongo: AsyncIOMotorClient = create_proxied_instance(AsyncIOMotorClient)

async def initialize_mongodb():
    """
    Initialize the MongoDB client.
    """
    if is_mongo_active():
        client = AsyncIOMotorClient(current_app.config['MONGO_URI'])
        async_mongo.set_proxied_object(client.get_default_database())
