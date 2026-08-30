import json
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.config import Settings
from app.errors import AppError
from app.services.gemini_errors import translate_gemini_error


class GeneratedGuideStep(BaseModel):
    """The provider-facing representation of one optional guide step."""

    model_config = ConfigDict(extra="forbid")

    title: str
    instruction: str
    image_id: str | None = None
    citation_markers: list[str] = Field(default_factory=list)

    @field_validator("title", "instruction")
    @classmethod
    def text_is_present(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("guide step text cannot be empty")
        return value


class GeneratedGuide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    steps: list[GeneratedGuideStep] = Field(min_length=1)


class GeneratedAnswer(BaseModel):
    """Strict internal JSON contract returned by Gemini.

    This model is not exposed as the HTTP response schema. RAG maps marker references to
    retrieved, backend-owned citation and image metadata first.
    """

    model_config = ConfigDict(extra="forbid")

    answer: str
    guide: GeneratedGuide | None = None

    @field_validator("answer")
    @classmethod
    def answer_is_present(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("answer cannot be empty")
        return value


def parse_generated_answer(value: Any) -> GeneratedAnswer:
    """Parse a provider response into the strict internal answer contract."""

    try:
        if isinstance(value, GeneratedAnswer):
            return value
        if isinstance(value, BaseModel):
            return GeneratedAnswer.model_validate(value.model_dump())
        if isinstance(value, str):
            payload = json.loads(value)
        else:
            payload = value
        return GeneratedAnswer.model_validate(payload)
    except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        raise AppError(
            "UPSTREAM_MODEL_ERROR",
            "The assistant returned an invalid structured response.",
            503,
        ) from exc


def _gemini_response_schema() -> dict[str, Any]:
    schema = GeneratedAnswer.model_json_schema()

    # Gemini rejects JSON Schema's additionalProperties keyword; strict local
    # Pydantic validation below still rejects unexpected fields in the response.
    def remove_additional_properties(value: Any) -> None:
        if isinstance(value, dict):
            value.pop("additionalProperties", None)
            for child in value.values():
                remove_additional_properties(child)
        elif isinstance(value, list):
            for child in value:
                remove_additional_properties(child)

    remove_additional_properties(schema)
    return schema


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

    async def generate(self, system_prompt: str, prompt: str) -> GeneratedAnswer:
        try:
            response = await self.client.aio.models.generate_content(
                model=self.settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.1,
                    max_output_tokens=self.settings.gemini_max_output_tokens,
                    response_modalities=["TEXT"],
                    response_mime_type="application/json",
                    response_schema=_gemini_response_schema(),
                    thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                ),
            )
            parsed = getattr(response, "parsed", None)
            if parsed is not None:
                return parse_generated_answer(parsed)
            text = getattr(response, "text", None)
            if not text:
                raise ValueError("generation provider returned no structured text")
            return parse_generated_answer(text.strip())
        except AppError:
            raise
        except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            raise AppError(
                "UPSTREAM_MODEL_ERROR",
                "The assistant returned an invalid structured response.",
                503,
            ) from exc
        except Exception as exc:
            raise translate_gemini_error(
                exc,
                "UPSTREAM_MODEL_ERROR",
                "The assistant is temporarily unavailable.",
            ) from exc
