import asyncio
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pydantic_settings import BaseSettings, SettingsConfigDict


class MongoSettings(BaseSettings):
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "chatbot"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


mongo_settings = MongoSettings()


class MongoDBManager:
    """
    Manages MongoDB async client connection pool using PyMongo AsyncMongoClient.
    Dynamically recreates the client if the event loop changes (common in unit tests).
    """

    _client: AsyncMongoClient | None = None

    @classmethod
    def get_db(cls) -> AsyncDatabase:
        current_loop = None
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        if cls._client is None or (
            cls._client._loop is not None and cls._client._loop != current_loop
        ):
            cls._client = AsyncMongoClient(mongo_settings.mongo_uri)
        return cls._client[mongo_settings.mongo_db]

    @classmethod
    async def close(cls) -> None:
        if cls._client is not None:
            try:
                await cls._client.close()
            except RuntimeError:
                # Catch RuntimeError if we attempt to close from a different event loop
                pass
            finally:
                cls._client = None
