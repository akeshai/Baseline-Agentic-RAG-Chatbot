from typing import Optional, List
from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from app.auth.models import ApiKey, User


def map_user(doc: dict) -> User:
    doc_copy = dict(doc)
    doc_copy["id"] = str(doc_copy["_id"])
    return User(**doc_copy)


def map_api_key(doc: dict) -> ApiKey:
    doc_copy = dict(doc)
    doc_copy["id"] = str(doc_copy["_id"])
    return ApiKey(**doc_copy)


class UserRepository:
    @staticmethod
    async def get_by_id(db: AsyncDatabase, id: str) -> Optional[User]:
        """
        Asynchronously retrieves a user by their MongoDB ObjectId string.
        """
        try:
            doc = await db.users.find_one({"_id": ObjectId(id)})
            if doc:
                return map_user(doc)
        except Exception:
            pass
        return None

    @staticmethod
    async def get_by_email(db: AsyncDatabase, email: str) -> Optional[User]:
        """
        Asynchronously retrieves a user by their unique email address.
        """
        doc = await db.users.find_one({"email": email})
        if doc:
            return map_user(doc)
        return None

    @staticmethod
    async def get_by_user_id(db: AsyncDatabase, user_id: str) -> Optional[User]:
        """
        Asynchronously retrieves a user by their unique string user ID (username).
        """
        doc = await db.users.find_one({"user_id": user_id})
        if doc:
            return map_user(doc)
        return None

    @staticmethod
    async def create(db: AsyncDatabase, user: User) -> User:
        """
        Asynchronously persists a new user record to MongoDB.
        """
        user_dict = user.model_dump(exclude={"id"})
        res = await db.users.insert_one(user_dict)
        user.id = str(res.inserted_id)
        return user


class ApiKeyRepository:
    @staticmethod
    async def get_by_hash(db: AsyncDatabase, key_hash: str) -> Optional[ApiKey]:
        """
        Asynchronously retrieves an ApiKey by its hash value.
        """
        doc = await db.api_keys.find_one({"key_hash": key_hash})
        if doc:
            return map_api_key(doc)
        return None

    @staticmethod
    async def get_by_id(db: AsyncDatabase, key_id: str) -> Optional[ApiKey]:
        """
        Asynchronously retrieves an ApiKey by its primary key ID string.
        """
        try:
            doc = await db.api_keys.find_one({"_id": ObjectId(key_id)})
            if doc:
                return map_api_key(doc)
        except Exception:
            pass
        return None

    @staticmethod
    async def get_by_user_id(db: AsyncDatabase, user_id: str) -> List[ApiKey]:
        """
        Asynchronously retrieves all active API keys associated with a user.
        """
        cursor = db.api_keys.find({"user_id": user_id})
        keys = []
        async for doc in cursor:
            keys.append(map_api_key(doc))
        return keys

    @staticmethod
    async def create(db: AsyncDatabase, api_key: ApiKey) -> ApiKey:
        """
        Asynchronously persists a new ApiKey record to MongoDB.
        """
        key_dict = api_key.model_dump(exclude={"id"})
        res = await db.api_keys.insert_one(key_dict)
        api_key.id = str(res.inserted_id)
        return api_key

    @staticmethod
    async def delete(db: AsyncDatabase, api_key: ApiKey) -> None:
        """
        Asynchronously deletes an ApiKey record from MongoDB.
        """
        try:
            await db.api_keys.delete_one({"_id": ObjectId(api_key.id)})
        except Exception:
            pass
