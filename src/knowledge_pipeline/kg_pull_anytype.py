"""Plan and apply additive Anytype edits to the canonical graph."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kg_ingest import _relation, _render_jsonl, _slug


PROPERTY_PREDICATES = {
    "authored_by": "authored_by",
    "corporate_authors": "corporate_authored_by",
    "about": "about",
    "affiliated_with": "affiliated_with",
    "member_of": "member_of",
    "broader": "broader",
    "related_to": "related_to",
    "presented_by": "presented_by",
    "presented_at": "presented_at",
    "scopes": "in_scope",
    "members": "contains",
}


class PullError(ValueError):
    """Raised when live Anytype state cannot be merged unambiguously."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PullError(f"invalid JSON in {path}:{line_number}: {exc.msg}") from exc
        if not isinstance(row, dict) or not row.get("id"):
            raise PullError(f"record in {path}:{line_number} must have an id")
        rows.append(row)
    return rows


def plan_anytype_pull(graph_dir: Path, live_state: dict[str, Any]) -> dict[str, Any]:
    """Build an additive merge plan without changing either system."""
    object_rows = (
        _read_jsonl(graph_dir / "entities.jsonl")
        + _read_jsonl(graph_dir / "concepts.jsonl")
        + _read_jsonl(graph_dir / "scopes.jsonl")
    )
    canonical = {row["id"]: row for row in object_rows}
    if len(canonical) != len(object_rows):
        raise PullError("canonical graph contains duplicate object IDs")

    live_objects = live_state.get("objects")
    if not isinstance(live_objects, list):
        raise PullError("Anytype pull returned no object list")

    by_anytype_id: dict[str, str] = {}
    live_canonical_ids: dict[str, str] = {}
    concepts = []
    stamps = []
    for item in live_objects:
        anytype_id = item.get("anytype_id")
        if not isinstance(anytype_id, str) or not anytype_id:
            raise PullError("every live object requires an Anytype ID")
        canonical_id = item.get("canonical_id")
        if canonical_id:
            if canonical_id in live_canonical_ids:
                raise PullError(f"duplicate canonical ID in Anytype: {canonical_id}")
            live_canonical_ids[canonical_id] = anytype_id
            if canonical_id in canonical:
                by_anytype_id[anytype_id] = canonical_id
            continue
        if item.get("type_key") != "concept":
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            raise PullError(f"new Anytype Concept {anytype_id} has no name")
        canonical_id = f"concept:anytype:{_slug(name)}"
        existing = canonical.get(canonical_id)
        source_id = f"anytype:live:{anytype_id}"
        is_recoverable = existing and any(
            ref.get("source_id") == source_id for ref in existing.get("source_refs", [])
        )
        if (existing and not is_recoverable) or canonical_id in live_canonical_ids:
            raise PullError(
                f"cannot adopt Anytype Concept {name!r}: canonical ID collision {canonical_id}"
            )
        live_canonical_ids[canonical_id] = anytype_id
        by_anytype_id[anytype_id] = canonical_id
        source_ref = {
            "source_id": source_id,
            "source_url": f"anytype://{anytype_id}",
        }
        if not is_recoverable:
            concepts.append(
                {
                    "id": canonical_id,
                    "name": name,
                    "properties": {"scheme": "Anytype"},
                    "rdf_type": "skos:Concept",
                    "source_refs": [source_ref],
                    "type": "Concept",
                }
            )
        stamps.append(
            {"anytype_id": anytype_id, "canonical_id": canonical_id, "type_key": "concept"}
        )

    existing_relations = _read_jsonl(graph_dir / "relations.jsonl")
    existing_triples = {
        (row.get("subject"), row.get("predicate"), row.get("object")) for row in existing_relations
    }
    relations = []
    unresolved = []
    for item in live_objects:
        subject = by_anytype_id.get(item["anytype_id"])
        if not subject:
            continue
        relation_values = item.get("relation_values") or {}
        for property_key, predicate in PROPERTY_PREDICATES.items():
            for target_anytype_id in relation_values.get(property_key) or []:
                object_id = by_anytype_id.get(target_anytype_id)
                if not object_id:
                    unresolved.append(
                        {
                            "subject": subject,
                            "property_key": property_key,
                            "target_anytype_id": target_anytype_id,
                        }
                    )
                    continue
                triple = (subject, predicate, object_id)
                if triple in existing_triples:
                    continue
                ref = {
                    "source_id": f"anytype:live:{item['anytype_id']}",
                    "source_url": f"anytype://{item['anytype_id']}",
                }
                relations.append(_relation(subject, predicate, object_id, ref))
                existing_triples.add(triple)

    concepts.sort(key=lambda row: row["id"])
    relations.sort(key=lambda row: row["id"])
    stamps.sort(key=lambda row: row["canonical_id"])
    unresolved.sort(key=lambda row: (row["subject"], row["property_key"], row["target_anytype_id"]))
    return {
        "space_id": live_state.get("space_id"),
        "objects_scanned": len(live_objects),
        "concepts": concepts,
        "relations": relations,
        "stamps": stamps,
        "unresolved": unresolved,
    }


def apply_anytype_pull(graph_dir: Path, plan: dict[str, Any]) -> None:
    """Atomically append a previously validated pull plan to canonical JSONL."""
    for filename, additions in (
        ("concepts.jsonl", plan.get("concepts") or []),
        ("relations.jsonl", plan.get("relations") or []),
    ):
        if not additions:
            continue
        path = graph_dir / filename
        merged = {row["id"]: row for row in _read_jsonl(path)}
        for row in additions:
            if row["id"] in merged:
                raise PullError(f"pull plan conflicts with existing record: {row['id']}")
            merged[row["id"]] = row
        rendered = _render_jsonl(sorted(merged.values(), key=lambda row: row["id"]))
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(path)
