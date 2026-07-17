import pytest

from app.auth.security import (
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_password,
)


@pytest.mark.anyio
async def test_password_hashing():
    """
    Tests password hashing and verification asynchronously.
    """
    password = "secretpassword"
    hashed = await hash_password(password)
    assert hashed != password
    assert await verify_password(password, hashed) is True
    assert await verify_password("wrongpassword", hashed) is False


@pytest.mark.anyio
async def test_api_key_generation_and_hash():
    """
    Tests generating API keys, checking prefix format, and deterministic hashing.
    """
    # Key generation
    raw_key, prefix = await generate_api_key()
    assert raw_key.startswith("sk_live_")
    assert prefix.startswith("sk_live_")
    assert len(raw_key) > len(prefix)

    # Hashing is deterministic
    hash1 = await hash_api_key(raw_key)
    hash2 = await hash_api_key(raw_key)
    assert hash1 == hash2

    # Unique keys yield unique hashes
    raw_key2, _ = await generate_api_key()
    hash3 = await hash_api_key(raw_key2)
    assert hash1 != hash3
