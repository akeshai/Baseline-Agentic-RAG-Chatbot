from typing import Any, List, Optional
from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """
    Search request body supporting query similarity search and/or direct chunk ID lookups.
    """

    query: Optional[str] = Field(
        default=None, description="The query string to run similarity search on"
    )
    chunk_ids: Optional[List[int]] = Field(
        default=None,
        description="Optional list of chunk IDs to retrieve parent context for",
    )
    limit: Optional[int] = Field(
        default=5,
        description="Number of results to retrieve (applicable for query search)",
    )
    resolve_full_tables: Optional[bool] = Field(
        default=False,
        description="If True, resolves and reconstructs full Markdown tables for any matched row chunks",
    )


class SearchResultItem(BaseModel):
    """
    Indicates a single search result matching the query or ID lookup.
    """

    id: Optional[Any] = Field(
        default=None, description="The unique ID of the text chunk"
    )
    content: str = Field(..., description="The textual content of the chunk")
    score: float = Field(..., description="Similarity score or ranking weight")
    title: Optional[str] = Field(
        default=None, description="The parent document's title"
    )
    source: str = Field(..., description="The source identifier or URL of the document")
    metadata: Optional[dict] = Field(
        default=None, description="Optional metadata (e.g. document_id, chunk_index)"
    )


class SearchResponse(BaseModel):
    """
    Wrapper search response containing matched search results list.
    """

    results: List[SearchResultItem]
