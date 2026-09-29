"""Ingest conference talks from structured Markdown into canonical graph JSONL."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from kg_ingest import (
    IngestError,
    _add_record,
    _load_jsonl,
    _relation,
    _render_jsonl,
    _slug,
    _source_ref,
    _without_source,
    parse_scope_spec,
)


OUTPUT_FILES = (
    "entities.jsonl",
    "relations.jsonl",
    "aliases.jsonl",
    "concepts.jsonl",
    "scopes.jsonl",
)
MISSING_AFFILIATION = "affiliation not stated on page"


def _parse_authors(value: str, title: str) -> list[dict[str, str | None]]:
    authors = []
    for raw_author in (part.strip() for part in value.split(";")):
        if not raw_author:
            continue
        match = re.fullmatch(r"(.+?)\s*\((.+)\)", raw_author)
        if not match:
            raise IngestError(f"invalid author entry for {title!r}: {raw_author!r}")
        name = match.group(1).strip()
        affiliation = match.group(2).strip()
        if not name:
            raise IngestError(f"empty author name for {title!r}")
        authors.append(
            {
                "name": name,
                "affiliation": None if affiliation.lower() == MISSING_AFFILIATION else affiliation,
            }
        )
    if not authors:
        raise IngestError(f"talk {title!r} has no authors")
    return authors


def parse_talks_markdown(text: str) -> list[dict[str, Any]]:
    """Parse track-grouped Markdown talks with explicit author lines."""
    talks: list[dict[str, Any]] = []
    track: str | None = None
    title: str | None = None
    body: list[str] = []

    def finish_talk() -> None:
        nonlocal title, body
        if title is None:
            return
        content = [line.rstrip() for line in body]
        while content and not content[0].strip():
            content.pop(0)
        while content and not content[-1].strip():
            content.pop()
        if not content or not content[0].startswith("**Authors:**"):
            raise IngestError(f"talk {title!r} has no **Authors:** line")
        author_text = content.pop(0).removeprefix("**Authors:**").strip()
        while content and not content[0].strip():
            content.pop(0)
        abstract = "\n".join(content).strip()
        if not abstract:
            raise IngestError(f"talk {title!r} has no abstract")
        talks.append(
            {
                "track": track,
                "title": title,
                "authors": _parse_authors(author_text, title),
                "abstract": abstract,
            }
        )
        title = None
        body = []

    for line in text.splitlines():
        if line.startswith("## "):
            finish_talk()
            track = line.removeprefix("## ").strip()
            if track == "Table of Contents":
                track = None
            continue
        if line.startswith("### "):
            finish_talk()
            if not track:
                raise IngestError(f"talk heading appears outside a track: {line}")
            title = line.removeprefix("### ").strip()
            if not title:
                raise IngestError("talk has an empty title")
            continue
        if title is not None:
            if line.strip() == "---":
                finish_talk()
            else:
                body.append(line)
    finish_talk()

    if not talks:
        raise IngestError("source contains no talks")
    return talks


def records_from_talks(
    talks: list[dict[str, Any]],
    source_id: str,
    source_url: str | None = None,
    scopes: list[dict[str, str]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Convert parsed talks into canonical entities, concepts, and relations."""
    entities: dict[str, dict[str, Any]] = {}
    concepts: dict[str, dict[str, Any]] = {}
    relations: dict[str, dict[str, Any]] = {}
    aliases: dict[str, dict[str, Any]] = {}
    scope_records: dict[str, dict[str, Any]] = {}

    event_id = "event:conference:cosmo26-2026"
    event_ref = _source_ref(source_id, "cosmo26-2026", source_url)
    event_properties: dict[str, Any] = {
        "description": "29th International Conference on Particle Physics & Cosmology",
        "start_date": "2026-08-24",
        "end_date": "2026-08-28",
        "location": "Leiden University, Leiden, Netherlands",
        "website": "https://cosmo-26.lorentz.leidenuniv.nl/",
    }
    _add_record(
        entities,
        {
            "id": event_id,
            "type": "Event",
            "rdf_type": "schema:Event",
            "name": "Cosmo-26",
            "properties": event_properties,
            "source_refs": [event_ref],
        },
    )

    for talk in talks:
        title = str(talk["title"])
        track = str(talk["track"])
        talk_id = f"talk:cosmo26:{_slug(title)}"
        track_id = f"concept:cosmo26-track:{_slug(track)}"
        ref = _source_ref(source_id, _slug(title), source_url)
        properties = {"description": talk["abstract"]}
        if source_url:
            properties["source_url"] = source_url
        _add_record(
            entities,
            {
                "id": talk_id,
                "type": "Talk",
                "rdf_type": "fabio:Presentation",
                "name": title,
                "properties": properties,
                "source_refs": [ref],
            },
        )
        _add_record(
            concepts,
            {
                "id": track_id,
                "type": "Concept",
                "rdf_type": "skos:Concept",
                "name": track,
                "properties": {
                    "description": f"Cosmo-26 topic track: {track}",
                    "scheme": "Cosmo-26 topic tracks",
                },
                "source_refs": [ref],
            },
        )
        _add_record(relations, _relation(talk_id, "presented_at", event_id, ref))
        _add_record(relations, _relation(talk_id, "about", track_id, ref))

        for author in talk["authors"]:
            name = str(author["name"])
            person_id = f"person:name:{_slug(name)}"
            _add_record(
                entities,
                {
                    "id": person_id,
                    "type": "Person",
                    "rdf_type": "foaf:Person",
                    "name": name,
                    "source_refs": [ref],
                },
            )
            _add_record(relations, _relation(talk_id, "presented_by", person_id, ref))
            alias_id = f"alias:author_name:{_slug(name)}"
            _add_record(
                aliases,
                {
                    "id": alias_id,
                    "alias": name,
                    "kind": "author_name",
                    "entity_id": person_id,
                    "source_refs": [ref],
                },
            )

            affiliation = author.get("affiliation")
            if affiliation:
                institution_id = f"institution:name:{_slug(str(affiliation))}"
                _add_record(
                    entities,
                    {
                        "id": institution_id,
                        "type": "Institution",
                        "rdf_type": "foaf:Organization",
                        "name": affiliation,
                        "properties": {"institution_kind": "research institution"},
                        "source_refs": [ref],
                    },
                )
                _add_record(relations, _relation(person_id, "affiliated_with", institution_id, ref))

    scoped_objects = list(entities.values()) + list(concepts.values())
    for scope in scopes or []:
        scope_id = f"scope:{scope['key']}"
        scope_properties: dict[str, Any] = {"scope_kind": scope["kind"]}
        if source_url:
            scope_properties["source_url"] = source_url
        for item in scoped_objects:
            item_refs = [ref for ref in item.get("source_refs", []) if ref.get("source_id") == source_id]
            if not item_refs:
                continue
            _add_record(
                scope_records,
                {
                    "id": scope_id,
                    "type": "GraphScope",
                    "rdf_type": "schema:Collection",
                    "name": scope["name"],
                    "properties": scope_properties,
                    "source_refs": item_refs,
                },
            )
            for item_ref in item_refs:
                _add_record(relations, _relation(item["id"], "in_scope", scope_id, item_ref))
                _add_record(relations, _relation(scope_id, "contains", item["id"], item_ref))

    return {
        "entities.jsonl": sorted(entities.values(), key=lambda record: record["id"]),
        "relations.jsonl": sorted(relations.values(), key=lambda record: record["id"]),
        "aliases.jsonl": sorted(aliases.values(), key=lambda record: record["id"]),
        "concepts.jsonl": sorted(concepts.values(), key=lambda record: record["id"]),
        "scopes.jsonl": sorted(scope_records.values(), key=lambda record: record["id"]),
    }


