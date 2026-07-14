import asyncio
import secrets
import hashlib

async def generate_api_key() -> tuple[str, str]:
    """
    Asynchronously generates a secure API Key and its short prefix on a background thread.
    Returns:
        tuple[str, str]: (raw_api_key, prefix)
    """
    def _generate():
        token = secrets.token_urlsafe(32)
        raw_key = f"sk_live_{token}"
        # Prefix is "sk_live_" + first 6 chars of the secret token
        prefix = f"sk_live_{token[:6]}"
        return raw_key, prefix

    return await asyncio.to_thread(_generate)

async def hash_api_key(plain_key: str) -> str:
    """
    Asynchronously hashes the plaintext API key using SHA-256 on a background thread.
    """
    def _hash():
        return hashlib.sha256(plain_key.encode("utf-8")).hexdigest()

    return await asyncio.to_thread(_hash)
