import os
import pytest

# Retrieve mode defaulting to STAGING
mode = os.getenv("MODE", "STAGING").strip("'\" ").upper()


@pytest.mark.real_oci
@pytest.mark.skipif(
    mode != "DEBUG",
    reason="Runs only when MODE=DEBUG in environment (default is STAGING)",
)
@pytest.mark.anyio
async def test_real_oci_llm_generate():
    """
    Verifies connection to the actual OCI Generative AI text generation service.
    Bypasses unit mocks and executes live completion queries.
    """
    from app.llm.oci import OCILLMAdapter

    adapter = OCILLMAdapter()

    test_prompt = "Tell me a joke in one sentence."
    response = await adapter.generate(test_prompt, max_tokens=600)

    assert isinstance(response, str)
    assert len(response) > 0


@pytest.mark.real_oci
@pytest.mark.skipif(
    mode != "DEBUG",
    reason="Runs only when MODE=DEBUG in environment (default is STAGING)",
)
@pytest.mark.anyio
async def test_real_oci_llm_chat():
    """
    Verifies connection to the actual OCI Generative AI chat service.
    Bypasses unit mocks and executes live chat sessions.
    """
    from app.llm.oci import OCILLMAdapter

    adapter = OCILLMAdapter()

    messages = [{"role": "user", "content": "Hello! Introduce yourself in 5 words."}]
    response = await adapter.chat(messages, max_tokens=600)

    assert isinstance(response, str)
    assert len(response) > 0
