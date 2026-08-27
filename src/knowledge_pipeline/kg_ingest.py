"""Ingest BibTeX sources into the canonical personal knowledge graph JSONL."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any


class IngestError(ValueError):
    """Raised when a source cannot be converted safely into graph records."""


SCOPE_KEY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_scope_spec(value: str) -> dict[str, str]:
    """Parse KEY or KEY=KIND:NAME into a canonical graph scope definition."""
    key, separator, details = value.strip().partition("=")
    if not SCOPE_KEY.fullmatch(key):
        raise IngestError(f"invalid scope key {key!r}; use lowercase words separated by hyphens")
    kind = "collection"
    name = key.replace("-", " ").title()
    if separator:
        kind, name_separator, supplied_name = details.partition(":")
        kind = kind.strip()
        name = supplied_name.strip() if name_separator else name
        if not kind or not name:
            raise IngestError(f"invalid scope {value!r}; expected KEY=KIND:NAME")
    return {"key": key, "kind": kind, "name": name}


def _split_top_level(value: str, delimiter: str = ",") -> list[str]:
    parts: list[str] = []
    start = 0
    brace_depth = 0
    paren_depth = 0
    quoted = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            quoted = not quoted
            continue
        if quoted:
            continue
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth -= 1
        elif char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth -= 1
        elif char == delimiter and brace_depth == 0 and paren_depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    tail = value[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _unwrap(value: str) -> str:
    value = value.strip()
    while len(value) >= 2 and (
        (value[0] == "{" and value[-1] == "}") or (value[0] == '"' and value[-1] == '"')
    ):
        value = value[1:-1]
    return re.sub(r"\s+", " ", value).strip()


def parse_bibtex(text: str) -> list[dict[str, Any]]:
    """Parse regular BibTeX entries while preserving LaTeX field values."""
    entries: list[dict[str, Any]] = []
    cursor = 0
    entry_start = re.compile(r"@([A-Za-z]+)\s*([\{(])")

    while match := entry_start.search(text, cursor):
        entry_type = match.group(1).lower()
        opening = match.group(2)
        closing = "}" if opening == "{" else ")"
        body_start = match.end()
        depth = 1
        quoted = False
        escaped = False
        index = body_start
        while index < len(text) and depth:
            char = text[index]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = not quoted
            elif not quoted:
                if char == opening:
                    depth += 1
                elif char == closing:
                    depth -= 1
            index += 1
        if depth:
            raise IngestError(f"unterminated @{entry_type} entry near character {match.start()}")

        body = text[body_start : index - 1]
        chunks = _split_top_level(body)
        if not chunks or not chunks[0]:
            raise IngestError(f"@{entry_type} entry has no citation key")
        fields: dict[str, str] = {}
        for chunk in chunks[1:]:
            if not chunk:
                continue
            key, separator, raw_value = chunk.partition("=")
            if not separator:
                raise IngestError(f"invalid field in {chunks[0]}: {chunk}")
            fields[key.strip().lower()] = _unwrap(raw_value)
        entries.append({"entry_type": entry_type, "citation_key": chunks[0].strip(), "fields": fields})
        cursor = index

    if not entries:
        raise IngestError("source contains no BibTeX entries")
    return entries


def _slug(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")
    return slug or hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _normalise_doi(value: str) -> str:
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.I).lower()


def _author_name(value: str) -> str:
    value = _unwrap(value)
    if "," not in value:
        return value
    family, given = (part.strip() for part in value.split(",", 1))
    return f"{given} {family}".strip()


def _source_ref(source_id: str, citation_key: str, source_url: str | None) -> dict[str, str]:
    ref = {"source_id": source_id, "citation_key": citation_key}
    if source_url:
        ref["source_url"] = source_url
    return ref


def _add_record(records: dict[str, dict[str, Any]], record: dict[str, Any]) -> None:
    existing = records.get(record["id"])
    if not existing:
        records[record["id"]] = record
        return
    refs = {json.dumps(ref, sort_keys=True): ref for ref in existing.get("source_refs", [])}
    refs.update({json.dumps(ref, sort_keys=True): ref for ref in record.get("source_refs", [])})
    existing["source_refs"] = [refs[key] for key in sorted(refs)]
    if "properties" in record:
        existing.setdefault("properties", {}).update(record["properties"])


def _relation(subject: str, predicate: str, object_id: str, ref: dict[str, str]) -> dict[str, Any]:
    digest = hashlib.sha256(f"{subject}\0{predicate}\0{object_id}".encode("utf-8")).hexdigest()[:20]
    return {
        "id": f"relation:{digest}",
        "subject": subject,
        "predicate": predicate,
        "object": object_id,
        "source_refs": [ref],
    }


def records_from_bibtex(
    entries: list[dict[str, Any]],
    source_id: str,
    source_url: str | None = None,
    scopes: list[dict[str, str]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    entities: dict[str, dict[str, Any]] = {}
    concepts: dict[str, dict[str, Any]] = {}
    relations: dict[str, dict[str, Any]] = {}
    aliases: dict[str, dict[str, Any]] = {}
    scope_records: dict[str, dict[str, Any]] = {}

    for entry in entries:
        fields = entry["fields"]
        citation_key = entry["citation_key"]
        title = fields.get("title")
        if not title:
            raise IngestError(f"{citation_key} has no title")
        doi = _normalise_doi(fields["doi"]) if fields.get("doi") else None
        arxiv_id = fields.get("eprint") if fields.get("archiveprefix", "").lower() == "arxiv" else None
        paper_id = (
            f"paper:doi:{doi}"
            if doi
            else f"paper:arxiv:{arxiv_id.lower()}"
            if arxiv_id
            else f"paper:bibtex:{_slug(citation_key)}"
        )
        paper_url = f"https://doi.org/{doi}" if doi else f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else source_url
        ref = _source_ref(source_id, citation_key, source_url)

        raw_authors = fields.get("author", "")
        author_parts = [part.strip() for part in re.split(r"\s+and\s+", raw_authors, flags=re.I) if part.strip()]
        incomplete_authors = any(part.lower() == "others" for part in author_parts)
        explicit_authors = [part for part in author_parts if part.lower() != "others"]
        year = int(fields["year"]) if fields.get("year", "").isdigit() else None
        properties: dict[str, Any] = {
            "citation_key": citation_key,
            "entry_type": entry["entry_type"],
            "author_list": raw_authors,
            "author_list_incomplete": incomplete_authors,
        }
        optional_properties = {
            "doi": f"https://doi.org/{doi}" if doi else None,
            "arxiv_id": arxiv_id,
            "publication_year": year,
            "source_url": paper_url,
            "journal": fields.get("journal"),
            "volume": fields.get("volume"),
            "pages": fields.get("pages"),
            "primary_class": fields.get("primaryclass"),
        }
        properties.update({key: value for key, value in optional_properties.items() if value is not None})
        _add_record(
            entities,
            {
                "id": paper_id,
                "type": "Paper",
                "rdf_type": "bibo:AcademicArticle",
                "name": title,
                "identifiers": {key: value for key, value in {"doi": doi, "arxiv": arxiv_id}.items() if value},
                "properties": properties,
                "source_refs": [ref],
            },
        )

        alias_values = [("bibtex_key", citation_key)]
        if doi:
            alias_values.append(("doi", doi))
        if arxiv_id:
            alias_values.append(("arxiv", arxiv_id))
        for kind, value in alias_values:
            alias_id = f"alias:{kind}:{_slug(value)}"
            _add_record(
                aliases,
                {
                    "id": alias_id,
                    "alias": value,
                    "kind": kind,
                    "entity_id": paper_id,
                    "source_refs": [ref],
                },
            )

        for raw_author in explicit_authors:
            name = _author_name(raw_author)
            person_id = f"person:name:{_slug(name)}"
            _add_record(
                entities,
                {
                    "id": person_id,
                    "type": "Person",
                    "rdf_type": "foaf:Person",
                    "name": name,
                    "properties": {"name_as_cited": raw_author},
                    "source_refs": [ref],
                },
            )
            relation = _relation(paper_id, "authored_by", person_id, ref)
            _add_record(relations, relation)
            alias_id = f"alias:author_name:{_slug(raw_author)}"
            _add_record(
                aliases,
                {
                    "id": alias_id,
                    "alias": raw_author,
                    "kind": "author_name",
                    "entity_id": person_id,
                    "source_refs": [ref],
                },
            )

        collaboration = fields.get("collaboration")
        if collaboration:
            organisation_name = collaboration if collaboration.lower().endswith("collaboration") else f"{collaboration} Collaboration"
            organisation_id = f"institution:name:{_slug(organisation_name)}"
            _add_record(
                entities,
                {
                    "id": organisation_id,
                    "type": "Institution",
                    "rdf_type": "foaf:Organization",
                    "name": organisation_name,
                    "properties": {"institution_kind": "collaboration"},
                    "source_refs": [ref],
                },
            )
            relation = _relation(paper_id, "corporate_authored_by", organisation_id, ref)
            _add_record(relations, relation)

        primary_class = fields.get("primaryclass")
        if primary_class:
            concept_id = f"concept:arxiv:{_slug(primary_class)}"
            _add_record(
                concepts,
                {
                    "id": concept_id,
                    "type": "Concept",
                    "rdf_type": "skos:Concept",
                    "name": primary_class,
                    "properties": {"notation": primary_class, "scheme": "arXiv taxonomy"},
                    "source_refs": [ref],
                },
            )
            relation = _relation(paper_id, "about", concept_id, ref)
            _add_record(relations, relation)

    scoped_objects = list(entities.values()) + list(concepts.values())
    for scope in scopes or []:
        scope_id = f"scope:{scope['key']}"
        scope_properties = {"scope_kind": scope["kind"]}
        if source_url:
            scope_properties["source_url"] = source_url
        for item in scoped_objects:
            for ref in item.get("source_refs", []):
                if ref.get("source_id") != source_id:
                    continue
                _add_record(
                    scope_records,
                    {
                        "id": scope_id,
                        "type": "GraphScope",
                        "rdf_type": "schema:Collection",
                        "name": scope["name"],
                        "properties": scope_properties,
                        "source_refs": [ref],
                    },
                )
                _add_record(relations, _relation(item["id"], "in_scope", scope_id, ref))
                _add_record(relations, _relation(scope_id, "contains", item["id"], ref))

    return {
        "entities.jsonl": sorted(entities.values(), key=lambda record: record["id"]),
        "relations.jsonl": sorted(relations.values(), key=lambda record: record["id"]),
        "aliases.jsonl": sorted(aliases.values(), key=lambda record: record["id"]),
        "concepts.jsonl": sorted(concepts.values(), key=lambda record: record["id"]),
        "scopes.jsonl": sorted(scope_records.values(), key=lambda record: record["id"]),
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IngestError(f"invalid JSON in {path}:{line_number}: {exc.msg}") from exc
        if not isinstance(record, dict) or not record.get("id"):
            raise IngestError(f"record in {path}:{line_number} must have an id")
        records.append(record)
    return records


def _without_source(record: dict[str, Any], source_id: str) -> dict[str, Any] | None:
    refs = [ref for ref in record.get("source_refs", []) if ref.get("source_id") != source_id]
    if not refs:
        return None
    cleaned = dict(record)
    cleaned["source_refs"] = refs
    return cleaned


def _render_jsonl(records: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)


def ingest_bibtex(
    input_path: Path,
    graph_dir: Path,
    source_id: str,
    source_url: str | None = None,
    dry_run: bool = False,
    scopes: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    try:
        text = input_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise IngestError(f"source not found: {input_path}") from exc

    entries = parse_bibtex(text)
    generated = records_from_bibtex(entries, source_id, source_url, scopes)
    files: dict[str, dict[str, Any]] = {}
    for filename, new_records in generated.items():
        output_path = graph_dir / filename
        retained = [record for record in (_without_source(item, source_id) for item in _load_jsonl(output_path)) if record]
        merged = {record["id"]: record for record in retained}
        for record in new_records:
            if record["id"] in merged:
                _add_record(merged, record)
            else:
                merged[record["id"]] = record
        final_records = sorted(merged.values(), key=lambda record: record["id"])
        rendered = _render_jsonl(final_records)
        previous = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        changed = rendered != previous
        files[filename] = {"records": len(final_records), "source_records": len(new_records), "changed": changed}
        if changed and not dry_run:
            graph_dir.mkdir(parents=True, exist_ok=True)
            temporary = output_path.with_suffix(output_path.suffix + ".tmp")
            temporary.write_text(rendered, encoding="utf-8")
            temporary.replace(output_path)

    return {
        "source_id": source_id,
        "input": str(input_path),
        "dry_run": dry_run,
        "entries": len(entries),
        "files": files,
        "changed_files": sum(file["changed"] for file in files.values()),
        "scopes": [scope["key"] for scope in scopes or []],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest BibTeX into canonical graph JSONL")
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
        report = ingest_bibtex(args.input, args.graph_dir, args.source_id, args.source_url, args.dry_run, scopes)
    except IngestError as exc:
        raise SystemExit(f"BibTeX ingestion failed: {exc}") from exc
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
