from shared.database.mongo import MongoDBManager


async def get_mongo_db():
    """
    Async dependency to inject the active MongoDB database instance.
    """
    return MongoDBManager.get_db()
