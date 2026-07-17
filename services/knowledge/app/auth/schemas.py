from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=100, description="The user's display name"
    )
    user_id: str = Field(
        ...,
        min_length=3,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Unique username identifier",
    )
    email: EmailStr
    password: str = Field(
        ..., min_length=6, description="Password with at least 6 characters"
    )
    role: Optional[str] = "user"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    name: str
    user_id: str
    email: EmailStr
    role: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserRoleUpdate(BaseModel):
    role: str = Field(
        ..., description="The new role to assign to the user (e.g. 'admin', 'user')"
    )


class ApiKeyCreate(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="A friendly label to identify this API Key",
    )


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    name: str
    prefix: str
    is_active: bool


class ApiKeyCreateResponse(ApiKeyResponse):
    plain_key: str = Field(
        ..., description="The plain text secret key. Show this ONLY once to the user."
    )
