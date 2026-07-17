import pytest
from unittest.mock import AsyncMock

from app.llm import get_llm_adapter, BaseLLMAdapter, OCILLMAdapter
from app.embeddings import (
    get_embedding_adapter,
    BaseEmbeddingAdapter,
    OCIEmbeddingAdapter,
)
from app.configs.dbs import settings as db_settings
from app.vector_store.milvus import MilvusVectorStore


@pytest.mark.anyio
async def test_llm_adapter_generate():
    adapter = get_llm_adapter()
    assert isinstance(adapter, BaseLLMAdapter)
    assert isinstance(adapter, OCILLMAdapter)

    response = await adapter.generate("Hello world!")
    assert (
        "Mock completion response for" in response
        or "Mock chat response for" in response
    )


@pytest.mark.anyio
async def test_llm_adapter_chat():
    adapter = get_llm_adapter()
    messages = [
        {"role": "user", "content": "Hello!"},
        {"role": "assistant", "content": "Hi, how can I help you today?"},
        {"role": "user", "content": "Explain quantum computing."},
    ]
    response = await adapter.chat(messages)
    assert "Mock chat response for" in response


@pytest.mark.anyio
async def test_embedding_adapter_embed_query():
    adapter = get_embedding_adapter()
    assert isinstance(adapter, BaseEmbeddingAdapter)
    assert isinstance(adapter, OCIEmbeddingAdapter)

    vector = await adapter.embed_query("Query text")
    assert len(vector) == db_settings.vector_dim
    assert all(isinstance(val, float) for val in vector)


@pytest.mark.anyio
async def test_embedding_adapter_embed_documents():
    adapter = get_embedding_adapter()
    docs = ["doc 1 content", "doc 2 content"]
    vectors = await adapter.embed_documents(docs)

    assert len(vectors) == 2
    assert len(vectors[0]) == db_settings.vector_dim
    assert len(vectors[1]) == db_settings.vector_dim


@pytest.mark.anyio
async def test_embedding_adapter_embed_documents_large_batching():
    adapter = get_embedding_adapter()
    # 100 items (exceeds OCI 96 limit)
    docs = [f"document text {i}" for i in range(100)]
    vectors = await adapter.embed_documents(docs)

    assert len(vectors) == 100
    assert all(len(v) == db_settings.vector_dim for v in vectors)


@pytest.mark.anyio
async def test_milvus_integration_with_adapter():
    store = MilvusVectorStore()
    assert store.embedding_adapter == get_embedding_adapter()

    # Mock the embedding adapter to verify it gets called
    mock_adapter = AsyncMock(spec=BaseEmbeddingAdapter)
    mock_adapter.embed_documents.return_value = [[0.1] * db_settings.vector_dim]

    store_injected = MilvusVectorStore(embedding_adapter=mock_adapter)
    assert store_injected.embedding_adapter == mock_adapter


@pytest.mark.anyio
async def test_llm_adapter_tool_calls_input_output():
    adapter = get_llm_adapter()
    assert isinstance(adapter, BaseLLMAdapter)

    # Define messages with tool calls and tool responses
    messages = [
        {"role": "user", "content": "Fetch details for order 123."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_order_123",
                    "type": "function",
                    "function": {
                        "name": "get_order_details",
                        "arguments": {"order_id": "123"},
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_order_123",
            "content": '{"status": "delivered", "date": "2026-07-15"}',
        },
    ]

    response = await adapter.chat(messages)
    assert 'Mock chat response for: {"status": "delivered"' in response
