import logging

from fastapi import APIRouter, HTTPException
from app.schemas import ReviewRequest, ReviewResponse
from app.services.reviewer import review_code

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/review", response_model=ReviewResponse)
async def review_code_endpoint(payload: ReviewRequest):
    try:
        return await review_code(payload)
    except Exception as exc:
        logger.exception("Code review failed")
        raise HTTPException(status_code=500, detail="Review failed. Please try again.") from exc
