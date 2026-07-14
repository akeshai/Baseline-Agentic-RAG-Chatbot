import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt
from app.auth.exceptions import CredentialsException
from app.configs.auth import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS

async def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Asynchronously creates a JWT access token.
    """
    def _encode():
        to_encode = data.copy()
        now = datetime.now(timezone.utc)
        if expires_delta:
            expire = now + expires_delta
        else:
            expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        
        to_encode.update({
            "exp": int(expire.timestamp()),
            "iat": int(now.timestamp()),
            "type": "access"
        })
        return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

    return await asyncio.to_thread(_encode)

async def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Asynchronously creates a JWT refresh token.
    """
    def _encode():
        to_encode = data.copy()
        now = datetime.now(timezone.utc)
        if expires_delta:
            expire = now + expires_delta
        else:
            expire = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
            
        to_encode.update({
            "exp": int(expire.timestamp()),
            "iat": int(now.timestamp()),
            "type": "refresh"
        })
        return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

    return await asyncio.to_thread(_encode)

async def decode_token(token: str) -> dict:
    """
    Asynchronously decodes and validates a JWT token.
    """
    def _decode():
        try:
            return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        except jwt.ExpiredSignatureError:
            raise CredentialsException(detail="Token has expired")
        except jwt.InvalidTokenError:
            raise CredentialsException(detail="Invalid token")

    return await asyncio.to_thread(_decode)
