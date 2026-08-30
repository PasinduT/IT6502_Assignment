from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urlparse
from urllib.request import Request, urlopen

try:
    import certifi
except ImportError:  # Fall back to the interpreter trust store outside the project environment.
    certifi = None

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\((https?://[^)]+)\)")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136 Safari/537.36"
)
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where() if certifi else None)
BINARY_SUFFIXES = {
    ".csv",
    ".doc",
    ".docx",
    ".pdf",
    ".xls",
    ".xlsm",
    ".xlsx",
    ".zip",
}


@dataclass(slots=True)
class DownloadResult:
    url: str
    status: str
    http_status: int | None = None
    final_url: str | None = None
    content_type: str | None = None
    file: str | None = None
    bytes: int | None = None
    sha256: str | None = None
    error: str | None = None


def extract_urls(report: Path) -> list[str]:
    return sorted(set(MARKDOWN_LINK.findall(report.read_text(encoding="utf-8"))))


def safe_stem(url: str) -> str:
    parsed = urlparse(url)
    path = unquote(parsed.path).strip("/") or "index"
    query = "-".join(f"{key}-{value}" for key, value in parse_qsl(parsed.query))
    raw = f"{parsed.netloc}-{path.replace('/', '-')}" + (f"-{query}" if query else "")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-.").lower()
    digest = hashlib.sha256(url.encode()).hexdigest()[:10]
    return f"{cleaned[:150]}-{digest}"


def extension_for(url: str, content_type: str) -> str:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type == "application/pdf" or urlparse(url).path.lower().endswith(".pdf"):
        return ".pdf"
    if media_type in {"text/html", "application/xhtml+xml"}:
        return ".html"
    guessed = mimetypes.guess_extension(media_type) if media_type else None
    return guessed or ".bin"


def download_one(url: str, output: Path) -> DownloadResult:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urlopen(request, timeout=45, context=SSL_CONTEXT) as response:
            body = response.read()
            final_url = response.geturl()
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            media_type = content_type.split(";", 1)[0].strip().lower()
            requested_suffix = Path(unquote(urlparse(url).path)).suffix.lower()
            if requested_suffix in BINARY_SUFFIXES and media_type in {
                "text/html",
                "application/xhtml+xml",
            }:
                return DownloadResult(
                    url=url,
                    status="failed",
                    http_status=getattr(response, "status", 200),
                    final_url=final_url,
                    content_type=content_type,
                    bytes=len(body),
                    error=f"expected {requested_suffix} but server returned {media_type}",
                )
            suffix = extension_for(final_url, content_type)
            destination = output / f"{safe_stem(url)}{suffix}"
            destination.write_bytes(body)
            return DownloadResult(
                url=url,
                status="downloaded",
                http_status=getattr(response, "status", 200),
                final_url=final_url,
                content_type=content_type,
                file=str(destination.relative_to(output)),
                bytes=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
            )
    except HTTPError as exc:
        return DownloadResult(url=url, status="failed", http_status=exc.code, error=str(exc))
    except (URLError, TimeoutError, ssl.SSLError) as exc:
        return DownloadResult(url=url, status="failed", error=str(exc))
    except (OSError, ValueError) as exc:
        return DownloadResult(url=url, status="failed", error=f"{type(exc).__name__}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download every linked source in the research report."
    )
    parser.add_argument("report", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    urls = extract_urls(args.report)
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    results: list[DownloadResult] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(download_one, url, args.output): url for url in urls}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(f"{result.status.upper():10} {result.url}")

    results.sort(key=lambda item: item.url)
    manifest = {
        "report": str(args.report),
        "downloaded_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "source_count": len(urls),
        "downloaded_count": sum(item.status == "downloaded" for item in results),
        "failed_count": sum(item.status == "failed" for item in results),
        "sources": [asdict(item) for item in results],
    }
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
