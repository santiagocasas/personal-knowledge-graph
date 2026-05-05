#!/usr/bin/env python3
"""Cluster enriched bookmarks and generate topic guide markdown files."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
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


def item_text(item: dict) -> str:
    values = [
        item.get("title", ""),
        item.get("url", ""),
        item.get("folder_path", ""),
        item.get("topic_label", ""),
        item.get("llm_description", ""),
        item.get("synthesis_summary", ""),
        " ".join(item.get("keywords") or []),
        " ".join(item.get("matched_tags") or []),
        " ".join(item.get("proposed_tags") or []),
    ]
    return " ".join(str(value) for value in values).lower()


def contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def pick_broad_topic(item: dict) -> str:
    text = item_text(item)
    folder = (item.get("folder_path") or "").lower()

    if contains_any(
        text,
        (
            "helmholtz",
            " hmc",
            "hifis",
            "gfz",
            "hereon",
            "fz-juelich",
            "dlr",
            "ufz",
            "kit.edu",
            "desy",
            "hzb",
            "dkfz",
            "geomar",
            "awip",
        ),
    ):
        return "Helmholtz and HMC"
    if contains_any(
        text,
        (
            "artificial intelligence",
            " ai ",
            " llm",
            "large language",
            "agent",
            "multi-agent",
            "machine learning",
            "deep learning",
            "neural",
            "chatgpt",
            "openai",
            "scientific ai",
            "blablador",
        ),
    ) or "/ai" in folder:
        return "AI, LLMs, and Scientific Agents"
    if contains_any(text, ("knowledge graph", "ontology", "semantic web", "rdf", "linked data", "sparql", "owl", "schema.org", "wikidata")):
        return "Knowledge Graphs and Ontologies"
    if contains_any(text, ("metadata standard", "datacite", "dublin core", "oai-pmh", "schema", "pid", "persistent identifier", "doi", "orcid", "purl", "metadata extraction")):
        return "Metadata Standards, PIDs, and Scholarly Metadata"
    if contains_any(text, ("fair", "research data management", " rdm", "data management plan", "data stewardship", "open science", "data quality")):
        return "Research Data Management and FAIR Practice"
    if contains_any(text, ("repository", "archive", "zenodo", "infrastructure", "nfdi", "dataset", "data portal", "catalog", "commons", "research data infrastructure")):
        return "Research Data Infrastructure and Repositories"
    if contains_any(text, ("training", "course", "workshop", "tutorial", "learning", "education", "onboarding", "lecture")):
        return "Training and Learning Resources"
    if contains_any(text, ("software", "github", "tool", "cli", "workflow", "reproducible", "open source", "package", "library")):
        return "Research Software and Open Tools"
    if contains_any(text, ("cloud", "service", "collaboration", "platform")):
        return "Cloud and Collaboration Services"
    if contains_any(text, ("conference", "event", "workshop", "community", "forum")):
        return "Events and Communities"
    return "General Research Resources"


def render_topic_markdown(topic: str, items: list[dict]) -> str:
    # The Anytype object name already renders as the page title, so avoid a
    # duplicate H1 in the page body.
    lines = ["## Overview", ""]
    lines.append(f"Curated guide for {len(items)} linked bookmarks in this broader topic family.")

    subtopics = Counter((item.get("topic_label") or "Misc").strip() or "Misc" for item in items)
    lines.extend(["", "## Subtopics", ""])
    for label, count in subtopics.most_common(12):
        lines.append(f"- {label}: {count}")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in sorted(items, key=lambda row: ((row.get("topic_label") or "Misc"), row.get("title") or "")):
        grouped[(item.get("topic_label") or "Misc").strip() or "Misc"].append(item)

    lines.extend(["", "## Resources", ""])
    for label, group in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0].lower())):
        lines.extend([f"### {label}", ""])
        for item in group:
            summary = item.get("synthesis_summary") or item.get("llm_description") or ""
            keywords = ", ".join((item.get("keywords") or [])[:6])
            lines.append(f"- [{item.get('title', 'Untitled')}]({item.get('url', '')})")
            if summary:
                lines.append(f"  - {summary}")
            if keywords:
                lines.append(f"  - keywords: {keywords}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def guide_tags(items: list[dict]) -> list[str]:
    seen: set[str] = set()
    tags: list[str] = []
    for item in items:
        values = []
        values.extend(item.get("matched_tags") or [])
        values.extend(item.get("proposed_tags") or [])
        values.extend((item.get("keywords") or [])[:3])
        for value in values:
            tag = str(value).strip()
            key = tag.lower()
            if tag and key not in seen:
                seen.add(key)
                tags.append(tag)
    return tags[:12]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate clustered topic guides from enriched bookmarks")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_TOPICS_JSON)
    parser.add_argument("--topics-dir", type=Path, default=DEFAULT_TOPICS_DIR)
    parser.add_argument("--min-items", type=int, default=2)
    parser.add_argument("--topic-mode", choices=["broad", "specific"], default="broad")
    args = parser.parse_args()

    rows = json.loads(args.input.read_text(encoding="utf-8"))

    clusters: dict[str, list[dict]] = defaultdict(list)
    for item in rows:
        if not item.get("enrich_ok"):
            continue
        if item.get("triage") not in {"knowledge_resource", "tool"}:
            continue
        topic = pick_broad_topic(item) if args.topic_mode == "broad" else pick_topic(item)
        clusters[topic].append(item)

    args.topics_dir.mkdir(parents=True, exist_ok=True)
    for old_file in args.topics_dir.glob("*.md"):
        old_file.unlink()

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
                "tags": guide_tags(items),
                "markdown": md,
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
