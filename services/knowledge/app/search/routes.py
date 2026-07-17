import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.asynchronous.database import AsyncDatabase

from app.auth.models import User
from app.auth.routes import get_current_user
from app.mongo import get_mongo_db
from app.search.schemas import SearchRequest, SearchResponse, SearchResultItem
from app.search.service import SearchService
from app.vector_store import get_vector_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["Knowledge"])
search_service = SearchService(vector_store=get_vector_store())


@router.post(
    "/search",
    response_model=SearchResponse,
    status_code=status.HTTP_200_OK,
)
async def perform_similarity_search(
    req: SearchRequest,
    db: AsyncDatabase = Depends(get_mongo_db),
    current_user: User = Depends(get_current_user),
):
    """
    Accepts search query and/or list of chunk IDs, and returns matched chunks + parent context.
    """
    try:
        # Case A: Retrieve specific chunk IDs
        if req.chunk_ids:
            raw = await search_service.search_by_chunk_ids(
                chunk_ids=req.chunk_ids,
                db=db,
                resolve_full_tables=req.resolve_full_tables or False,
            )
            results = [SearchResultItem(**item) for item in raw]
            return SearchResponse(results=results)

        # Case B: Similarity query search
        if not req.query:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either 'query' or 'chunk_ids' must be provided.",
            )

        raw = await search_service.search_by_query(
            query_text=req.query,
            db=db,
            limit=req.limit or 5,
            resolve_full_tables=req.resolve_full_tables or False,
        )
        results = [SearchResultItem(**item) for item in raw]
        return SearchResponse(results=results)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Search failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {e}",
        )
