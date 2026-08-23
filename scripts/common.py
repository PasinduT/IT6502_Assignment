from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from dotenv import load_dotenv
from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "backend" / ".env")


@dataclass(frozen=True)
class IngestionConfig:
    gemini_api_key: str
    embedding_model: str
    embedding_dimensions: int
    search_endpoint: str
    search_index: str
    search_key: str
    chunk_chars: int
    chunk_overlap: int

    @classmethod
    def from_env(cls) -> IngestionConfig:
        config = cls(
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            embedding_model=os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-2"),
            embedding_dimensions=int(os.getenv("EMBEDDING_DIMENSIONS", "768")),
            search_endpoint=os.getenv("AZURE_SEARCH_ENDPOINT", ""),
            search_index=os.getenv("AZURE_SEARCH_INDEX", "tax-assistant"),
            search_key=os.getenv("AZURE_SEARCH_KEY", ""),
            chunk_chars=int(os.getenv("CHUNK_CHARS", "2400")),
            chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "300")),
        )
        missing = [
            name
            for name, value in {
                "GEMINI_API_KEY": config.gemini_api_key,
                "AZURE_SEARCH_ENDPOINT": config.search_endpoint,
                "AZURE_SEARCH_KEY": config.search_key,
            }.items()
            if not value
        ]
        if missing:
            sys.exit(f"Missing required configuration: {', '.join(missing)}")
        if config.chunk_overlap >= config.chunk_chars:
            sys.exit("CHUNK_OVERLAP must be smaller than CHUNK_CHARS")
        return config


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if not normalized:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + size, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind("\n", start + size // 2, end)
            if boundary > start:
                end = boundary
        chunks.append(normalized[start:end].strip())
        if end >= len(normalized):
            break
        start = max(start + 1, end - overlap)
    return chunks


def stable_id(source_id: str, position: str, content: str) -> str:
    digest = hashlib.sha256(f"{source_id}:{position}:{content}".encode()).hexdigest()[:24]
    return f"{source_id}-{digest}".replace("/", "-")


def iso_datetime(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    return text if "T" in text else f"{text}T00:00:00Z"


def embed_and_upload(records: list[dict[str, Any]], config: IngestionConfig) -> tuple[int, int]:
    if not records:
        return 0, 0
    genai_client = genai.Client(api_key=config.gemini_api_key)
    search_client = SearchClient(
        config.search_endpoint, config.search_index, AzureKeyCredential(config.search_key)
    )
    uploaded = failed = 0
    for batch_start in range(0, len(records), 16):
        batch = records[batch_start : batch_start + 16]
        response = genai_client.models.embed_content(
            model=config.embedding_model,
            contents=[record["content"] for record in batch],
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_DOCUMENT",
                output_dimensionality=config.embedding_dimensions,
            ),
        )
        if not response.embeddings or len(response.embeddings) != len(batch):
            raise RuntimeError("Embedding count did not match the batch size")
        for record, embedding in zip(batch, response.embeddings, strict=True):
            record["embedding"] = list(embedding.values or [])
        results = search_client.merge_or_upload_documents(batch)
        uploaded += sum(1 for result in results if result.succeeded)
        failed += sum(1 for result in results if not result.succeeded)
    search_client.close()
    return uploaded, failed


def require_lk(metadata: dict[str, Any], source_name: str) -> None:
    if metadata.get("jurisdiction") != "LK":
        raise ValueError(f"{source_name}: jurisdiction must be exactly 'LK'")


def batches(values: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]
