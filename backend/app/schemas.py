from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str

    @field_validator("content")
    @classmethod
    def content_is_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message content cannot be empty")
        return value


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=50)

    @field_validator("messages")
    @classmethod
    def last_message_is_user(cls, messages: list[ChatMessage]) -> list[ChatMessage]:
        if messages and messages[-1].role != "user":
            raise ValueError("the last message must have role 'user'")
        return messages


class Citation(BaseModel):
    id: str
    title: str
    document_type: str | None = None
    section: str | None = None
    page: int | None = None
    page_end: int | None = None
    sheet: str | None = None
    cell_range: str | None = None
    authority_level: str | None = None
    status: str | None = None
    source_id: str | None = None
    published_date: date | None = None
    effective_from: date | None = None
    tax_year: str | None = None
    url: str | None = None


class GuideImage(BaseModel):
    id: str
    url: str
    alt: str
    caption: str | None = None
    source_id: str
    page: int | None = None


class GuideStep(BaseModel):
    number: int
    title: str
    instruction: str
    image: GuideImage | None = None
    citation_ids: list[str] = Field(default_factory=list)


class Guide(BaseModel):
    title: str
    steps: list[GuideStep]

    @field_validator("steps")
    @classmethod
    def steps_are_contiguous(cls, steps: list[GuideStep]) -> list[GuideStep]:
        expected = list(range(1, len(steps) + 1))
        if [step.number for step in steps] != expected:
            raise ValueError("guide step numbers must be contiguous and start at one")
        return steps


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    guide: Guide | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
