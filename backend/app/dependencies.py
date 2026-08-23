from functools import lru_cache

from app.config import get_settings
from app.services.embeddings import EmbeddingService
from app.services.gemini import GeminiService
from app.services.rag import RagService
from app.services.search import SearchService


@lru_cache
def get_rag_service() -> RagService:
    settings = get_settings()
    return RagService(
        settings=settings,
        embeddings=EmbeddingService(settings),
        search=SearchService(settings),
        gemini=GeminiService(settings),
    )
