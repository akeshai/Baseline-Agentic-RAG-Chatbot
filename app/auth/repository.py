from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import Optional, Sequence
from app.auth.models import User, ApiKey

class UserRepository:
    @staticmethod
    async def get_by_id(db: AsyncSession, id: int) -> Optional[User]:
        """
        Asynchronously retrieves a user by their auto-incremented primary key.
        """
        stmt = select(User).filter(User.id == id)
        result = await db.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def get_by_email(db: AsyncSession, email: str) -> Optional[User]:
        """
        Asynchronously retrieves a user by their unique email address.
        """
        stmt = select(User).filter(User.email == email)
        result = await db.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def get_by_user_id(db: AsyncSession, user_id: str) -> Optional[User]:
        """
        Asynchronously retrieves a user by their unique string user ID (username).
        """
        stmt = select(User).filter(User.user_id == user_id)
        result = await db.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def create(db: AsyncSession, user: User) -> User:
        """
        Asynchronously persists a new user record to the database.
        """
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

class ApiKeyRepository:
    @staticmethod
    async def get_by_hash(db: AsyncSession, key_hash: str) -> Optional[ApiKey]:
        """
        Asynchronously retrieves an ApiKey by its hash value, eager loading the owner User.
        """
        stmt = (
            select(ApiKey)
            .filter(ApiKey.key_hash == key_hash)
            .options(selectinload(ApiKey.user))
        )
        result = await db.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def get_by_id(db: AsyncSession, key_id: int) -> Optional[ApiKey]:
        """
        Asynchronously retrieves an ApiKey by its primary key ID.
        """
        stmt = select(ApiKey).filter(ApiKey.id == key_id)
        result = await db.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def get_by_user_id(db: AsyncSession, user_id: int) -> Sequence[ApiKey]:
        """
        Asynchronously retrieves all active API keys associated with a user.
        """
        stmt = select(ApiKey).filter(ApiKey.user_id == user_id)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def create(db: AsyncSession, api_key: ApiKey) -> ApiKey:
        """
        Asynchronously persists a new ApiKey record to the database.
        """
        db.add(api_key)
        await db.commit()
        await db.refresh(api_key)
        return api_key

    @staticmethod
    async def delete(db: AsyncSession, api_key: ApiKey) -> None:
        """
        Asynchronously deletes an ApiKey record from the database.
        """
        await db.delete(api_key)
        await db.commit()
