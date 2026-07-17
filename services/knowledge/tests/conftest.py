import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# Enforce test databases
os.environ["MONGO_DB"] = "test_chatbot"
os.environ["MILVUS_COLLECTION"] = "test_chatbot_chunks"
os.environ["REDIS_URL"] = "redis://localhost:6379/1"
os.environ["OBJECT_STORAGE_PROVIDER"] = "local"
os.environ["OBJECT_STORAGE_ROOT"] = "test_storage_buckets"

from main import app  # noqa: E402
from shared.database.mongo import MongoDBManager  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_storage():
    """
    Automatically cleans up any files written to the test_storage_buckets directory
    at the end of the test session.
    """
    import shutil

    # Clean up before session starts
    if os.path.exists("test_storage_buckets"):
        try:
            shutil.rmtree("test_storage_buckets")
        except Exception:
            pass
    yield
    # Clean up after session ends
    if os.path.exists("test_storage_buckets"):
        try:
            shutil.rmtree("test_storage_buckets")
        except Exception:
            pass


async def _async_db_cleanup():
    """Helper to perform async database operations synchronously in fixtures."""
    import redis.asyncio as aioredis
    from app.vector_store.factory import get_vector_store

    # 1. Clean up MongoDB
    db = MongoDBManager.get_db()
    try:
        collections = await db.list_collection_names()
        for collection in collections:
            await db[collection].drop()
    except Exception:
        pass

    # 2. Clean up Redis
    try:
        r = aioredis.from_url(os.environ["REDIS_URL"])
        await r.flushdb()
        await r.close()
    except Exception:
        pass

    # 3. Clean up Milvus
    try:
        vs = get_vector_store()
        if await vs.client.has_collection(vs.collection_name):
            await vs.client.drop_collection(vs.collection_name)
        await vs.close()
    except Exception:
        pass


@pytest.fixture(name="client")
def fixture_client():
    """
    Provides a FastAPI TestClient configured for the test environment.
    Automatically drops MongoDB test database before and after each test for full isolation.
    """
    # Run setup database cleanup synchronously
    asyncio.run(_async_db_cleanup())

    # Yield client
    with TestClient(app) as test_client:
        yield test_client

    # Run teardown database cleanup synchronously
    asyncio.run(_async_db_cleanup())
    asyncio.run(MongoDBManager.close())


@pytest.fixture
def anyio_backend():
    """
    Defines backend runner for anyio async tests.
    """
    return "asyncio"


@pytest.fixture(autouse=True)
def mock_oci_client(request):
    """
    Globally mock the OCI GenerativeAiInferenceClient to prevent real API calls
    and avoid loading nonexistent local OCI config credentials during unit tests.
    Bypassed if the test is marked with @pytest.mark.real_oci.
    """
    if request.node.get_closest_marker("real_oci"):
        yield
        return

    import hashlib
    import random
    from unittest.mock import MagicMock, patch

    mock_instance = MagicMock()

    # 1. Mock embed_text
    def mock_embed_text(details):
        from app.configs.dbs import settings as db_settings

        inputs = details.inputs
        embeddings = []
        dim = db_settings.vector_dim
        for text in inputs:
            seed = int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16) % 100000
            rng = random.Random(seed)
            embeddings.append([rng.uniform(-1.0, 1.0) for _ in range(dim)])

        mock_response = MagicMock()
        mock_response.data = MagicMock()
        mock_response.data.embeddings = embeddings
        return mock_response

    mock_instance.embed_text.side_effect = mock_embed_text

    # 2. Mock generate_text
    def mock_generate_text(details):
        mock_response = MagicMock()
        mock_response.data = MagicMock()
        mock_response.data.inference_response = MagicMock()

        prompt_text = details.inference_request.prompt

        # Cohere style
        generated_text = MagicMock()
        generated_text.text = f"Mock completion response for: {prompt_text[:30]}..."
        mock_response.data.inference_response.generated_texts = [generated_text]

        # Llama/Generic style
        choice = MagicMock()
        choice.text = f"Mock completion response for: {prompt_text[:30]}..."
        mock_response.data.inference_response.choices = [choice]

        return mock_response

    mock_instance.generate_text.side_effect = mock_generate_text

    # 3. Mock chat
    def mock_chat(details):
        mock_response = MagicMock()
        mock_response.data = MagicMock()

        # Determine the user prompt message from Cohere or Generic request type
        if hasattr(details.chat_request, "message"):
            prompt_summary = details.chat_request.message[:30]
        else:
            # GenericChatRequest has messages list
            last_msg = details.chat_request.messages[-1]
            if hasattr(last_msg, "content") and last_msg.content:
                prompt_summary = last_msg.content[0].text[:30]
            elif hasattr(last_msg, "tool_call_id"):
                prompt_summary = f"tool_response_for_{last_msg.tool_call_id}"
            elif hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                prompt_summary = f"tool_calls_{len(last_msg.tool_calls)}"
            else:
                prompt_summary = "empty"

        # Cohere response style
        mock_response.data.chat_response = MagicMock()
        mock_response.data.chat_response.text = (
            f"Mock chat response for: {prompt_summary}..."
        )

        # Llama/Generic response style
        chat_choice = MagicMock()
        chat_choice.message = MagicMock()
        chat_content = MagicMock()
        chat_content.text = f"Mock chat response for: {prompt_summary}..."
        chat_choice.message.content = [chat_content]
        mock_response.data.chat_response.choices = [chat_choice]
        mock_response.data.choices = [chat_choice]  # Keep fallback

        return mock_response

    mock_instance.chat.side_effect = mock_chat

    with (
        patch("app.llm.oci.GenerativeAiInferenceClient", return_value=mock_instance),
        patch(
            "app.embeddings.oci.GenerativeAiInferenceClient", return_value=mock_instance
        ),
        patch(
            "oci.generative_ai_inference.generative_ai_inference_client.Signer",
            return_value=MagicMock(),
        ),
        patch(
            "oci.config.from_file",
            return_value={
                "tenancy": "ocid1.tenancy.oc1..dummy",
                "user": "ocid1.user.oc1..dummy",
                "fingerprint": "00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00",
                "key_file": "dummy.pem",
                "region": "us-chicago-1",
            },
        ),
    ):
        yield mock_instance
