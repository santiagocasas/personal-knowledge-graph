"""Build a validated Anytype projection manifest from canonical graph JSONL."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


TYPE_KEYS = {
    "Person": "person",
    "Institution": "institution",
    "Paper": "paper",
    "Talk": "talk",
    "Concept": "concept",
    "Event": "event",
    "GraphScope": "graph_scope",
}
RELATION_PROPERTIES = {
    "authored_by": "authored_by",
    "corporate_authored_by": "corporate_authors",
    "about": "about",
    "affiliated_with": "affiliated_with",
    "member_of": "member_of",
    "broader": "broader",
    "related_to": "related_to",
    "presented_by": "presented_by",
    "presented_at": "presented_at",
    "in_scope": "scopes",
    "contains": "members",
}
TYPE_RELATION_PROPERTIES = {
    "Person": ["affiliated_with", "member_of", "scopes"],
    "Institution": ["scopes"],
    "Paper": ["authored_by", "corporate_authors", "about", "scopes"],
    "Talk": ["presented_by", "presented_at", "about", "scopes"],
    "Concept": ["broader", "related_to", "scopes"],
    "Event": ["scopes"],
    "GraphScope": ["members"],
}


class MaterializationError(ValueError):
    """Raised when canonical graph records cannot be safely projected."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise MaterializationError(f"graph file not found: {path}")
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MaterializationError(f"invalid JSON in {path}:{line_number}: {exc.msg}") from exc
        if not isinstance(row, dict):
            raise MaterializationError(f"record in {path}:{line_number} must be an object")
        rows.append(row)
    return rows


def _project_properties(
    row: dict[str, Any], projection: dict[str, Any] | None
) -> dict[str, Any]:
    properties = dict(row.get("properties") or {})
    if row.get("type") != "Paper" or not projection:
        return properties
    author_count = properties.get("author_count")
    maximum = projection.get("full_expansion_max_authors", 20)
    first_count = projection.get("large_author_list_first_authors", 5)
    author_list = properties.get("author_list")
    if not isinstance(author_count, int) or author_count <= maximum or not isinstance(author_list, str):
        return properties
    authors = [author.strip() for author in author_list.split(";") if author.strip()]
    visible = authors[:first_count]
    remaining = max(author_count - len(visible), 0)
    properties["author_list"] = "; ".join(visible) + f"; and {remaining} additional authors"
    return properties


def load_materialization_manifest(
    graph_dir: Path,
    space_id: str,
    projection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load canonical entities and supported relations into a runtime manifest."""
    objects = _read_jsonl(graph_dir / "entities.jsonl") + _read_jsonl(graph_dir / "concepts.jsonl")
    scopes_path = graph_dir / "scopes.jsonl"
    if scopes_path.exists():
        objects += _read_jsonl(scopes_path)
    relations = _read_jsonl(graph_dir / "relations.jsonl")

    by_id: dict[str, dict[str, Any]] = {}
    projected = []
    for row in objects:
        canonical_id = row.get("id")
        type_name = row.get("type")
        name = row.get("name")
        if not isinstance(canonical_id, str) or not canonical_id:
            raise MaterializationError("every object requires a non-empty id")
        if canonical_id in by_id:
            raise MaterializationError(f"duplicate canonical object id: {canonical_id}")
        if type_name not in TYPE_KEYS:
            raise MaterializationError(f"unsupported object type {type_name!r} for {canonical_id}")
        if not isinstance(name, str) or not name.strip():
            raise MaterializationError(f"object {canonical_id} requires a non-empty name")
        by_id[canonical_id] = row
        projected.append(
            {
                "canonical_id": canonical_id,
                "type_key": TYPE_KEYS[type_name],
                "name": name.strip(),
                "properties": _project_properties(row, projection),
                "relation_properties": TYPE_RELATION_PROPERTIES[type_name],
            }
        )

    projected_relations = []
    for row in relations:
        predicate = row.get("predicate")
        if predicate not in RELATION_PROPERTIES:
            continue
        subject = row.get("subject")
        object_id = row.get("object")
        if subject not in by_id or object_id not in by_id:
            raise MaterializationError(
                f"relation {row.get('id', '<unknown>')} has an unprojected endpoint"
            )
        projected_relations.append(
            {
                "subject": subject,
                "property_key": RELATION_PROPERTIES[predicate],
                "object": object_id,
            }
        )

    projected.sort(key=lambda row: row["canonical_id"])
    projected_relations.sort(key=lambda row: (row["subject"], row["property_key"], row["object"]))
    return {
        "space_id": space_id,
        "objects": projected,
        "relations": projected_relations,
    }
