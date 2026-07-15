import json
from datetime import datetime
from typing import List, Optional
from sqlalchemy import ForeignKey, String, func, TEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator
from app.database import Base
from app.configs.dbs import settings as db_settings



class SafeVector(TypeDecorator):
    """
    Custom SQLAlchemy type that compiles to PGVector's Vector type on PostgreSQL,
    and falls back to TEXT containing serialized JSON arrays on SQLite.
    """
    impl = TEXT
    cache_ok = True

    def __init__(self, dimensions: int):
        super().__init__()
        self.dimensions = dimensions

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            try:
                from pgvector.sqlalchemy import Vector
                return dialect.type_descriptor(Vector(self.dimensions))
            except ImportError:
                return dialect.type_descriptor(TEXT())
        else:
            return dialect.type_descriptor(TEXT())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return list(value)
        try:
            return json.loads(value)
        except Exception:
            return []


class IngestedDocument(Base):
    __tablename__ = "ingested_documents"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    source_type: Mapped[str] = mapped_column(nullable=False)  # "url", "manual_file", "manual_text"
    source_identifier: Mapped[str] = mapped_column(String(512), unique=True, index=True, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(nullable=True)
    current_version: Mapped[int] = mapped_column(nullable=False, default=1)
    current_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256 content hash
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, default=func.now(), onupdate=func.now()
    )

    # Relationship to document versions
    versions: Mapped[List["DocumentVersion"]] = relationship(
        "DocumentVersion", back_populates="document", cascade="all, delete-orphan"
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("ingested_documents.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_storage_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    text_content: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, default="active")  # "active", "superseded"
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=func.now())

    # Back relationships
    document: Mapped["IngestedDocument"] = relationship(
        "IngestedDocument", back_populates="versions"
    )
    chunks: Mapped[List["DocumentChunk"]] = relationship(
        "DocumentChunk", back_populates="version", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(nullable=False)
    embedding: Mapped[List[float]] = mapped_column(SafeVector(db_settings.vector_dim), nullable=False)

    # Relationship to version
    version: Mapped["DocumentVersion"] = relationship(
        "DocumentVersion", back_populates="chunks"
    )
