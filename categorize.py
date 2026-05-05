#!/usr/bin/env python3
"""
categorize.py — Parse Firefox bookmarks and categorize them via Blablador LLM.

Steps:
  1. Parse data/bookmarks.html → data/bookmarks.json
  2. Call Blablador LLM to match/propose tags → data/categorized.json

Usage:
    uv run categorize.py [options]

Options:
    --input        Path to Firefox HTML export (default: data/bookmarks.html)
    --batch-size   Bookmarks per LLM call (default: 10)
    --dry-run      Show what would be sent to LLM, don't call it
    --parse-only   Refresh data/bookmarks.json and stop before LLM/API calls
    --resume       Skip bookmarks already in categorized.json
    --skip-parse   Reuse existing data/bookmarks.json
    --verbose, -v  Print each bookmark as processed

Env vars (from .env):
    BLABLADOR_API_KEY   — required (or set in system env)
    ANYTYPE_API_KEY     — required (to fetch current tags from Ontologist)
    ANYTYPE_BASE_URL    — defaults to http://localhost:31009
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent

load_dotenv(REPO_ROOT / ".env")

BLABLADOR_BASE_URL = "https://api.helmholtz-blablador.fz-juelich.de/v1"
BLABLADOR_MODEL = "alias-fast"
BLABLADOR_API_KEY = os.getenv("BLABLADOR_API_KEY", "")

ANYTYPE_BASE_URL = os.getenv("ANYTYPE_BASE_URL", "http://localhost:31009")
ANYTYPE_API_KEY = os.getenv("ANYTYPE_API_KEY", "")
ANYTYPE_VERSION = "2025-11-08"
ONTOLOGIST_SPACE_ID = os.getenv(
    "ANYTYPE_SPACE_ID",
    "bafyreig6fpie6n66zh7ive6chvjrsvwxbdue6kzh5b7ljrc3i5ny2z2jui.q4gkw8g0ft1i",
)
TAG_PROPERTY_ID = "bafyreiailumqalfxxfwcocgpwbxgjis3thxrqzsghx7cfnnhtcxp27nqqu"

DEFAULT_BATCH_SIZE = 10
MAX_RETRIES = 3
RETRY_DELAY = 5


# ---------------------------------------------------------------------------
# Step 1: Parse Firefox bookmarks HTML
# ---------------------------------------------------------------------------

def parse_bookmarks(html_path: Path) -> list[dict]:
    bookmarks: list[dict] = []
    folder_stack: list[str] = []
    pending_folder = ""
    last_bookmark: dict | None = None

    # Firefox exports bookmarks in Netscape bookmark HTML format. It is not
    # reliably parseable as a normal DOM because sibling <DT> elements are often
    # treated as descendants by HTML parsers, so preserve hierarchy from lines.
    with open(html_path, encoding="utf-8") as f:
        for line in f:
            lower = line.lower()

            if "<dt><h3" in lower:
                h3 = BeautifulSoup(line, "html.parser").find("h3")
                pending_folder = h3.get_text(strip=True) if h3 else ""
                last_bookmark = None
                continue

            if "<dl" in lower:
                if pending_folder:
                    folder_stack.append(pending_folder)
                    pending_folder = ""
                last_bookmark = None
                continue

            if "</dl>" in lower:
                if folder_stack:
                    folder_stack.pop()
                pending_folder = ""
                last_bookmark = None
                continue

            if "<dt><a" in lower:
                anchor = BeautifulSoup(line, "html.parser").find("a")
                if not anchor or not anchor.get("href"):
                    last_bookmark = None
                    continue
                url = anchor["href"].strip()
                if not url.startswith("http"):
                    last_bookmark = None
                    continue
                add_date_raw = anchor.get("add_date", "")
                date_added = ""
                if add_date_raw:
                    try:
                        ts = int(add_date_raw)
                        date_added = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
                    except (ValueError, OSError):
                        pass
                last_bookmark = {
                    "title": anchor.get_text(strip=True),
                    "url": url,
                    "folder_path": " > ".join(folder_stack) if folder_stack else "Uncategorized",
                    "date_added": date_added,
                    "description": "",
                }
                bookmarks.append(last_bookmark)
                continue

            if "<dd" in lower and last_bookmark is not None:
                dd = BeautifulSoup(line, "html.parser").find("dd")
                if dd:
                    last_bookmark["description"] = dd.get_text(strip=True)

    return bookmarks


# ---------------------------------------------------------------------------
# Step 2: Fetch existing Anytype tags
# ---------------------------------------------------------------------------

def anytype_headers() -> dict:
    return {
        "Authorization": f"Bearer {ANYTYPE_API_KEY}",
        "Anytype-Version": ANYTYPE_VERSION,
        "Content-Type": "application/json",
    }


def fetch_existing_tags(space_id: str) -> list[dict]:
    url = f"{ANYTYPE_BASE_URL}/v1/spaces/{space_id}/properties/{TAG_PROPERTY_ID}/tags"
    resp = requests.get(url, headers=anytype_headers(), timeout=10)
    resp.raise_for_status()
    return resp.json().get("data", [])


# ---------------------------------------------------------------------------
# Step 3: Blablador LLM categorization
# ---------------------------------------------------------------------------

def blablador_chat(messages: list[dict], temperature: float = 0.1) -> str:
    url = f"{BLABLADOR_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {BLABLADOR_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"model": BLABLADOR_MODEL, "messages": messages, "temperature": temperature}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code == 429:
                wait = RETRY_DELAY * attempt
                print(f"  Rate limited, waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                raise
            print(f"  Request error (attempt {attempt}/{MAX_RETRIES}): {e}", file=sys.stderr)
            time.sleep(RETRY_DELAY)
    raise RuntimeError("Max retries exceeded for Blablador API")


def build_system_prompt(existing_tags: list[dict]) -> str:
    tag_list = "\n".join(f"  - {t['name']}" for t in existing_tags)
    return f"""You are a knowledge management assistant. Your job is to categorize bookmarks for a researcher working in ontologies, metadata, knowledge graphs, research data management (RDM), and software tools.

