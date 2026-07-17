from typing import Annotated, Any, Optional
from bson import ObjectId
from pydantic import BaseModel, BeforeValidator, Field


def validate_object_id(v: Any) -> str:
    if isinstance(v, ObjectId):
        return str(v)
    if not isinstance(v, str):
        return str(v)
    return v


PyObjectId = Annotated[str, BeforeValidator(validate_object_id)]


class User(BaseModel):
    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    name: str
    user_id: str
    email: str
    password_hash: str
    role: str = "user"

    class Config:
        populate_by_name = True


class ApiKey(BaseModel):
    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    name: str = "default"
    prefix: str
    key_hash: str
    user_id: str  # References User.user_id
    is_active: bool = True

    class Config:
        populate_by_name = True