def ingest_talks(
    input_path: Path,
    graph_dir: Path,
    source_id: str,
    source_url: str | None = None,
    dry_run: bool = False,
    scopes: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Replace one talk source's contributions while preserving all other sources."""
    try:
        text = input_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise IngestError(f"source not found: {input_path}") from exc

    talks = parse_talks_markdown(text)
    generated = records_from_talks(talks, source_id, source_url, scopes)
    files: dict[str, dict[str, Any]] = {}
    for filename in OUTPUT_FILES:
        new_records = generated[filename]
        output_path = graph_dir / filename
        retained = [
            record
            for record in (_without_source(item, source_id) for item in _load_jsonl(output_path))
            if record
        ]
        merged = {record["id"]: record for record in retained}
        for record in new_records:
            _add_record(merged, record)
        final_records = sorted(merged.values(), key=lambda record: record["id"])
        rendered = _render_jsonl(final_records)
        previous = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        changed = rendered != previous
        files[filename] = {
            "records": len(final_records),
            "source_records": len(new_records),
            "changed": changed,
        }
        if changed and not dry_run:
            graph_dir.mkdir(parents=True, exist_ok=True)
            temporary = output_path.with_suffix(output_path.suffix + ".tmp")
            temporary.write_text(rendered, encoding="utf-8")
            temporary.replace(output_path)

    return {
        "source_id": source_id,
        "input": str(input_path),
        "dry_run": dry_run,
        "talks": len(talks),
        "tracks": len({talk["track"] for talk in talks}),
        "files": files,
        "changed_files": sum(file["changed"] for file in files.values()),
        "scopes": [scope["key"] for scope in scopes or []],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest structured conference-talk Markdown")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--graph-dir", type=Path, default=Path("graph"))
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--source-url")
    parser.add_argument(
        "--scope",
        action="append",
        default=[],
        metavar="KEY[=KIND:NAME]",
        help="Assign generated records to a graph scope; repeat for multiple scopes",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        scopes = [parse_scope_spec(value) for value in args.scope]
        report = ingest_talks(
            args.input,
            args.graph_dir,
            args.source_id,
            args.source_url,
            args.dry_run,
            scopes,
        )
    except IngestError as exc:
        raise SystemExit(f"Talk ingestion failed: {exc}") from exc
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
