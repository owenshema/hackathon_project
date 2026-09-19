from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.memory import ChatRequest, MemoryAnswer, Platform
from app.services.response_router import handle_user_message

router = APIRouter()


@router.post("/chat", response_model=dict)
async def chat(body: ChatRequest, db: AsyncSession = Depends(get_db)):
    """Web / admin chat — same brain as WhatsApp & Teams."""
    result, formatted = await handle_user_message(
        db,
        text=body.question,
        user_id=body.user_id or "web-user",
        user_name=body.user_name,
        platform=body.platform or Platform.WEB,
        mode=body.mode,
    )
    if isinstance(result, MemoryAnswer):
        return {
            "type": "answer",
            "formatted": formatted,
            "data": result.model_dump(mode="json"),
        }
    return {
        "type": "catchup",
        "formatted": formatted,
        "data": result.model_dump(mode="json"),
    }
