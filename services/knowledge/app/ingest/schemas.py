from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class TextIngestRequest(BaseModel):
    """
    Schema for manual raw text ingestion payloads.
    """

    source_identifier: str = Field(
        ...,
        description="Unique URN or descriptive ID matching the text (e.g. manual://docs/rate_notes).",
    )
    title: Optional[str] = Field(
        None, description="Optional title description for the document."
    )
    text_content: str = Field(
        ..., description="The cleaned raw text content to ingest, chunk, and embed."
    )
    is_html: Optional[bool] = Field(
        default=False,
        description="If True, treats text as HTML and parses structured elements.",
    )


class IngestResponse(BaseModel):
    """
    Standard output payload for document ingestion requests.
    """

    document_id: str
    version: int
    action: str = Field(
        ..., description="The action executed: 'created', 'updated', or 'skipped'."
    )
    hash: str = Field(..., description="The SHA-256 content hash of the text payload.")


class DocumentVersionMetadata(BaseModel):
    id: str
    version: int
    content_hash: str
    raw_storage_key: Optional[str] = None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True}


class DocumentMetadataResponse(BaseModel):
    id: str
    source_type: str
    source_identifier: str
    title: Optional[str] = None
    current_version: int
    current_hash: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    versions: List[DocumentVersionMetadata]

    model_config = {"from_attributes": True, "populate_by_name": True}


class TaskMetadata(BaseModel):
    task_id: str
    status: str  # "pending_ingestion" or "ingested"
    created_at: datetime


class FileMetadata(BaseModel):
    filename: str
    status: str  # "pending_ingestion" or "ingested"
    last_modified_at: datetime


class IngestionStatusResponse(BaseModel):
    crawls: List[TaskMetadata]
    manual_files: List[FileMetadata]
