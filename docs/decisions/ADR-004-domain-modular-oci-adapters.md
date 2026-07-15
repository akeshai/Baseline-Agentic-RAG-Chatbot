# ADR-004: Domain-Modular OCI LLM and Embedding Adapters

## Status
Accepted

## Context
The chatbot application requires integration with Large Language Models (LLMs) for text generation/chat and Embedding models for vector search. To support scalability and vendor flexibility, the design must meet the following criteria:
1. **Low Coupling & Modularity**: Avoid embedding third-party API SDK dependencies directly into core controllers or database engines (like `PGVectorStore`).
2. **Offline Local Development**: Run the unit test suite offline without OCI network dependencies or loading nonexistent local OCI key configurations.
3. **Dedicated & On-Demand Support**: Support both OCI on-demand public models (like Cohere) and fine-tuned custom models deployed on dedicated AI clusters (such as `openai.gpt-oss-20b`).
4. **Asymmetric Vector Embeddings**: Support asymmetric embedding types (`SEARCH_DOCUMENT` and `SEARCH_QUERY`) required for Cohere v3 models to ensure high cosine-similarity retrieval accuracy.

## Options Considered
* **Option 1: Inlined SDK Calls**: Directly call the OCI Generative AI client inside endpoints and `PGVectorStore`.
* **Option 2: Domain-Modular Adapters with Constructor Injection**: Create abstract interfaces (`BaseLLMAdapter`, `BaseEmbeddingAdapter`) and self-contained packages under `app/llm/` and `app/embeddings/`. Inject these adapters into classes (like `PGVectorStore`) requiring them.

## Decision
We selected **Option 2: Domain-Modular Adapters with Constructor Injection**.

### 1. Abstract Adapter Interfaces
* **LLM Interface (`app/llm/base.py`)**: Defines standard `generate` and `chat` operations.
* **Embeddings Interface (`app/embeddings/base.py`)**: Defines standard `embed_query` and `embed_documents` operations.

### 2. Concrete OCI Adapters
* **OCI LLM Adapter (`app/llm/oci.py`)**:
  - Automatically checks model ID prefix to route queries to `DedicatedServingMode` (for custom endpoints) or `OnDemandServingMode` (for public models).
  - Routes text generation completions to the chat endpoint for custom/instruct-tuned models to prevent 500 service errors.
  - Parses OCI SDK generic responses via `.chat_response.choices`.
* **OCI Embedding Adapter (`app/embeddings/oci.py`)**:
  - Directs batch document embedding queries using the `SEARCH_DOCUMENT` input type.
  - Directs user search query calls using the `SEARCH_QUERY` input type.
* **Factory Singletons**: `get_llm_adapter()` and `get_embedding_adapter()` act as unified provider endpoints.

### 3. Dependency Injection
* `PGVectorStore` receives a `BaseEmbeddingAdapter` via its constructor, removing dependencies on OCI models or credentials from the database layer.

### 4. Testing & Bypassing Mocks
* By default, an autouse session fixture `mock_oci_client` intercepting SDK calls permits fast offline testing.
* Integration tests targeting live OCI endpoints (`tests/test_oci_embeddings.py` and `tests/test_oci_llm.py`) are skipped unless the environment variable `MODE` is set to `"DEBUG"`. These tests carry the `@pytest.mark.real_oci` marker which signals `conftest.py` to bypass global mock patching.

## Consequences
* **Pros**:
  - Easily swap OCI for alternate providers (OpenAI, HuggingFace, etc.) in the future without modifying core business code or database classes.
  - Excellent search similarity metrics by adhering to Cohere's asymmetric embedding inputs.
  - Solid developer feedback loops with both offline mock testing and live debugging.
* **Cons**:
  - Slight initial setup overhead due to interfaces and constructor routing.
