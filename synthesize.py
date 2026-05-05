#!/usr/bin/env python3
"""Cluster enriched bookmarks and generate topic guide markdown files."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).parent
DEFAULT_INPUT = REPO_ROOT / "data" / "enriched_bookmarks.json"
DEFAULT_TOPICS_JSON = REPO_ROOT / "data" / "topic_guides.json"
DEFAULT_TOPICS_DIR = REPO_ROOT / "data" / "topic_guides"


def slug(text: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return token or "misc"


def pick_topic(item: dict) -> str:
    label = (item.get("topic_label") or "").strip()
    if label:
        return label
    keywords = item.get("keywords") or []
    if keywords:
        return keywords[0].replace("_", " ")
    folder = (item.get("folder_path") or "misc").split(">")[-1].strip()
    return folder or "misc"


def render_topic_markdown(topic: str, items: list[dict]) -> str:
    lines = [f"# Topic Guide: {topic}", "", "## Overview", ""]
    lines.append(f"Curated quick guide for {len(items)} bookmarks in this topic.")
    lines.extend(["", "## Resources", ""])
    for item in items:
        summary = item.get("synthesis_summary") or item.get("llm_description") or ""
        keywords = ", ".join(item.get("keywords") or [])
        lines.append(f"- [{item.get('title', 'Untitled')}]({item.get('url', '')})")
        if summary:
            lines.append(f"  - {summary}")
        if keywords:
            lines.append(f"  - keywords: {keywords}")
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate clustered topic guides from enriched bookmarks")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_TOPICS_JSON)
    parser.add_argument("--topics-dir", type=Path, default=DEFAULT_TOPICS_DIR)
    parser.add_argument("--min-items", type=int, default=2)
    args = parser.parse_args()

    rows = json.loads(args.input.read_text(encoding="utf-8"))

    clusters: dict[str, list[dict]] = defaultdict(list)
    for item in rows:
        if not item.get("enrich_ok"):
            continue
        if item.get("triage") not in {"knowledge_resource", "tool"}:
            continue
        clusters[pick_topic(item)].append(item)

    args.topics_dir.mkdir(parents=True, exist_ok=True)

    guides: list[dict] = []
    for topic, items in sorted(clusters.items(), key=lambda kv: len(kv[1]), reverse=True):
        if len(items) < args.min_items:
            continue
        topic_slug = slug(topic)
        md_path = args.topics_dir / f"{topic_slug}.md"
        md = render_topic_markdown(topic, items)
        md_path.write_text(md, encoding="utf-8")
        guides.append(
            {
                "topic": topic,
                "topic_slug": topic_slug,
                "count": len(items),
                "markdown_path": str(md_path.relative_to(REPO_ROOT)),
                "bookmarks": [
                    {
                        "title": item.get("title", "Untitled"),
                        "url": item.get("url", ""),
                    }
                    for item in items
                ],
            }
        )

    args.output.write_text(json.dumps(guides, indent=2, ensure_ascii=True), encoding="utf-8")

    print("Synthesis summary:")
    print(f"  Guides created : {len(guides)}")
    print(f"  Output manifest: {args.output}")
    print(f"  Guides folder  : {args.topics_dir}")


if __name__ == "__main__":
    main()
