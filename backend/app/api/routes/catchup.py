from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.memory import CatchUpRequest, CatchUpResponse
from app.services.extraction import catch_me_up
from app.services.response_router import format_catchup_for_platform

router = APIRouter()


@router.post("/catchup", response_model=dict)
async def catchup(body: CatchUpRequest, db: AsyncSession = Depends(get_db)):
    since = None
    if body.since:
        since = datetime.fromisoformat(body.since.replace("Z", "+00:00"))
    recap: CatchUpResponse = await catch_me_up(
        db,
        body.user_id,
        user_name=body.user_name,
        since=since,
    )
    return {
        "type": "catchup",
        "formatted": format_catchup_for_platform(recap),
        "data": recap.model_dump(mode="json"),
    }
