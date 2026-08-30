"""Embedding, cache, and scoped Azure Search upload helpers.

The functions in this module are provider-injectable so validation and cache behavior can be
verified offline.  No provider client is created until an upload is explicitly requested.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from scripts.common import IngestionConfig
from scripts.ingestion.embedding_cache import EmbeddingCache, content_hash, validate_vector
from scripts.ingestion.models import ChunkRecord

DEFAULT_BATCH_SIZE = 16
UPLOAD_RETRY_BATCH_SIZE = 4
MAX_RETRIES = 3
EMBEDDING_CLIENT_ATTEMPTS = 1

_RATE_LIMIT_MARKERS = (
    "rate limit",
    "rate_limit",
    "rate-limit",
    "too many requests",
    "throttl",
)
_QUOTA_MARKERS = (
    "daily",
    "per day",
    "per_day",
    "day limit",
    "current quota",
    "exceeded your current quota",
    "quota exceeded",
    "quota_exceeded",
    "quota exhausted",
    "quota_exhausted",
    "resource exhausted",
    "resource_exhausted",
)


@dataclass
class UploadReport:
    chunk_count: int = 0
    counts_by_type: dict[str, int] = field(default_factory=dict)
    counts_by_source: dict[str, int] = field(default_factory=dict)
    estimated_embedding_characters: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    uploaded: int = 0
    failed: int = 0
    uploaded_ids: list[str] = field(default_factory=list)
    failed_ids: list[str] = field(default_factory=list)
    embedding_errors: list[str] = field(default_factory=list)
    embedding_failure_kinds: dict[str, int] = field(default_factory=dict)
    embedding_retries: int = 0
    embedding_stopped: bool = False
    pending: int = 0
    upload_errors: list[str] = field(default_factory=list)
    stale_ids: list[str] = field(default_factory=list)
    deleted_stale: int = 0
    missing_configuration: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_count": self.chunk_count,
            "counts_by_type": dict(sorted(self.counts_by_type.items())),
            "counts_by_source": dict(sorted(self.counts_by_source.items())),
            "estimated_embedding_characters": self.estimated_embedding_characters,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "uploaded": self.uploaded,
            "failed": self.failed,
            "uploaded_ids": self.uploaded_ids,
            "failed_ids": self.failed_ids,
            "embedding_errors": self.embedding_errors,
            "embedding_failure_kinds": dict(sorted(self.embedding_failure_kinds.items())),
            "embedding_retries": self.embedding_retries,
            "embedding_stopped": self.embedding_stopped,
            "pending": self.pending,
            "upload_errors": self.upload_errors,
            "stale_ids": self.stale_ids,
            "deleted_stale": self.deleted_stale,
            "missing_configuration": self.missing_configuration,
        }


def load_chunks(path: str | Path) -> list[ChunkRecord]:
    """Load and validate canonical ChunkRecord JSONL input."""

    records: list[ChunkRecord] = []
    seen: set[str] = set()
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = ChunkRecord.model_validate(json.loads(line))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid chunk at line {line_number}: {type(exc).__name__}") from exc
        if record.id in seen:
            raise ValueError(f"duplicate chunk ID: {record.id}")
        seen.add(record.id)
        records.append(record)
    return records


def validate_chunks(chunks: Iterable[ChunkRecord | Mapping[str, Any]]) -> list[ChunkRecord]:
    """Validate records and reject duplicate IDs before any network operation."""

    result: list[ChunkRecord] = []
    seen: set[str] = set()
    for value in chunks:
        record = value if isinstance(value, ChunkRecord) else ChunkRecord.model_validate(value)
        if record.id in seen:
            raise ValueError(f"duplicate chunk ID: {record.id}")
        seen.add(record.id)
        result.append(record)
    return result


def _midnight_utc(value: date | datetime | str | None) -> str | None:
    """Format dates as the required UTC-midnight Azure timestamp."""

    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return f"{value.isoformat()[:10]}T00:00:00Z"
    return f"{str(value)[:10]}T00:00:00Z"


def to_search_document(
    chunk: ChunkRecord | Mapping[str, Any], vector: Sequence[float], dimensions: int
) -> dict[str, Any]:
    """Map a canonical chunk to the complete section-26 Search document."""

    if not isinstance(chunk, ChunkRecord):
        chunk = ChunkRecord.model_validate(chunk)
    checked = validate_vector(vector, dimensions)
    if checked is None:
        raise ValueError(f"{chunk.id}: embedding must contain exactly {dimensions} finite numbers")
    value = chunk.model_dump(mode="json")
    for name in ("published_date", "effective_from", "effective_to"):
        value[name] = _midnight_utc(value.get(name))
    value["embedding"] = checked
    # These are ingestion-contract fields and are intentionally not indexed.
    value.pop("embedding_text", None)
    value.pop("schema_version", None)
    return value


def estimate(
    chunks: Sequence[ChunkRecord], cache_path: str | Path, config: IngestionConfig
) -> UploadReport:
    """Calculate a dry-run report without creating or mutating a cache or provider client."""

    report = UploadReport(
        chunk_count=len(chunks),
        estimated_embedding_characters=sum(len(chunk.embedding_text) for chunk in chunks),
    )
    for chunk in chunks:
        report.counts_by_type[chunk.content_type] = (
            report.counts_by_type.get(chunk.content_type, 0) + 1
        )
        report.counts_by_source[chunk.source_id] = (
            report.counts_by_source.get(chunk.source_id, 0) + 1
        )
    path = Path(cache_path)
    if not path.is_file():
        report.cache_misses = len(chunks)
        return report
    try:
        # Read-only mode preserves the no-mutation guarantee of dry runs.
        import sqlite3

        uri = f"file:{path.resolve()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            for chunk in chunks:
                key = content_hash(chunk.embedding_text)
                row = connection.execute(
                    "SELECT vector_json FROM embeddings WHERE model = ? AND dimensions = ? "
                    "AND content_hash = ?",
                    (config.embedding_model, config.embedding_dimensions, key),
                ).fetchone()
                valid = None
                if row:
                    try:
                        valid = json.loads(row[0])
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass
                if validate_vector(valid, config.embedding_dimensions) is None:
                    report.cache_misses += 1
                else:
                    report.cache_hits += 1
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        report.cache_misses = len(chunks)
    return report


def _status_code(exc: Exception) -> int | None:
    for value in (
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(value, int):
            return value
    return None


def _error_markers(exc: Exception) -> str:
    """Return provider classification text without exposing it in reports or logs."""

    values = [getattr(exc, "status", None), getattr(exc, "message", None), str(exc)]
    details = getattr(exc, "details", None)
    if details is not None:
        values.append(details)
    return " ".join(str(value) for value in values if value is not None).lower()


def _is_quota_exhausted(exc: Exception) -> bool:
    """Identify terminal quota errors that must not be retried.

    Gemini uses HTTP 429 with ``RESOURCE_EXHAUSTED`` for both rate limiting and
    exhausted quota.  Explicit rate-limit markers remain retryable; an otherwise
    ambiguous RESOURCE_EXHAUSTED response is treated as terminal to avoid burning
    time and quota through repeated attempts.
    """

    if _status_code(exc) != 429:
        return False
    markers = _error_markers(exc)
    status = str(getattr(exc, "status", "")).upper()
    # Check quota language first: Gemini's quota message often includes a
    # rate-limits documentation URL, which must not turn a terminal error into
    # a retryable one.
    if any(marker in markers for marker in _QUOTA_MARKERS):
        return True
    if status == "RESOURCE_EXHAUSTED" and not any(
        marker in markers for marker in _RATE_LIMIT_MARKERS
    ):
        return True
    return False


def _embedding_failure_kind(exc: Exception) -> str:
    if _is_quota_exhausted(exc):
        return "quota_exhausted"
    if _is_transient(exc):
        return "transient"
    return "provider_error"


def _is_transient(exc: Exception) -> bool:
    status_code = _status_code(exc)
    if status_code is not None:
        if status_code == 429:
            return not _is_quota_exhausted(exc)
        return 500 <= status_code < 600

    status = getattr(exc, "status", None) or getattr(exc, "code", None)
    if isinstance(status, int):
        return status == 429 or 500 <= status < 600
    if isinstance(status, str) and status.upper() in {
        "RESOURCE_EXHAUSTED",
        "UNAVAILABLE",
        "DEADLINE_EXCEEDED",
    }:
        return True
    return isinstance(exc, (TimeoutError, ConnectionError))


def _embed_provider_batch(
    client: Any, config: IngestionConfig, texts: Sequence[str]
) -> list[list[float]]:
    from google.genai import types

    response = client.models.embed_content(
        model=config.embedding_model,
        contents=[types.Content(parts=[types.Part(text=text)]) for text in texts],
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=config.embedding_dimensions,
        ),
    )
    embeddings = getattr(response, "embeddings", None)
    if not embeddings or len(embeddings) != len(texts):
        raise ValueError("embedding count did not match batch size")
    vectors: list[list[float]] = []
    for embedding in embeddings:
        values = (
            embedding.get("values")
            if isinstance(embedding, Mapping)
            else getattr(embedding, "values", None)
        )
        vector = validate_vector(values, config.embedding_dimensions)
        if vector is None:
            raise ValueError(
                f"embedding provider returned a vector with wrong size; expected "
                f"{config.embedding_dimensions}"
            )
        vectors.append(vector)
    return vectors


def _embed_with_retry(
    client: Any,
    config: IngestionConfig,
    texts: Sequence[str],
    *,
    retries: int = MAX_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[], None] | None = None,
) -> list[list[float]]:
    for attempt in range(retries + 1):
        try:
            return _embed_provider_batch(client, config, texts)
        except Exception as exc:
            if not _is_transient(exc) or attempt >= retries:
                raise
            delay = min(8.0, 0.25 * (2**attempt)) + random.uniform(0, 0.1)
            if on_retry is not None:
                on_retry()
            sleep(delay)
    raise RuntimeError("unreachable embedding retry state")


def search_existing_ids(client: Any, source_ids: Iterable[str]) -> set[str]:
    """Read IDs only within the supplied source scope."""

    existing: set[str] = set()
    for source_id in sorted(set(source_ids)):
        escaped = source_id.replace("'", "''")
        results = client.search(
            search_text="*",
            filter=f"source_id eq '{escaped}'",
            select=["id", "source_id"],
            top=1000,
        )
        for result in results:
            result_source = (
                result.get("source_id")
                if isinstance(result, Mapping)
                else getattr(result, "source_id", None)
            )
            result_id = (
                result.get("id") if isinstance(result, Mapping) else getattr(result, "id", None)
            )
            if result_source == source_id and result_id:
                existing.add(str(result_id))
    return existing


def _succeeded(result: Any) -> bool:
    if isinstance(result, Mapping):
        return bool(result.get("succeeded", False))
    return bool(getattr(result, "succeeded", False))


def _upload_batch(
    client: Any, documents: Sequence[dict[str, Any]]
) -> tuple[int, int, list[str], list[str]]:
    results = client.merge_or_upload_documents(list(documents))
    uploaded = failed = 0
    errors: list[str] = []
    uploaded_ids: list[str] = []
    for document, result in zip(documents, results, strict=False):
        if _succeeded(result):
            uploaded += 1
            uploaded_ids.append(document["id"])
        else:
            failed += 1
            key = (
                result.get("key", document["id"])
                if isinstance(result, Mapping)
                else getattr(result, "key", document["id"])
            )
            errors.append(str(key))
    if len(results) < len(documents):
        failed += len(documents) - len(results)
        errors.extend(document["id"] for document in documents[len(results) :])
    return uploaded, failed, errors, uploaded_ids


def _upload_batch_with_retry(
    client: Any,
    documents: Sequence[dict[str, Any]],
    *,
    retries: int = MAX_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[], None] | None = None,
) -> tuple[int, int, list[str], list[str]]:
    """Retry a Search batch only when the provider reports a transient failure."""

    for attempt in range(retries + 1):
        try:
            return _upload_batch(client, documents)
        except Exception as exc:
            if not _is_transient(exc) or attempt >= retries:
                raise
            delay = min(8.0, 0.25 * (2**attempt)) + random.uniform(0, 0.1)
            if on_retry is not None:
                on_retry()
            sleep(delay)
    raise RuntimeError("unreachable upload retry state")


def run_upload(
    chunks: Sequence[ChunkRecord],
    cache: EmbeddingCache,
    config: IngestionConfig,
    *,
    embedding_client: Any | None = None,
    search_client: Any | None = None,
    delete_stale: bool = False,
    source_ids: Iterable[str] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> UploadReport:
    """Embed/cache/upload records, resuming from successful cache rows."""

    if batch_size < 1:
        raise ValueError("batch size must be positive")
    records = validate_chunks(chunks)
    report = estimate(records, cache.path, config)
    # estimate() opened read-only; use cache.get below for authoritative validation.
    report.cache_hits = report.cache_misses = 0
    own_embedding = embedding_client is None
    own_search = search_client is None
    if own_embedding:
        from google import genai
        from google.genai import types

        # The SDK defaults to five HTTP attempts. Keep retries in this module so
        # quota failures stop immediately and transient failures have one bounded
        # policy rather than multiplying nested retry loops.
        embedding_client = genai.Client(
            api_key=config.gemini_api_key,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=EMBEDDING_CLIENT_ATTEMPTS)
            ),
        )
    if own_search:
        search_client = SearchClient(
            config.search_endpoint, config.search_index, AzureKeyCredential(config.search_key)
        )
    try:
        for start in range(0, len(records), batch_size):
            batch = records[start : start + batch_size]
            vectors: dict[str, list[float]] = {}
            misses: list[ChunkRecord] = []
            embedding_failed = False
            for chunk in batch:
                key = content_hash(chunk.embedding_text)
                cached = cache.get(config.embedding_model, config.embedding_dimensions, key)
                if cached is None:
                    misses.append(chunk)
                    report.cache_misses += 1
                else:
                    vectors[chunk.id] = cached
                    report.cache_hits += 1
            if misses:
                try:
                    generated = _embed_with_retry(
                        embedding_client,
                        config,
                        [chunk.embedding_text for chunk in misses],
                        on_retry=lambda: setattr(
                            report, "embedding_retries", report.embedding_retries + 1
                        ),
                    )
                    cache.put_batch(
                        config.embedding_model,
                        config.embedding_dimensions,
                        [
                            (content_hash(chunk.embedding_text), vector)
                            for chunk, vector in zip(misses, generated, strict=True)
                        ],
                    )
                    vectors.update(
                        {chunk.id: vector for chunk, vector in zip(misses, generated, strict=True)}
                    )
                except Exception as exc:
                    kind = _embedding_failure_kind(exc)
                    report.embedding_errors.append(f"batch starting {batch[0].id}: {kind}")
                    report.embedding_failure_kinds[kind] = (
                        report.embedding_failure_kinds.get(kind, 0) + 1
                    )
                    report.failed += len(misses)
                    report.failed_ids.extend(chunk.id for chunk in misses)
                    embedding_failed = True
                    if kind == "quota_exhausted":
                        report.embedding_stopped = True
                        report.pending = len(records) - (start + len(batch))
            documents = [
                to_search_document(chunk, vectors[chunk.id], config.embedding_dimensions)
                for chunk in batch
                if chunk.id in vectors
            ]
            if not documents:
                if embedding_failed and report.embedding_stopped:
                    break
                continue
            try:
                uploaded, failed, errors, uploaded_ids = _upload_batch_with_retry(
                    search_client, documents
                )
            except Exception as exc:
                # Some Search service tiers reject a larger request transiently while
                # accepting the same documents in smaller payloads. Retry the normal
                # batch first, then recover in bounded four-document sub-batches.
                if _is_transient(exc) and len(documents) > UPLOAD_RETRY_BATCH_SIZE:
                    for sub_start in range(0, len(documents), UPLOAD_RETRY_BATCH_SIZE):
                        sub_batch = documents[sub_start : sub_start + UPLOAD_RETRY_BATCH_SIZE]
                        try:
                            sub_uploaded, sub_failed, sub_errors, sub_uploaded_ids = (
                                _upload_batch_with_retry(search_client, sub_batch)
                            )
                        except Exception as sub_exc:
                            report.failed += len(sub_batch)
                            report.upload_errors.append(
                                f"batch starting {sub_batch[0]['id']}: {type(sub_exc).__name__}"
                            )
                            report.failed_ids.extend(document["id"] for document in sub_batch)
                        else:
                            report.uploaded += sub_uploaded
                            report.failed += sub_failed
                            report.uploaded_ids.extend(sub_uploaded_ids)
                            report.upload_errors.extend(sub_errors)
                            report.failed_ids.extend(sub_errors)
                else:
                    report.failed += len(documents)
                    report.upload_errors.append(
                        f"batch starting {batch[0].id}: {type(exc).__name__}"
                    )
                    report.failed_ids.extend(document["id"] for document in documents)
            else:
                report.uploaded += uploaded
                report.failed += failed
                report.uploaded_ids.extend(uploaded_ids)
                report.upload_errors.extend(errors)
                report.failed_ids.extend(errors)

            # A daily/project quota is not recoverable by trying the next batch.
            # Stop after uploading any cache-backed records in this batch; all
            # successful cache writes remain committed for a later resume.
            if embedding_failed and report.embedding_stopped:
                break

        if source_ids is None:
            source_ids = {chunk.source_id for chunk in records}
        # Deletion is only attempted after all records have embedded and uploaded successfully.
        if (
            delete_stale
            and report.failed == 0
            and not report.embedding_errors
            and not report.upload_errors
        ):
            existing = search_existing_ids(search_client, source_ids)
            local = {chunk.id for chunk in records}
            report.stale_ids = sorted(existing - local)
            for start in range(0, len(report.stale_ids), batch_size):
                stale_batch = [
                    {"id": item} for item in report.stale_ids[start : start + batch_size]
                ]
                if stale_batch:
                    results = search_client.delete_documents(stale_batch)
                    report.deleted_stale += sum(1 for result in results if _succeeded(result))
    finally:
        if own_search and search_client is not None:
            close = getattr(search_client, "close", None)
            if close:
                close()
    return report


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "UploadReport",
    "estimate",
    "load_chunks",
    "run_upload",
    "search_existing_ids",
    "to_search_document",
    "validate_chunks",
]
