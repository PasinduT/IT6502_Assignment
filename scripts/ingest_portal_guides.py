from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import IngestionConfig, chunk_text, embed_and_upload, stable_id  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def guide_text(guide: dict) -> str:
    steps = "\n".join(f"{index}. {step}" for index, step in enumerate(guide["steps"], start=1))
    notes = "\n".join(f"- {note}" for note in guide.get("notes", []))
    return f"Workflow: {guide['title']}\nSteps:\n{steps}" + (f"\nNotes:\n{notes}" if notes else "")


def main() -> None:
    parser = argparse.ArgumentParser(description="Index reviewed text-only portal guides.")
    parser.add_argument("--directory", default=ROOT / "data/processed/portal-guides", type=Path)
    args = parser.parse_args()
    config = IngestionConfig.from_env()
    records = []
    errors = 0
    for path in sorted(args.directory.glob("*.y*ml")):
        try:
            guide = yaml.safe_load(path.read_text())
            if guide.get("jurisdiction") != "LK":
                raise ValueError("jurisdiction must be exactly 'LK'")
            if guide.get("review_status") != "approved":
                raise ValueError("guide must have review_status: approved")
            text = guide_text(guide)
            for position, content in enumerate(
                chunk_text(text, config.chunk_chars, config.chunk_overlap), start=1
            ):
                records.append(
                    {
                        "id": stable_id(guide["workflow_id"], str(position), content),
                        "content": content,
                        "content_type": "portal_guide",
                        "title": guide["title"],
                        "source_id": guide["workflow_id"],
                        "source_url": guide.get("source_url"),
                        "blob_path": None,
                        "page": None,
                        "section": None,
                        "published_date": None,
                        "effective_from": None,
                        "effective_to": None,
                        "tax_year": None,
                        "document_version": guide.get("portal_version"),
                        "workflow_id": guide["workflow_id"],
                        "tags": guide.get("tags", []),
                    }
                )
        except Exception as exc:
            errors += 1
            print(f"ERROR {path}: {exc}", file=sys.stderr)
    uploaded, failed = embed_and_upload(records, config)
    print(f"Indexed={uploaded} failed={failed} guide_errors={errors}")
    if failed or errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