EXISTING TAGS in their Anytype knowledge base:
{tag_list}

For each bookmark you receive, respond with a JSON object containing:
- "matched_tags": list of existing tag names from the list above that apply (can be empty)
- "proposed_tags": list of NEW tag names NOT in the existing list (use snake_case, only propose if truly necessary)
- "description": 1-2 sentence description of what this resource is about

Rules:
- Prefer matching existing tags over creating new ones
- Only propose a new tag if no existing tag adequately captures the topic
- Keep descriptions factual and concise
- If the bookmark is clearly not relevant to the research domain (e.g. shopping, admin), set matched_tags=[], proposed_tags=[], description="Not relevant to research domain"
- Return ONLY valid JSON, no markdown fences, no explanation"""


def build_user_prompt(batch: list[dict]) -> str:
    items = []
    for i, bm in enumerate(batch):
        items.append(
            f'{i+1}. Title: "{bm["title"]}"\n'
            f'   URL: {bm["url"]}\n'
            f'   Firefox folder: {bm["folder_path"]}\n'
            f'   Firefox description: {bm.get("description", "") or "(none)"}'
        )
    return (
        f"Categorize these {len(batch)} bookmarks. "
        f"Return a JSON array with one object per bookmark, in the same order:\n\n"
        + "\n\n".join(items)
    )


def categorize_batch(batch: list[dict], existing_tags: list[dict]) -> list[dict]:
    messages = [
        {"role": "system", "content": build_system_prompt(existing_tags)},
        {"role": "user", "content": build_user_prompt(batch)},
    ]
    raw = blablador_chat(messages)
    try:
        results = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            results = json.loads(match.group())
        else:
            print(f"  WARNING: Could not parse LLM response as JSON. Raw:\n{raw[:500]}", file=sys.stderr)
            results = [{"matched_tags": [], "proposed_tags": [], "description": ""} for _ in batch]
    if not isinstance(results, list):
        results = [results]
    enriched = []
    for i, bm in enumerate(batch):
        enrichment = results[i] if i < len(results) else {}
        enriched.append({
            **bm,
            "matched_tags": enrichment.get("matched_tags", []),
            "proposed_tags": enrichment.get("proposed_tags", []),
            "llm_description": enrichment.get("description", ""),
        })
    return enriched


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Parse Firefox bookmarks and categorize via Blablador LLM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run categorize.py                        # full run
  uv run categorize.py --dry-run              # show LLM prompt, no API calls
  uv run categorize.py --parse-only           # only refresh data/bookmarks.json
  uv run categorize.py --resume               # continue interrupted run
  uv run categorize.py --skip-parse           # skip HTML parsing, reuse bookmarks.json
  uv run categorize.py --batch-size 5 -v     # smaller batches, verbose
        """,
    )
    parser.add_argument("--input", default=str(REPO_ROOT / "data" / "bookmarks.html"),
                        help="Firefox bookmarks HTML export (default: data/bookmarks.html)")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help="Bookmarks per LLM call (default: 10)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be sent to LLM without calling it")
    parser.add_argument("--parse-only", action="store_true",
                        help="Refresh data/bookmarks.json and stop before LLM/API calls")
    parser.add_argument("--resume", action="store_true",
                        help="Skip bookmarks already in categorized.json")
    parser.add_argument("--skip-parse", action="store_true",
                        help="Skip HTML parsing, reuse existing data/bookmarks.json")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    bookmarks_path = REPO_ROOT / "data" / "bookmarks.json"
    categorized_path = REPO_ROOT / "data" / "categorized.json"

    # --- Step 1: Parse ---
    if not args.skip_parse:
        html_path = Path(args.input)
        if not html_path.exists():
            print(f"ERROR: {html_path} not found.", file=sys.stderr)
            print("Export from Firefox: Bookmarks → Manage Bookmarks → Import and Backup → Export to HTML", file=sys.stderr)
            sys.exit(1)
        print(f"Parsing {html_path}...")
        bookmarks = parse_bookmarks(html_path)
        bookmarks_path.parent.mkdir(parents=True, exist_ok=True)
        with open(bookmarks_path, "w", encoding="utf-8") as f:
            json.dump(bookmarks, f, indent=2, ensure_ascii=False)
        print(f"Parsed {len(bookmarks)} bookmarks → {bookmarks_path}")
        if args.verbose:
            folders: dict[str, int] = {}
            for bm in bookmarks:
                folders[bm["folder_path"]] = folders.get(bm["folder_path"], 0) + 1
            print("\nFolder breakdown:")
            for folder, count in sorted(folders.items(), key=lambda x: -x[1]):
                print(f"  {count:4d}  {folder}")
        if args.parse_only:
            return
    else:
        if not bookmarks_path.exists():
            print(f"ERROR: {bookmarks_path} not found. Run without --skip-parse first.", file=sys.stderr)
            sys.exit(1)
        with open(bookmarks_path, encoding="utf-8") as f:
            bookmarks = json.load(f)
        print(f"Loaded {len(bookmarks)} bookmarks from {bookmarks_path}")

    # --- Step 2: Validate env ---
    if not BLABLADOR_API_KEY:
        print("ERROR: BLABLADOR_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    if not ANYTYPE_API_KEY:
        print("ERROR: ANYTYPE_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    # --- Step 3: Fetch existing tags ---
    print("Fetching existing tags from Anytype Ontologist space...")
    try:
        existing_tags = fetch_existing_tags(ONTOLOGIST_SPACE_ID)
        print(f"Found {len(existing_tags)} existing tags: {[t['name'] for t in existing_tags]}")
    except Exception as e:
        print(f"WARNING: Could not fetch tags from Anytype: {e}", file=sys.stderr)
        print("Continuing without existing tags (LLM will propose new ones)", file=sys.stderr)
        existing_tags = []

    # --- Step 4: Resume logic ---
    already_done: set[str] = set()
    existing_results: list[dict] = []
    if args.resume and categorized_path.exists():
        with open(categorized_path, encoding="utf-8") as f:
            existing_results = json.load(f)
        already_done = {bm["url"] for bm in existing_results}
        print(f"Resuming: {len(already_done)} already categorized")

    to_process = [bm for bm in bookmarks if bm["url"] not in already_done]
    print(f"Categorizing {len(to_process)} bookmarks in batches of {args.batch_size}...")

    if args.dry_run:
        print("\n[DRY RUN] Would send the following to LLM:")
        print(build_user_prompt(to_process[:args.batch_size]))
        print(f"\n[DRY RUN] System prompt:\n{build_system_prompt(existing_tags)}")
        return

    # --- Step 5: Categorize ---
    all_results = list(existing_results)
    for i in range(0, len(to_process), args.batch_size):
        batch = to_process[i: i + args.batch_size]
        batch_num = i // args.batch_size + 1
        total_batches = (len(to_process) + args.batch_size - 1) // args.batch_size
        print(f"  Batch {batch_num}/{total_batches} ({len(batch)} bookmarks)...", end=" ", flush=True)
        try:
            enriched = categorize_batch(batch, existing_tags)
            all_results.extend(enriched)
            print("done")
            if args.verbose:
                for bm in enriched:
                    print(f"    {bm['title'][:50]} → {bm['matched_tags']} + {bm['proposed_tags']}")
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            print("Saving progress so far.", file=sys.stderr)
            break
        # Save after each batch (allows resume)
        categorized_path.parent.mkdir(parents=True, exist_ok=True)
        with open(categorized_path, "w", encoding="utf-8") as f:
            json.dump(all_results, f, indent=2, ensure_ascii=False)

    print(f"\nCategorized {len(all_results)} bookmarks → {categorized_path}")

    # Summary of proposed new tags
    proposed: dict[str, int] = {}
    for bm in all_results:
        for tag in bm.get("proposed_tags", []):
            proposed[tag] = proposed.get(tag, 0) + 1
    if proposed:
        print(f"\nProposed NEW tags ({len(proposed)}) — will be created in Anytype during import:")
        for tag, count in sorted(proposed.items(), key=lambda x: -x[1]):
            print(f"  {count:4d}x  {tag}")


if __name__ == "__main__":
    main()
