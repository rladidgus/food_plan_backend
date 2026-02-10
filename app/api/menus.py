"""Menu collection APIs for Node B."""

from __future__ import annotations

from datetime import datetime, timezone
import time
import json
from typing import Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import (
    MenuCollectionFailure,
    MenuCollectionJob,
    MenuItem,
    NutritionFacts,
    Restaurant,
)
from app.services.menu_collection import collect_menus_for_restaurants


router = APIRouter()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MenuCollectRequest(BaseModel):
    restaurant_ids: List[int] = Field(..., min_items=1, max_items=100)
    collection_method_priority: List[str] = Field(
        default_factory=lambda: ["SCRAPING", "LLM", "API"]
    )
    rate_limit_ms: int = Field(800, ge=0, le=5000)
    naver_validation: bool = True
    llm_fallback: bool = True

    @validator("collection_method_priority")
    def _validate_priority(cls, value: List[str]) -> List[str]:
        allowed = {"SCRAPING", "LLM", "API"}
        for item in value:
            if item not in allowed:
                raise ValueError("collection_method_priority must be subset of SCRAPING/LLM/API")
        return value


class MenuCollectResponse(BaseModel):
    job_id: str
    status: str
    requested_count: int
    created_at: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    requested_count: int
    success_count: int
    failure_count: int
    started_at: Optional[str] = None
    updated_at: Optional[str] = None
    errors: List[str] = []


class MenuItemOut(BaseModel):
    menu_id: int
    name: str
    description: Optional[str] = None
    price: Optional[float] = None
    collection_method: Optional[str] = None
    source_url: Optional[str] = None
    confidence: Optional[float] = None


class RestaurantMenusResponse(BaseModel):
    restaurant_id: int
    menus: List[MenuItemOut]
    updated_at: Optional[str] = None


class JobFailuresResponse(BaseModel):
    job_id: str
    failures: List[Dict[str, str]]


def _run_job(
    job_id: str,
    restaurant_ids: List[int],
    method_priority: List[str],
    rate_limit_ms: int,
    naver_validation: bool,
    llm_fallback: bool,
) -> None:
    db = SessionLocal()
    try:
        job = db.query(MenuCollectionJob).filter(MenuCollectionJob.job_id == job_id).one()
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        db.commit()

        for restaurant_id in restaurant_ids:
            menu_item_ids, failures = collect_menus_for_restaurants(
                db,
                [restaurant_id],
                method_priority,
                llm_fallback,
                naver_validation,
            )
            if menu_item_ids:
                job.success_count += 1
            if failures:
                job.failure_count += 1
                for failure in failures:
                    db.add(
                        MenuCollectionFailure(
                            job_id=job_id,
                            restaurant_id=int(failure.get("restaurant_id") or 0) or None,
                            stage=failure.get("stage") or "COLLECT",
                            reason=failure.get("reason") or "unknown",
                            detail=failure.get("detail"),
                        )
                    )
            job.updated_at = datetime.now(timezone.utc)
            db.commit()

            if rate_limit_ms:
                time.sleep(rate_limit_ms / 1000.0)

        if job.success_count == 0 and job.failure_count > 0:
            job.status = "failed"
        elif job.failure_count > 0:
            job.status = "partial"
        else:
            job.status = "completed"
        job.updated_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


@router.post(
    "/menus/collect",
    response_model=MenuCollectResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def collect_menus(
    request: MenuCollectRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> MenuCollectResponse:
    job_id = f"bmenu-{uuid4().hex[:8]}"
    job = MenuCollectionJob(
        job_id=job_id,
        status="queued",
        requested_count=len(request.restaurant_ids),
        request_payload=json.dumps(request.dict()),
    )
    db.add(job)
    db.commit()

    background_tasks.add_task(
        _run_job,
        job_id,
        request.restaurant_ids,
        request.collection_method_priority,
        request.rate_limit_ms,
        request.naver_validation,
        request.llm_fallback,
    )

    return MenuCollectResponse(
        job_id=job.job_id,
        status=job.status,
        requested_count=job.requested_count,
        created_at=job.created_at.isoformat(),
    )


@router.get("/menus/collect/{job_id}", response_model=JobStatusResponse)
def get_collect_status(job_id: str) -> JobStatusResponse:
    db = SessionLocal()
    try:
        job = db.query(MenuCollectionJob).filter(MenuCollectionJob.job_id == job_id).one_or_none()
        if job is None:
            raise HTTPException(status_code=404, detail="job_id not found")
        return JobStatusResponse(
            job_id=job.job_id,
            status=job.status,
            requested_count=job.requested_count,
            success_count=job.success_count,
            failure_count=job.failure_count,
            started_at=job.started_at.isoformat() if job.started_at else None,
            updated_at=job.updated_at.isoformat() if job.updated_at else None,
            errors=json.loads(job.errors) if job.errors else [],
        )
    finally:
        db.close()


@router.get("/menus/collect/{job_id}/failures", response_model=JobFailuresResponse)
def get_collect_failures(job_id: str) -> JobFailuresResponse:
    db = SessionLocal()
    try:
        job = db.query(MenuCollectionJob).filter(MenuCollectionJob.job_id == job_id).one_or_none()
        if job is None:
            raise HTTPException(status_code=404, detail="job_id not found")
        failures = (
            db.query(MenuCollectionFailure)
            .filter(MenuCollectionFailure.job_id == job_id)
            .order_by(MenuCollectionFailure.failure_id.asc())
            .all()
        )
        return JobFailuresResponse(
            job_id=job.job_id,
            failures=[
                {
                    "restaurant_id": str(failure.restaurant_id) if failure.restaurant_id else "",
                    "stage": failure.stage,
                    "reason": failure.reason,
                    "detail": failure.detail or "",
                }
                for failure in failures
            ],
        )
    finally:
        db.close()


@router.get("/restaurants/{restaurant_id}/menus", response_model=RestaurantMenusResponse)
def get_restaurant_menus(
    restaurant_id: int,
    include_source: bool = Query(False),
    include_confidence: bool = Query(False),
    db: Session = Depends(get_db),
) -> RestaurantMenusResponse:
    restaurant = (
        db.query(Restaurant)
        .filter(Restaurant.restaurant_id == restaurant_id)
        .one_or_none()
    )
    if restaurant is None:
        raise HTTPException(status_code=404, detail="restaurant_id not found")

    menus = (
        db.query(MenuItem)
        .filter(MenuItem.restaurant_id == restaurant_id)
        .order_by(MenuItem.menu_id.asc())
        .all()
    )

    items: List[MenuItemOut] = []
    for menu in menus:
        confidence = None
        if include_confidence:
            confidence = (
                db.query(NutritionFacts.confidence)
                .filter(NutritionFacts.menu_item_id == menu.menu_id)
                .order_by(NutritionFacts.confidence.desc().nullslast())
                .limit(1)
                .scalar()
            )

        items.append(
            MenuItemOut(
                menu_id=menu.menu_id,
                name=menu.name,
                description=menu.description,
                price=menu.price,
                collection_method=menu.source if include_source else None,
                source_url=menu.source_url if include_source else None,
                confidence=confidence,
            )
        )

    return RestaurantMenusResponse(
        restaurant_id=restaurant_id,
        menus=items,
        updated_at=_now_iso(),
    )


__all__ = ["router"]
