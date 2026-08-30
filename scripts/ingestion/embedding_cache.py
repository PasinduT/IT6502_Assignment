"""Durable, validated SQLite cache for document embedding vectors."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """CREATE TABLE IF NOT EXISTS embeddings (
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (model, dimensions, content_hash)
)"""


def content_hash(embedding_text: str) -> str:
    """Return the cache key for an embedding input."""

    return hashlib.sha256(embedding_text.encode("utf-8")).hexdigest()


def validate_vector(vector: object, dimensions: int) -> list[float] | None:
    """Decode/validate a vector; invalid cache or provider values return ``None``."""

    if dimensions < 1 or not isinstance(vector, (list, tuple)) or len(vector) != dimensions:
        return None
    result: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        if not math.isfinite(number):
            return None
        result.append(number)
    return result


class EmbeddingCache:
    """SQLite-backed cache keyed by model, dimensions, and embedding-text hash."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.execute(SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> EmbeddingCache:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def get(self, model: str, dimensions: int, text_hash: str) -> list[float] | None:
        row = self._connection.execute(
            "SELECT vector_json FROM embeddings WHERE model = ? AND dimensions = ? "
            "AND content_hash = ?",
            (model, dimensions, text_hash),
        ).fetchone()
        if row is None:
            return None
        try:
            decoded = json.loads(row[0])
        except (TypeError, ValueError, json.JSONDecodeError):
            decoded = None
        vector = validate_vector(decoded, dimensions)
        if vector is None:
            # Invalid rows cannot be reused and are removed so a replacement can be committed.
            self._connection.execute(
                "DELETE FROM embeddings WHERE model = ? AND dimensions = ? AND content_hash = ?",
                (model, dimensions, text_hash),
            )
            self._connection.commit()
        return vector

    def get_many(
        self, model: str, dimensions: int, hashes: Iterable[str]
    ) -> dict[str, list[float]]:
        return {
            text_hash: vector
            for text_hash in hashes
            if (vector := self.get(model, dimensions, text_hash)) is not None
        }

    def put(self, model: str, dimensions: int, text_hash: str, vector: Sequence[float]) -> None:
        valid = validate_vector(vector, dimensions)
        if valid is None:
            raise ValueError(f"embedding vector must contain exactly {dimensions} finite numbers")
        self._connection.execute(
            "INSERT OR REPLACE INTO embeddings "
            "(model, dimensions, content_hash, vector_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                model,
                dimensions,
                text_hash,
                json.dumps(valid, separators=(",", ":")),
                datetime.now(UTC).isoformat(),
            ),
        )
        self._connection.commit()

    # Explicit aliases make the cache convenient for callers that prefer vector terminology.
    get_vector = get
    put_vector = put

    def put_batch(
        self, model: str, dimensions: int, values: Iterable[tuple[str, Sequence[float]]]
    ) -> None:
        prepared = []
        for text_hash, vector in values:
            valid = validate_vector(vector, dimensions)
            if valid is None:
                raise ValueError(
                    f"embedding vector must contain exactly {dimensions} finite numbers"
                )
            prepared.append(
                (
                    model,
                    dimensions,
                    text_hash,
                    json.dumps(valid, separators=(",", ":")),
                    datetime.now(UTC).isoformat(),
                )
            )
        if prepared:
            self._connection.executemany(
                "INSERT OR REPLACE INTO embeddings "
                "(model, dimensions, content_hash, vector_json, created_at) VALUES (?, ?, ?, ?, ?)",
                prepared,
            )
        self._connection.commit()

    def count(self) -> int:
        return int(self._connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])


__all__ = ["EmbeddingCache", "SCHEMA", "content_hash", "validate_vector"]
