"""Read selected pages of an authorized private GitHub PDF in memory.

Requires an authenticated GitHub CLI with repository read access and pypdf.
Never writes book bytes, page text, or credentials to disk.
"""

import argparse
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from urllib.parse import quote

from pypdf import PdfReader


def api_json(endpoint: str) -> dict:
    gh = os.environ.get("XIAONANHAI_GH", "gh")
    try:
        result = subprocess.run(
            [gh, "api", endpoint], capture_output=True, text=True,
            encoding="utf-8", timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("GitHub CLI is unavailable or the read timed out.") from exc
    if result.returncode:
        raise RuntimeError("Private repository unavailable: check GitHub login and read permission.")
    return json.loads(result.stdout)


def private_repo() -> str:
    value = os.environ.get("XIAONANHAI_BOOK_REPO", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("Set XIAONANHAI_BOOK_REPO to an authorized owner/repository.")
    return value


def get_manifest(repo: str) -> list[dict]:
    record = api_json(f"repos/{repo}/contents/MANIFEST.json")
    if record.get("encoding") != "base64":
        raise RuntimeError("Repository manifest is not available as base64 text.")
    raw = base64.b64decode(record["content"])
    return json.loads(raw.decode("utf-8"))["files"]


def page_numbers(spec: str) -> list[int]:
    match = re.fullmatch(r"([1-9][0-9]*)(?:-([1-9][0-9]*))?", spec)
    if not match:
        raise ValueError("Use --pages N or N-M (1-based PDF pages).")
    first = int(match.group(1))
    last = int(match.group(2) or first)
    if last < first or last - first >= 3:
        raise ValueError("Read at most three consecutive pages per call.")
    return list(range(first, last + 1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List authorized PDF names only")
    parser.add_argument("--book", help="Exact PDF file name from --list")
    parser.add_argument("--pages", help="1-based page or short range, e.g. 12-13")
    parser.add_argument("--max-chars", type=int, default=5000)
    args = parser.parse_args()
    repo = private_repo()
    files = get_manifest(repo)
    if args.list:
        for item in files:
            path = item["path"]
            if path.startswith("books/") and path.endswith(".pdf"):
                print(path.removeprefix("books/"))
        return
    if not args.book or not args.pages:
        parser.error("--book and --pages are required unless --list is used")
    if args.max_chars < 100 or args.max_chars > 10000:
        parser.error("--max-chars must be between 100 and 10000")
    path = "books/" + args.book
    match = next((item for item in files if item["path"] == path), None)
    if not match:
        raise ValueError("Book is not in the private repository manifest.")
    pages = page_numbers(args.pages)
    encoded_path = quote(path, safe="/")
    metadata = api_json(f"repos/{repo}/contents/{encoded_path}")
    blob = api_json(f"repos/{repo}/git/blobs/{metadata['sha']}")
    if blob.get("encoding") != "base64":
        raise RuntimeError("GitHub did not provide the PDF as a base64 blob.")
    raw = base64.b64decode(blob["content"])
    if len(raw) != match["bytes"] or hashlib.sha256(raw).hexdigest() != match["sha256"]:
        raise RuntimeError("Remote PDF does not match the private manifest; no pages returned.")
    reader = PdfReader(io.BytesIO(raw), strict=False)
    if any(number > len(reader.pages) for number in pages):
        raise ValueError(f"This PDF has {len(reader.pages)} pages.")
    print(f"Source: {args.book} | SHA-256: {match['sha256']} | PDF pages: {len(reader.pages)}")
    for number in pages:
        text = reader.pages[number - 1].extract_text() or ""
        print(f"\n--- PDF page {number} ---\n{text[:args.max_chars]}")
        if len(text) > args.max_chars:
            print("[Page text truncated; read this page again with a larger limit if needed.]")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Cannot read private book: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
