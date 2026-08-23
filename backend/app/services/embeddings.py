from google import genai
from google.genai import types

from app.config import Settings
from app.errors import AppError


class EmbeddingService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: genai.Client | None = None

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=self.settings.gemini_api_key)
        return self._client

    async def embed_query(self, text: str) -> list[float]:
        try:
            response = await self.client.aio.models.embed_content(
                model=self.settings.gemini_embedding_model,
                contents=text,
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_QUERY",
                    output_dimensionality=self.settings.embedding_dimensions,
                ),
            )
            if not response.embeddings or not response.embeddings[0].values:
                raise ValueError("embedding provider returned no vector")
            return list(response.embeddings[0].values)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "EMBEDDING_ERROR", "The assistant could not process this question.", 503
            ) from exc
