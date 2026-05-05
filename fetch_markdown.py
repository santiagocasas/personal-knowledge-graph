#!/usr/bin/env python3
"""Fetch bookmark pages as markdown using Jina Reader."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests


REPO_ROOT = Path(__file__).parent
DEFAULT_INPUT = REPO_ROOT / "data" / "categorized.json"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "fetched_markdown.json"
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "markdown_cache"


def slug_for_url(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def jina_url(url: str) -> str:
    return f"https://r.jina.ai/{url}"


def fetch_markdown(url: str, timeout: int, retries: int) -> tuple[bool, str]:
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(jina_url(url), timeout=timeout)
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                return False, f"http_{resp.status_code}"
            if resp.status_code == 429:
                time.sleep(min(8, attempt * 2))
                last_error = "rate_limited"
                continue
            resp.raise_for_status()
            text = resp.text.strip()
            if not text:
                return False, "empty_response"
            return True, text
        except requests.RequestException as exc:
            last_error = str(exc)
            if attempt < retries:
                time.sleep(min(8, attempt * 2))
    return False, last_error or "unknown_error"


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch markdown from bookmark URLs using Jina Reader")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true", help="Skip URLs already fetched successfully")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    bookmarks = json.loads(args.input.read_text(encoding="utf-8"))
    if args.limit > 0:
        bookmarks = bookmarks[: args.limit]

    args.cache_dir.mkdir(parents=True, exist_ok=True)

    previous: dict[str, dict] = {}
    if args.resume and args.output.exists():
        old = json.loads(args.output.read_text(encoding="utf-8"))
        previous = {item["url"]: item for item in old if isinstance(item, dict) and item.get("url")}

    results: list[dict] = []
    ok_count = 0
    fail_count = 0
    pending: list[tuple[int, dict]] = []

    for idx, bm in enumerate(bookmarks, start=1):
        url = (bm.get("url") or "").strip()
        title = bm.get("title") or "Untitled"
        if not url:
            continue

        if args.resume and url in previous and previous[url].get("fetch_ok"):
            results.append(previous[url])
            ok_count += 1
            continue

        cache_name = f"{slug_for_url(url)}.md"
        cache_path = args.cache_dir / cache_name
        row = {
            "_index": idx,
            "title": title,
            "url": url,
            "folder_path": bm.get("folder_path", ""),
            "matched_tags": bm.get("matched_tags", []),
            "proposed_tags": bm.get("proposed_tags", []),
            "llm_description": bm.get("llm_description", ""),
            "cache_path": str(cache_path.relative_to(REPO_ROOT)),
            "fetch_ok": False,
            "fetch_error": "",
        }

        print(f"[{idx}/{len(bookmarks)}] fetch {title}")
        if args.dry_run:
            row["fetch_error"] = "dry_run"
            results.append(row)
            continue

        pending.append((idx, row))

    if not args.dry_run:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            future_map = {
                executor.submit(fetch_markdown, row["url"], args.timeout, args.retries): (idx, row)
                for idx, row in pending
            }

            for future in as_completed(future_map):
                idx, row = future_map[future]
                try:
                    ok, payload = future.result()
                except Exception as exc:  # noqa: BLE001
                    ok, payload = False, str(exc)

                cache_path = REPO_ROOT / row["cache_path"]
                if ok:
                    cache_path.write_text(payload, encoding="utf-8")
                    row["fetch_ok"] = True
                    ok_count += 1
                else:
                    row["fetch_error"] = payload
                    fail_count += 1
                results.append(row)
                if args.delay > 0:
                    time.sleep(args.delay)

    if args.dry_run:
        ok_count = sum(1 for r in results if r.get("fetch_ok"))
        fail_count = sum(1 for r in results if not r.get("fetch_ok"))

    results.sort(key=lambda item: item.get("_index", 0))
    for row in results:
        row.pop("_index", None)

    if not args.dry_run:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2, ensure_ascii=True), encoding="utf-8")

    print("\nFetch summary:")
    print(f"  Input bookmarks : {len(bookmarks)}")
    print(f"  Markdown fetched: {ok_count}")
    print(f"  Fetch failures  : {fail_count}")
    print(f"  Output manifest : {args.output}")
    if args.dry_run:
        print("  [DRY RUN] Manifest not written.")


if __name__ == "__main__":
    main()
