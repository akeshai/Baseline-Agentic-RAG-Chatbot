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
async def test_real_oci_embeddings():
    """
    Verifies connection to the actual OCI Generative AI service.
    Bypasses unit mocks and executes live embed queries.
    """
    from app.embeddings.oci import OCIEmbeddingAdapter

    adapter = OCIEmbeddingAdapter()

    test_text = "Hello world! Testing real OCI Generative AI embeddings in unit test."
    vector = await adapter.embed_query(test_text)

    assert len(vector) == 1024
    assert all(isinstance(val, float) for val in vector)
