from typing import Annotated

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.dependencies import get_rag_service
from app.errors import AppError
from app.schemas import ChatRequest, ChatResponse
from app.services.rag import RagService

router = APIRouter(prefix="/api", tags=["chat"])
RagDependency = Annotated[RagService, Depends(get_rag_service)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    rag: RagDependency,
    settings: SettingsDependency,
) -> ChatResponse:
    total_chars = sum(len(message.content) for message in request.messages)
    if total_chars > settings.max_message_chars:
        raise AppError("REQUEST_TOO_LARGE", "The conversation is too long.", 413)
    return await rag.answer(request.messages)
