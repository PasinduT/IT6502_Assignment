from __future__ import annotations

import argparse
import json
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError


def image_count(page) -> int:
    resources = page.get("/Resources")
    if not resources:
        return 0
    resources = resources.get_object()
    xobjects = resources.get("/XObject")
    if not xobjects:
        return 0
    count = 0
    for reference in xobjects.get_object().values():
        try:
            if reference.get_object().get("/Subtype") == "/Image":
                count += 1
        except (AttributeError, KeyError, PdfReadError, TypeError, ValueError):
            continue
    return count


def inspect_pdf(path: Path, root: Path) -> dict:
    reader = PdfReader(path)
    page_text_lengths: list[int] = []
    images = 0
    errors: list[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            page_text_lengths.append(len((page.extract_text() or "").strip()))
        except (KeyError, PdfReadError, TypeError, ValueError) as exc:
            page_text_lengths.append(0)
            errors.append(f"page {page_number} text: {type(exc).__name__}: {exc}")
        try:
            images += image_count(page)
        except (AttributeError, KeyError, PdfReadError, TypeError, ValueError) as exc:
            errors.append(f"page {page_number} images: {type(exc).__name__}: {exc}")
    metadata = reader.metadata or {}
    return {
        "file": str(path.relative_to(root)),
        "pages": len(reader.pages),
        "pages_with_text": sum(length > 20 for length in page_text_lengths),
        "text_characters": sum(page_text_lengths),
        "image_objects": images,
        "title": metadata.get("/Title"),
        "author": metadata.get("/Author"),
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit downloaded PDF text and image coverage.")
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    results = []
    for path in sorted(args.root.rglob("*.pdf")):
        try:
            results.append(inspect_pdf(path, args.root))
        except (OSError, PdfReadError, TypeError, ValueError) as exc:
            results.append(
                {
                    "file": str(path.relative_to(args.root)),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"Audited {len(results)} PDFs: {args.output}")


if __name__ == "__main__":
    main()
