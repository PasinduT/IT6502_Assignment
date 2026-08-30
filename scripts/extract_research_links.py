from __future__ import annotations

import argparse
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attributes = dict(attrs)
        self._href = attributes.get("href")
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        text = re.sub(r"\s+", " ", html.unescape(" ".join(self._text))).strip()
        self.links.append((text, self._href.strip()))
        self._href = None
        self._text = []


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract resolvable links from downloaded HTML indexes."
    )
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    manifest = json.loads((args.root / "manifest.json").read_text(encoding="utf-8"))
    pages = {
        source["file"]: source.get("final_url") or source["url"]
        for source in manifest["sources"]
        if source["status"] == "downloaded" and source.get("file", "").endswith(".html")
    }

    records: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for filename, source_url in sorted(pages.items()):
        link_parser = LinkParser()
        link_parser.feed((args.root / filename).read_text(encoding="utf-8", errors="replace"))
        source_host = urlparse(source_url).netloc.lower()
        for label, href in link_parser.links:
            url = urljoin(source_url, href)
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != source_host:
                continue
            key = (label, url)
            if key in seen:
                continue
            seen.add(key)
            records.append({"source": source_url, "label": label or "Unlabelled link", "url": url})

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    print(f"Extracted {len(records)} links: {args.output}")


if __name__ == "__main__":
    main()
