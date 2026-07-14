import asyncio
import bcrypt


async def hash_password(password: str) -> str:
    """
    Asynchronously hashes a cleartext password using bcrypt on a background thread.
    """

    def _hash():
        pwd_bytes = password.encode("utf-8")
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(pwd_bytes, salt)
        return hashed.decode("utf-8")

    return await asyncio.to_thread(_hash)


async def verify_password(password: str, hashed_password: str) -> bool:
    """
    Asynchronously verifies a cleartext password against a bcrypt hash on a background thread.
    """

    def _verify():
        pwd_bytes = password.encode("utf-8")
        hash_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(pwd_bytes, hash_bytes)

    return await asyncio.to_thread(_verify)
