#!/usr/bin/env python3
"""Enrich fetched markdown with triage, summary, and keywords."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

import requests
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

BLABLADOR_BASE_URL = "https://api.helmholtz-blablador.fz-juelich.de/v1"
BLABLADOR_MODEL = "alias-qwen36-35b"
BLABLADOR_API_KEY = os.getenv("BLABLADOR_API_KEY", "")

DEFAULT_INPUT = REPO_ROOT / "data" / "fetched_markdown.json"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "enriched_bookmarks.json"


def write_json_atomic(path: Path, payload: list[dict]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    tmp_path.replace(path)


def llm_chat(messages: list[dict], retries: int = 3) -> str:
    if not BLABLADOR_API_KEY:
        raise RuntimeError("BLABLADOR_API_KEY is missing")
    url = f"{BLABLADOR_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {BLABLADOR_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"model": BLABLADOR_MODEL, "messages": messages, "temperature": 0.1}
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=90)
            if resp.status_code == 429:
                time.sleep(2 * attempt)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(2 * attempt)
    raise RuntimeError("LLM request failed")


def parse_json_object(raw: str) -> dict:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        return json.loads(match.group())
    return {}


def build_messages(item: dict, markdown: str) -> list[dict]:
    system = (
        "You are a knowledge triage and synthesis assistant. "
        "Return ONLY JSON with keys: triage, summary, keywords, topic_label. "
        "triage must be one of: knowledge_resource, tool, login_page, admin_page, other. "
        "summary: 2-3 concise sentences. keywords: list of 3-7 lowercase snake_case terms. "
        "topic_label: short phrase (2-5 words) describing the knowledge topic."
    )
    snippet = markdown[:12000]
    user = (
        f"Title: {item.get('title', '')}\n"
        f"URL: {item.get('url', '')}\n"
        f"Folder: {item.get('folder_path', '')}\n\n"
        "Markdown content:\n"
        f"{snippet}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def normalize_keywords(values: list) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        token = re.sub(r"[^a-z0-9_]+", "_", str(value).strip().lower()).strip("_")
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out[:7]


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich fetched markdown with Blablador")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--save-every", type=int, default=10)
    args = parser.parse_args()

    rows = json.loads(args.input.read_text(encoding="utf-8"))
    if args.limit > 0:
        rows = rows[: args.limit]

    previous: dict[str, dict] = {}
    if args.resume and args.output.exists():
        old = json.loads(args.output.read_text(encoding="utf-8"))
        previous = {item["url"]: item for item in old if isinstance(item, dict) and item.get("url")}

    enriched: list[dict] = []
    triage_counts: dict[str, int] = {}

    try:
        for idx, item in enumerate(rows, start=1):
            url = item.get("url", "")
            title = item.get("title", "Untitled")

            if args.resume and url in previous:
                enriched.append(previous[url])
                triage = previous[url].get("triage", "other")
                triage_counts[triage] = triage_counts.get(triage, 0) + 1
                continue

            base = dict(item)
            base.update({
                "triage": "other",
                "synthesis_summary": "",
                "keywords": [],
                "topic_label": "misc",
                "enrich_ok": False,
                "enrich_error": "",
            })

            if not item.get("fetch_ok"):
                base["enrich_error"] = "missing_markdown"
                enriched.append(base)
                triage_counts["other"] = triage_counts.get("other", 0) + 1
                if not args.dry_run and args.save_every > 0 and len(enriched) % args.save_every == 0:
                    write_json_atomic(args.output, enriched)
                    print(f"checkpoint saved: {len(enriched)}/{len(rows)}", flush=True)
                continue

            print(f"[{idx}/{len(rows)}] enrich {title}")
            if args.dry_run:
                base["enrich_error"] = "dry_run"
                enriched.append(base)
                triage_counts["other"] = triage_counts.get("other", 0) + 1
                continue

            cache_path = REPO_ROOT / item["cache_path"]
            markdown = cache_path.read_text(encoding="utf-8") if cache_path.exists() else ""
            try:
                raw = llm_chat(build_messages(item, markdown))
                data = parse_json_object(raw)
                triage = data.get("triage", "other")
                if triage not in {"knowledge_resource", "tool", "login_page", "admin_page", "other"}:
                    triage = "other"
                base["triage"] = triage
                base["synthesis_summary"] = (data.get("summary", "") or "").strip()
                base["keywords"] = normalize_keywords(data.get("keywords", []))
                base["topic_label"] = (data.get("topic_label", "misc") or "misc").strip()
                base["enrich_ok"] = True
            except Exception as exc:  # noqa: BLE001
                base["enrich_error"] = str(exc)

            enriched.append(base)
            triage_counts[base["triage"]] = triage_counts.get(base["triage"], 0) + 1

            if not args.dry_run and args.save_every > 0 and len(enriched) % args.save_every == 0:
                write_json_atomic(args.output, enriched)
                print(f"checkpoint saved: {len(enriched)}/{len(rows)}", flush=True)
            time.sleep(args.delay)
    except KeyboardInterrupt:
        if not args.dry_run:
            write_json_atomic(args.output, enriched)
            print(f"\nInterrupted. Saved partial progress: {len(enriched)}/{len(rows)}")
        raise

    if not args.dry_run:
        write_json_atomic(args.output, enriched)

    print("\nEnrichment summary:")
    print(f"  Input rows : {len(rows)}")
    print(f"  Output     : {args.output}")
    if args.dry_run:
        print("  [DRY RUN] Output file not written.")
    for key in sorted(triage_counts):
        print(f"  {key:18}: {triage_counts[key]}")


if __name__ == "__main__":
    main()
