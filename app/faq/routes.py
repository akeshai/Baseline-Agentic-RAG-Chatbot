import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from app.auth.models import User
from app.auth.routes import get_current_user
from app.faq.repository import FAQRepository
from app.configs.yaml_loader import categories_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/faq", tags=["FAQ"])
repo = FAQRepository()


@router.get(
    "/search",
    status_code=status.HTTP_200_OK,
)
async def search_faq(
    question: str = Query(..., description="Question text to search"),
    current_user: User = Depends(get_current_user),
):
    """
    Search FAQ by exact/slugified question text.
    Runs in O(1) time.
    """
    if not question.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Question cannot be empty"
        )
    
    faq = await repo.get_faq_by_question(question)
    if not faq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No FAQ found for: {question}"
        )
    return {"success": True, "faq": faq}


@router.get(
    "/categories",
    status_code=status.HTTP_200_OK,
)
async def get_all_categories(
    current_user: User = Depends(get_current_user),
):
    """
    Get summary and counts of all categories.
    """
    try:
        summary = await repo.get_category_summary()
        # Compile detail list with description
        categories_list = []
        for cat in categories_config:
            cat_id = cat["id"]
            categories_list.append({
                "id": cat_id,
                "description": cat.get("description", "").strip(),
                "count": summary.get(cat_id, 0)
            })
            
        total_faqs = sum(summary.values())
        return {
            "success": True,
            "categories": categories_list,
            "total_categories": len(categories_list),
            "total_faqs": total_faqs,
            "message": f"Found {len(categories_list)} categories with {total_faqs} FAQs"
        }
    except Exception as e:
        logger.error("Failed to retrieve categories: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get(
    "/categories/{category}/questions",
    status_code=status.HTTP_200_OK,
)
async def get_category_questions(
    category: str,
    include_full_data: bool = Query(False, description="If true, return full FAQ data"),
    current_user: User = Depends(get_current_user),
):
    """
    Get all questions/FAQs in a category.
    """
    if not category.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Category cannot be empty"
        )
        
    # Check if category is valid in configurations
    valid_categories = [cat["id"] for cat in categories_config]
    if category not in valid_categories:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category '{category}' is not configured"
        )
        
    faqs = await repo.get_questions_by_category(category)
    if not faqs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No FAQs found in category: {category}"
        )
        
    if include_full_data:
        questions_data = faqs
    else:
        # Only return the list of question texts
        questions_data = [faq["question"] for faq in faqs]
        
    return {
        "success": True,
        "category": category,
        "count": len(faqs),
        "include_full_data": include_full_data,
        "questions": questions_data
    }


@router.delete(
    "/categories/{category}",
    status_code=status.HTTP_200_OK,
)
async def delete_category(
    category: str,
    current_user: User = Depends(get_current_user),
):
    """
    Delete all FAQs in a category.
    """
    if not category.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Category cannot be empty"
        )
        
    # Check if category is valid in configurations
    valid_categories = [cat["id"] for cat in categories_config]
    if category not in valid_categories:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category '{category}' is not configured"
        )
        
    success = await repo.delete_faqs_by_category(category)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete category: {category}"
        )
        
    return {
        "success": True,
        "category": category,
        "message": f"All FAQs in category '{category}' have been deleted"
    }
