from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    IngestionConfig,
    chunk_text,
    embed_and_upload,
    iso_datetime,
    require_lk,
    stable_id,
)

ROOT = Path(__file__).resolve().parents[1]


def records_for_source(source: dict[str, Any], config: IngestionConfig, manifest_dir: Path):
    require_lk(source, source.get("id", "unknown source"))
    pdf_path = (manifest_dir / source["file"]).resolve()
    reader = PdfReader(pdf_path)
    records = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for position, content in enumerate(
            chunk_text(text, config.chunk_chars, config.chunk_overlap), start=1
        ):
            records.append(
                {
                    "id": stable_id(source["id"], f"p{page_number}-c{position}", content),
                    "content": content,
                    "content_type": "tax_document",
                    "title": source["title"],
                    "source_id": source["id"],
                    "source_url": source.get("source_url"),
                    "blob_path": source.get("blob_path"),
                    "page": page_number,
                    "section": None,
                    "published_date": iso_datetime(source.get("published_date")),
                    "effective_from": iso_datetime(source.get("effective_from")),
                    "effective_to": iso_datetime(source.get("effective_to")),
                    "tax_year": source.get("tax_year"),
                    "document_version": source.get("document_version"),
                    "workflow_id": None,
                    "tags": source.get("tags", []),
                }
            )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Index approved Sri Lankan tax PDFs.")
    parser.add_argument(
        "--manifest", default=ROOT / "data/metadata/sources.example.yaml", type=Path
    )
    args = parser.parse_args()
    config = IngestionConfig.from_env()
    manifest = yaml.safe_load(args.manifest.read_text())
    all_records = []
    skipped = errors = 0
    for source in manifest.get("sources", []):
        if source.get("type") != "tax_document":
            skipped += 1
            continue
        try:
            all_records.extend(records_for_source(source, config, args.manifest.parent))
        except Exception as exc:
            errors += 1
            print(f"ERROR {source.get('id', 'unknown')}: {exc}", file=sys.stderr)
    uploaded, failed = embed_and_upload(all_records, config)
    print(f"Indexed={uploaded} failed={failed} skipped_sources={skipped} source_errors={errors}")
    if failed or errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
