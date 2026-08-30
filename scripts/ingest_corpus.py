"""Validate, embed, and incrementally upload the canonical chunk corpus.

The default operation is a local dry run.  Gemini and Azure clients are created only when
``--upload`` is explicitly supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPOSITORY_ROOT = _SCRIPT_DIR.parent
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from scripts.common import IngestionConfig  # noqa: E402
from scripts.ingestion.embedding_cache import EmbeddingCache  # noqa: E402
from scripts.ingestion.models import SourceManifest  # noqa: E402
from scripts.ingestion.registry import load_registry  # noqa: E402
from scripts.ingestion.search_upload import (  # noqa: E402
    UploadReport,
    estimate,
    load_chunks,
    run_upload,
    search_existing_ids,
    validate_chunks,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Embed and upload the approved chunk corpus")
    parser.add_argument("--chunks", required=True, type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--manifest", type=Path, help="approved source registry for upload scope")
    parser.add_argument("--report", type=Path, help="machine-readable report path")
    parser.add_argument("--embedding-batch-size", type=int, default=16)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="validate and estimate without mutation"
    )
    mode.add_argument("--upload", action="store_true", help="perform embedding and Search uploads")
    parser.add_argument(
        "--delete-stale",
        action="store_true",
        help=("delete stale IDs only for a complete matching corpus build (requires --upload)"),
    )
    return parser


def _write_report(path: Path | None, report: UploadReport) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _approved_source_ids(manifest_path: Path) -> set[str]:
    manifest: SourceManifest = load_registry(manifest_path)
    return {
        source.id
        for source in manifest.sources
        if source.review_status.value == "approved" and source.status.value != "excluded"
    }


def _source_scope(manifest_path: Path | None, chunks: list) -> set[str]:
    chunk_sources = {chunk.source_id for chunk in chunks}
    if manifest_path is None:
        return chunk_sources
    approved = _approved_source_ids(manifest_path)
    outside = chunk_sources - approved
    if outside:
        raise ValueError(
            "chunks contain source IDs outside the approved manifest: " + ", ".join(sorted(outside))
        )
    # A chunk file may be an intentionally narrow debugging or incremental set.  Its
    # source IDs are the only safe scope unless a complete-build provenance marker is
    # explicitly validated for stale deletion below.
    return chunk_sources


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validated_delete_scope(
    manifest_path: Path | None, chunks_path: Path, chunks: list
) -> set[str]:
    """Return the full deletion scope only for a matching complete corpus build."""

    if manifest_path is None:
        raise ValueError("--delete-stale requires --manifest and a complete corpus build")

    metadata_path = chunks_path.with_name("corpus-scope.json")
    try:
        metadata: Any = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            "--delete-stale requires corpus-scope.json produced by a complete corpus build"
        ) from exc
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
        raise ValueError("corpus-scope.json has an unsupported schema")
    if metadata.get("complete") is not True:
        raise ValueError("--delete-stale requires a complete corpus build; scope is partial")
    if metadata.get("includes_images") is not True:
        raise ValueError("--delete-stale requires a corpus build that includes images")

    approved = _approved_source_ids(manifest_path)
    source_ids = metadata.get("source_ids")
    if not isinstance(source_ids, list) or any(not isinstance(item, str) for item in source_ids):
        raise ValueError("corpus-scope.json does not contain valid source IDs")
    scope = set(source_ids)
    if len(scope) != len(source_ids) or scope != approved:
        raise ValueError("corpus-scope.json source scope does not exactly match the manifest")
    requested = metadata.get("requested_source_ids")
    if (
        not isinstance(requested, list)
        or any(not isinstance(item, str) for item in requested)
        or set(requested) not in (set(), approved)
    ):
        raise ValueError("corpus-scope.json was produced from a subset source selection")
    if metadata.get("manifest_sha256") != _sha256(manifest_path):
        raise ValueError("corpus-scope.json does not match the supplied manifest")
    if metadata.get("chunks_sha256") != _sha256(chunks_path):
        raise ValueError("corpus-scope.json does not match the supplied chunks")
    chunk_sources = {chunk.source_id for chunk in chunks}
    if not chunk_sources <= scope:
        raise ValueError("chunks contain source IDs outside corpus-scope.json")
    return scope


def _dry_run(
    chunks: list,
    cache_path: Path,
    config: IngestionConfig,
    source_ids: set[str],
    report: UploadReport,
) -> None:
    if config.search_endpoint and config.search_key and source_ids:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient

        client = SearchClient(
            config.search_endpoint, config.search_index, AzureKeyCredential(config.search_key)
        )
        try:
            local_ids = {chunk.id for chunk in chunks}
            report.stale_ids = sorted(search_existing_ids(client, source_ids) - local_ids)
        except Exception as exc:
            report.upload_errors.append(f"stale lookup: {type(exc).__name__}")
        finally:
            close = getattr(client, "close", None)
            if close:
                close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.delete_stale and not args.upload:
        print("--delete-stale requires explicit --upload", file=sys.stderr)
        return 2
    if args.embedding_batch_size < 1:
        print("--embedding-batch-size must be positive", file=sys.stderr)
        return 2

    try:
        config = IngestionConfig.from_env(require_cloud=False)
    except (SystemExit, ValueError) as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 2
    try:
        chunks = validate_chunks(load_chunks(args.chunks))
        source_ids = _source_scope(args.manifest, chunks)
        if args.delete_stale:
            source_ids = _validated_delete_scope(args.manifest, args.chunks.resolve(), chunks)
    except (OSError, TypeError, ValueError) as exc:
        print(f"Invalid corpus input: {exc}", file=sys.stderr)
        return 2

    report_path = args.report or args.chunks.resolve().parent / "ingestion-report.json"

    report = estimate(chunks, args.cache, config)
    report.missing_configuration = config.missing_cloud_configuration()
    if not args.upload:
        _dry_run(chunks, args.cache, config, source_ids, report)
        _write_report(report_path, report)
        print(
            f"Dry run: chunks={report.chunk_count} "
            f"embedding_chars={report.estimated_embedding_characters} "
            f"cache_hits={report.cache_hits} cache_misses={report.cache_misses} "
            f"stale={len(report.stale_ids)} no network mutations"
        )
        return 1 if report.upload_errors else 0

    if report.missing_configuration:
        print(
            "Missing required configuration: " + ", ".join(report.missing_configuration),
            file=sys.stderr,
        )
        _write_report(report_path, report)
        return 2
    if not chunks:
        _write_report(report_path, report)
        print("Upload complete: chunks=0 uploaded=0 failed=0")
        return 0

    try:
        with EmbeddingCache(args.cache) as cache:
            report = run_upload(
                chunks,
                cache,
                config,
                delete_stale=args.delete_stale,
                source_ids=source_ids,
                batch_size=args.embedding_batch_size,
            )
            report.missing_configuration = config.missing_cloud_configuration()
    except (OSError, ValueError) as exc:
        print(f"Upload failed: {type(exc).__name__}", file=sys.stderr)
        report.upload_errors.append(type(exc).__name__)
        _write_report(report_path, report)
        return 1

    _write_report(report_path, report)
    label = "Upload stopped" if report.embedding_stopped else "Upload complete"
    pending = f" pending={report.pending}" if report.embedding_stopped else ""
    print(
        f"{label}: chunks={report.chunk_count} uploaded={report.uploaded} "
        f"failed={report.failed} cache_hits={report.cache_hits} cache_misses={report.cache_misses} "
        f"stale={len(report.stale_ids)} deleted_stale={report.deleted_stale}{pending}"
    )
    return 1 if report.failed or report.embedding_errors or report.upload_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
