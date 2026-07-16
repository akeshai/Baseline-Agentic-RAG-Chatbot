import logging
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.models import User
from app.auth.routes import get_current_user
from sqlalchemy import select
from app.database import SessionLocal
from app.ingest.models import DocumentChunk, DocumentVersion, IngestedDocument
from app.search.schemas import SearchRequest, SearchResponse, SearchResultItem
from app.vector_store import PGVectorStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["Search"])
vector_store = PGVectorStore()


@router.post(
    "",
    response_model=SearchResponse,
    status_code=status.HTTP_200_OK,
)
async def perform_similarity_search(
    req: SearchRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Accepts search query and/or list of chunk IDs, and returns matched chunks + parent context.
    """
    try:
        # Case A: Retrieve specific chunk IDs and parent info directly
        if req.chunk_ids:
            results = []
            async with SessionLocal() as db:
                stmt = (
                    select(
                        DocumentChunk.id,
                        DocumentChunk.content,
                        IngestedDocument.id.label("document_id"),
                        IngestedDocument.title,
                        IngestedDocument.source_identifier,
                        DocumentChunk.chunk_index,
                        DocumentChunk.version_id,
                        DocumentChunk.chunk_metadata,
                    )
                    .join(
                        DocumentVersion, DocumentChunk.version_id == DocumentVersion.id
                    )
                    .join(
                        IngestedDocument,
                        DocumentVersion.document_id == IngestedDocument.id,
                    )
                    .where(DocumentChunk.id.in_(req.chunk_ids))
                )
                res = await db.execute(stmt)
                for row in res.all():
                    chunk_id = row[0]
                    content = row[1]
                    doc_id = row[2]
                    title = row[3]
                    source = row[4]
                    chunk_index = row[5]
                    version_id = row[6]
                    meta = row[7] or {}

                    # Resolve full table if requested and chunk is a table row
                    if (
                        req.resolve_full_tables
                        and meta.get("type") == "table_row"
                        and "table_index" in meta
                    ):
                        table_index = meta["table_index"]
                        stmt_siblings = (
                            select(DocumentChunk.content)
                            .where(
                                (DocumentChunk.version_id == version_id)
                                & (
                                    DocumentChunk.chunk_metadata["type"].as_string()
                                    == "table_row"
                                )
                                & (
                                    DocumentChunk.chunk_metadata[
                                        "table_index"
                                    ].as_integer()
                                    == table_index
                                )
                            )
                            .order_by(
                                DocumentChunk.chunk_metadata["row_index"]
                                .as_integer()
                                .asc()
                            )
                        )
                        res_siblings = await db.execute(stmt_siblings)
                        sibling_contents = [r[0] for r in res_siblings.all()]

                        if sibling_contents:
                            first_content = sibling_contents[0]
                            parts = first_content.split("\nRow ")
                            context_str = parts[0]

                            headers = None
                            dividers = None
                            data_rows = []

                            for sc in sibling_contents:
                                lines = sc.split("\n")
                                table_lines = [
                                    line.strip()
                                    for line in lines
                                    if line.strip().startswith("|")
                                ]
                                if len(table_lines) >= 3:
                                    if headers is None:
                                        headers = table_lines[0]
                                        dividers = table_lines[1]
                                    data_rows.append(table_lines[2])

                            if headers and dividers and data_rows:
                                table_md = "\n".join([headers, dividers] + data_rows)
                                content = f"{context_str}\nFull Table:\n{table_md}"
                            else:
                                content = "\n\n".join(sibling_contents)

                    results.append(
                        SearchResultItem(
                            id=chunk_id,
                            content=content,
                            score=1.0,  # Static score for direct ID fetch
                            title=title,
                            source=source,
                            metadata={
                                "document_id": doc_id,
                                "chunk_index": chunk_index,
                                **meta,
                            },
                        )
                    )
            return SearchResponse(results=results)

        # Case B: Standard similarity query search
        if not req.query:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either 'query' or 'chunk_ids' must be provided.",
            )

        limit = req.limit or 5
        raw_results = await vector_store.query_similarity(
            query_text=req.query, limit=limit
        )

        resolved_results = []
        if req.resolve_full_tables:
            async with SessionLocal() as db:
                for item in raw_results:
                    meta = item.get("metadata") or {}
                    version_id = item.get("version_id")
                    content = item["content"]

                    if (
                        meta.get("type") == "table_row"
                        and version_id is not None
                        and "table_index" in meta
                    ):
                        table_index = meta["table_index"]
                        stmt_siblings = (
                            select(DocumentChunk.content)
                            .where(
                                (DocumentChunk.version_id == version_id)
                                & (
                                    DocumentChunk.chunk_metadata["type"].as_string()
                                    == "table_row"
                                )
                                & (
                                    DocumentChunk.chunk_metadata[
                                        "table_index"
                                    ].as_integer()
                                    == table_index
                                )
                            )
                            .order_by(
                                DocumentChunk.chunk_metadata["row_index"]
                                .as_integer()
                                .asc()
                            )
                        )
                        res_siblings = await db.execute(stmt_siblings)
                        sibling_contents = [r[0] for r in res_siblings.all()]

                        if sibling_contents:
                            first_content = sibling_contents[0]
                            parts = first_content.split("\nRow ")
                            context_str = parts[0]

                            headers = None
                            dividers = None
                            data_rows = []

                            for sc in sibling_contents:
                                lines = sc.split("\n")
                                table_lines = [
                                    line.strip()
                                    for line in lines
                                    if line.strip().startswith("|")
                                ]
                                if len(table_lines) >= 3:
                                    if headers is None:
                                        headers = table_lines[0]
                                        dividers = table_lines[1]
                                    data_rows.append(table_lines[2])

                            if headers and dividers and data_rows:
                                table_md = "\n".join([headers, dividers] + data_rows)
                                content = f"{context_str}\nFull Table:\n{table_md}"
                            else:
                                content = "\n\n".join(sibling_contents)

                    resolved_results.append(
                        SearchResultItem(
                            id=item.get("id"),
                            content=content,
                            score=item["score"],
                            title=item["title"],
                            source=item["source"],
                            metadata=meta,
                        )
                    )
        else:
            resolved_results = [
                SearchResultItem(
                    id=res.get("id"),
                    content=res["content"],
                    score=res["score"],
                    title=res["title"],
                    source=res["source"],
                    metadata=res.get("metadata"),
                )
                for res in raw_results
            ]

        return SearchResponse(results=resolved_results)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Search failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {e}",
        )
