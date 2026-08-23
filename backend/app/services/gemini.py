from google import genai
from google.genai import types

from app.config import Settings
from app.errors import AppError


class GeminiService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: genai.Client | None = None

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=self.settings.gemini_api_key)
        return self._client

    async def generate(self, system_prompt: str, prompt: str) -> str:
        try:
            response = await self.client.aio.models.generate_content(
                model=self.settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.1,
                    response_modalities=["TEXT"],
                ),
            )
            if not response.text:
                raise ValueError("generation provider returned no text")
            return response.text.strip()
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "UPSTREAM_MODEL_ERROR", "The assistant is temporarily unavailable.", 503
            ) from exc
