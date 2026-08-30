from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    environment: Literal["development", "test", "production"] = "development"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_embedding_model: str = "gemini-embedding-2"
    gemini_timeout_seconds: int = Field(default=10, ge=1, le=120)
    gemini_max_output_tokens: int = Field(default=1024, ge=64, le=8192)
    embedding_dimensions: int = Field(default=768, ge=1, le=3072)
    azure_search_endpoint: str = ""
    azure_search_index: str = "tax-assistant"
    azure_search_key: str = ""
    azure_storage_account_url: str = ""
    azure_storage_container: str = "tax-source-documents"
    frontend_origin: str = "http://localhost:5173"
    # Public, approved guide media is served from a separate origin.  Keep this empty by
    # default so image responses stay disabled until an explicit trusted origin is configured.
    guide_media_base_url: str = ""
    rag_top_k: int = Field(default=6, ge=1, le=24)
    # Azure hybrid search returns reciprocal-rank-fusion (RRF) scores, which are
    # commonly around 0.01–0.03 rather than normalized cosine similarities.
    # Keep the default below the observed relevant range; deployments can still
    # tune this through RAG_MIN_SCORE after evaluating their corpus.
    rag_min_score: float = Field(default=0.01, ge=0, le=1)
    # Retrieval fan-out is intentionally separate from the final context size.  The larger
    # initial set gives deterministic reranking enough candidates for source diversity.
    rag_initial_top: int = Field(default=24, ge=1, le=100)
    rag_vector_k: int = Field(default=24, ge=1, le=100)
    rag_image_top_k: int = Field(default=8, ge=1, le=50)
    rag_max_evidence: int = Field(default=8, ge=1, le=24)
    rag_max_images: int = Field(default=4, ge=1, le=8)
    max_message_chars: int = Field(default=8000, ge=100, le=50_000)
    max_history_messages: int = Field(default=12, ge=1, le=50)

    @field_validator("frontend_origin")
    @classmethod
    def clean_origin(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("guide_media_base_url")
    @classmethod
    def clean_guide_media_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def cors_origins(self) -> list[str]:
        origins = {self.frontend_origin}
        if self.environment != "production":
            origins.update({"http://localhost:5173", "http://127.0.0.1:5173"})
        return sorted(origin for origin in origins if origin)

    @property
    def providers_configured(self) -> bool:
        return self.gemini_configured and self.search_configured

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def search_configured(self) -> bool:
        return bool(self.azure_search_endpoint and self.azure_search_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
