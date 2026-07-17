from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader
from pymongo.asynchronous.database import AsyncDatabase

from app.auth.exceptions import CredentialsException
from app.auth.models import User
from app.auth.schemas import (
    ApiKeyCreate,
    ApiKeyCreateResponse,
    ApiKeyResponse,
    LoginRequest,
    UserCreate,
    UserResponse,
    UserRoleUpdate,
)
from app.auth.service import AuthService
from app.mongo import get_mongo_db

# Initialize API router
router = APIRouter(prefix="/auth", tags=["Authentication"])

# Setup X-API-Key extraction dependency from headers
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def get_current_user(
    api_key: Optional[str] = Security(api_key_header),
    db: AsyncDatabase = Depends(get_mongo_db),
) -> User:
    """
    Validates the API key from header and returns the user owner.
    """
    if not api_key:
        raise CredentialsException(detail="API Key missing in request headers")
    return await AuthService.validate_api_key(db, api_key)


class RoleChecker:
    def __init__(self, allowed_roles: List[str]):
        self.allowed_roles = allowed_roles

    async def __call__(self, current_user: User = Depends(get_current_user)) -> User:
        """
        Validates user role requirements for protected endpoints.
        """
        if current_user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access forbidden: insufficient permissions",
            )
        return current_user


@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def register(user_in: UserCreate, db: AsyncDatabase = Depends(get_mongo_db)):
    """
    Registers a new user profile.
    """
    return await AuthService.register_user(db, user_in)


@router.post("/login", response_model=UserResponse)
async def login(login_req: LoginRequest, db: AsyncDatabase = Depends(get_mongo_db)):
    """
    Verifies user credentials and returns user details.
    """
    return await AuthService.authenticate_user(db, login_req)


@router.post(
    "/api-keys",
    response_model=ApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_key(
    key_in: ApiKeyCreate,
    login_req: LoginRequest,
    db: AsyncDatabase = Depends(get_mongo_db),
):
    """
    Validates user credentials and generates a new API key.
    """
    user = await AuthService.authenticate_user(db, login_req)
    return await AuthService.create_api_key(db, user.user_id, key_in.name)


@router.get("/api-keys", response_model=List[ApiKeyResponse])
async def list_keys(
    current_user: User = Depends(get_current_user),
    db: AsyncDatabase = Depends(get_mongo_db),
):
    """
    Lists the metadata of all API keys owned by the user.
    """
    return await AuthService.list_api_keys(db, current_user.user_id)


@router.delete("/api-keys/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncDatabase = Depends(get_mongo_db),
):
    """
    Revokes (deletes) a specific API key belonging to the user.
    """
    await AuthService.revoke_api_key(db, current_user.user_id, id)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """
    Returns user details for a valid API key bearer.
    """
    return current_user


@router.put(
    "/users/{user_id}/role",
    response_model=UserResponse,
    dependencies=[Depends(RoleChecker(["admin"]))],
)
async def update_user_role(
    user_id: str, role_in: UserRoleUpdate, db: AsyncDatabase = Depends(get_mongo_db)
):
    """
    Modifies a target user's role (Restricted to administrators).
    """
    return await AuthService.update_user_role(db, user_id, role_in.role)
