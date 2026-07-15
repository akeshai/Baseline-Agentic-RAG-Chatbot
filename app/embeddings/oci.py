import asyncio
import logging
from typing import List

import oci
from oci.generative_ai_inference import GenerativeAiInferenceClient
from oci.generative_ai_inference.models import (
    EmbedTextDetails,
    OnDemandServingMode,
)

from app.configs.llm import settings
from app.embeddings.base import BaseEmbeddingAdapter

logger = logging.getLogger(__name__)


class OCIEmbeddingAdapter(BaseEmbeddingAdapter):
    """
    OCI Generative AI implementation of BaseEmbeddingAdapter.
    Wraps synchronous OCI SDK calls in asyncio.to_thread to avoid blocking event loop.
    Supports asymmetric search types (SEARCH_DOCUMENT for chunks and SEARCH_QUERY for search queries).
    """

    def __init__(self):
        self._client = None

    @property
    def client(self) -> GenerativeAiInferenceClient:
        if self._client is None:
            # Load OCI config
            try:
                config = oci.config.from_file(
                    file_location=settings.oci_config_file,
                    profile_name=settings.oci_profile,
                )
            except Exception as e:
                logger.warning(
                    "Failed to load OCI config file. Attempting default fallback: %s", e
                )
                config = oci.config.from_file()

            # Initialize Inference Client
            self._client = GenerativeAiInferenceClient(
                config=config,
                service_endpoint=settings.oci_service_endpoint,
            )
        return self._client

    async def embed_query(self, text: str) -> List[float]:
        """
        Generates an embedding vector for a single query string using SEARCH_QUERY type.
        """
        if not text:
            return []

        embeddings = await self._embed_texts([text], input_type="SEARCH_QUERY")
        if embeddings:
            return embeddings[0]
        return []

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        Generates embedding vectors for multiple texts using SEARCH_DOCUMENT type in one batched call.
        """
        return await self._embed_texts(texts, input_type="SEARCH_DOCUMENT")

    async def _embed_texts(
        self, texts: List[str], input_type: str
    ) -> List[List[float]]:
        """
        Private helper executing text embedding generation under OCI client.
        """
        if not texts:
            return []

        details = EmbedTextDetails(
            inputs=texts,
            compartment_id=settings.oci_compartment_id,
            serving_mode=OnDemandServingMode(model_id=settings.oci_embedding_model_id),
            input_type=input_type,
        )

        response = await asyncio.to_thread(self.client.embed_text, details)

        try:
            return response.data.embeddings
        except AttributeError as e:
            logger.error(
                "Failed to parse OCI embedding response: %s. Response data: %s",
                e,
                response.data,
            )
            raise ValueError(
                "Error parsing embedding response from OCI Generative AI"
            ) from e
