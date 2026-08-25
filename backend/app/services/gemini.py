from google import genai
from google.genai import types

from app.config import Settings
from app.errors import AppError
from app.services.gemini_errors import translate_gemini_error


class GeminiService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: genai.Client | None = None

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(
                api_key=self.settings.gemini_api_key,
                http_options=types.HttpOptions(
                    timeout=self.settings.gemini_timeout_seconds * 1000,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        return self._client

    async def generate(self, system_prompt: str, prompt: str) -> str:
        try:
            response = await self.client.aio.models.generate_content(
                model=self.settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.1,
                    max_output_tokens=self.settings.gemini_max_output_tokens,
                    response_modalities=["TEXT"],
                    thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                ),
            )
            if not response.text:
                raise ValueError("generation provider returned no text")
            return response.text.strip()
        except AppError:
            raise
        except Exception as exc:
            raise translate_gemini_error(
                exc,
                "UPSTREAM_MODEL_ERROR",
                "The assistant is temporarily unavailable.",
            ) from exc
