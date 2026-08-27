"""Ingest a researcher's publications from the INSPIRE REST API."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from kg_ingest import (
    _add_record,
    _author_name,
    _load_jsonl,
    _normalise_doi,
    _relation,
    _render_jsonl,
    _slug,
    _without_source,
    parse_scope_spec,
)


INSPIRE_API = "https://inspirehep.net/api"
RESEARCHER_ID = "person:name:s-casas"
SOURCE_ID_PREFIX = "inspire:author:"
MAX_PROJECTED_AUTHORS = 5


class InspireError(ValueError):
    """Raised when INSPIRE data cannot be ingested safely."""


def _get_json(session: Any, url: str, params: dict[str, Any], timeout: int) -> dict[str, Any]:
    try:
        response = session.get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise InspireError(f"INSPIRE request failed: {exc}") from exc
    if response.status_code == 429:
        raise InspireError("INSPIRE rate limit reached; wait and retry")
    if response.status_code != 200:
        raise InspireError(f"INSPIRE request failed: HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise InspireError("INSPIRE returned invalid JSON") from exc


def _author_identifier(metadata: dict[str, Any]) -> str:
    for item in metadata.get("ids") or []:
        if item.get("schema") == "INSPIRE BAI" and item.get("value"):
            return str(item["value"])
    raise InspireError("INSPIRE author record has no BAI")


def _title(metadata: dict[str, Any]) -> str:
    titles = metadata.get("titles") or []
    for item in titles:
        if item.get("title"):
            return str(item["title"])
    raise InspireError("INSPIRE literature record has no title")


def _doi(metadata: dict[str, Any]) -> str | None:
    values = [item.get("value") for item in metadata.get("dois") or []]
    return _normalise_doi(str(values[0])) if values and values[0] else None


def _arxiv(metadata: dict[str, Any]) -> str | None:
    values = metadata.get("arxiv_eprints") or []
    return str(values[0].get("value")).lower() if values and values[0].get("value") else None


def _paper_id(metadata: dict[str, Any], recid: str) -> tuple[str, dict[str, str]]:
    doi = _doi(metadata)
    arxiv = _arxiv(metadata)
    if doi:
        return f"paper:doi:{doi}", {"doi": doi}
    if arxiv:
        return f"paper:arxiv:{arxiv}", {"arxiv": arxiv}
    return f"paper:inspire:{recid}", {"inspire": recid}


def _is_researcher(author: dict[str, Any], author_bai: str) -> bool:
    if str(author.get("recid")) == author_bai:
        return True
    return any(item.get("value") == "0000-0002-4751-5138" for item in author.get("ids") or [])


def _author_ref(source_id: str, source_url: str, recid: str) -> dict[str, str]:
    return {"source_id": source_id, "source_url": source_url, "inspire_recid": recid}


def _author_records(
    paper_id: str,
    authors: list[dict[str, Any]],
    author_bai: str,
    ref: dict[str, str],
    entities: dict[str, dict[str, Any]],
    relations: dict[str, dict[str, Any]],
    aliases: dict[str, dict[str, Any]],
) -> int:
    researcher = next((author for author in authors if _is_researcher(author, author_bai)), None)
    if researcher is None:
        return 0
    ordered = [researcher] + [author for author in authors if author is not researcher]
    selected = ordered[: MAX_PROJECTED_AUTHORS + 1]
    for author in selected:
        name = str(author.get("full_name") or author.get("full_name_unicode_normalized") or "").strip()
        if not name:
            continue
        person_id = RESEARCHER_ID if _is_researcher(author, author_bai) else f"person:name:{_slug(_author_name(name))}"
        person_ref = dict(ref)
        recid = author.get("recid")
        if recid:
            person_ref["inspire_author_recid"] = str(recid)
        identifiers = {"inspire": str(recid)} if recid else {}
        for identifier in author.get("ids") or []:
            if identifier.get("schema") == "ORCID" and identifier.get("value"):
                identifiers["orcid"] = str(identifier["value"])
        properties = {"name_as_cited": name}
        _add_record(entities, {
            "id": person_id,
            "type": "Person",
            "rdf_type": "foaf:Person",
            "name": "Santiago Casas" if person_id == RESEARCHER_ID else _author_name(name),
            "identifiers": identifiers,
            "properties": properties,
            "source_refs": [person_ref],
        })
        _add_record(relations, _relation(paper_id, "authored_by", person_id, person_ref))
        _add_record(aliases, {
            "id": f"alias:author_name:{_slug(name)}",
            "alias": name,
            "kind": "author_name",
            "entity_id": person_id,
            "source_refs": [person_ref],
        })
    return len(selected)


def fetch_inspire_works(author: str, session: Any, timeout: int = 30) -> tuple[str, list[dict[str, Any]]]:
    author_record = _get_json(session, f"{INSPIRE_API}/authors/{author}", {}, timeout)
    author_bai = _author_identifier(author_record.get("metadata") or {})
    works: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = _get_json(session, f"{INSPIRE_API}/literature", {
            "q": f"a {author_bai}",
            "size": 1000,
            "page": page,
            "fields": "titles,authors,dois,arxiv_eprints,texkeys,publication_info,abstracts,citation_count,control_number,documents",
        }, timeout)
        hits = payload.get("hits") or {}
        works.extend(hits.get("hits") or [])
        total = int(hits.get("total") or len(works))
        if len(works) >= total or not hits.get("hits"):
            break
        page += 1
    return author_bai, works


def ingest_inspire(
    graph_dir: Path,
    author: str,
    source_id: str | None = None,
    scope: dict[str, str] | None = None,
    dry_run: bool = False,
    timeout: int = 30,
    session: Any | None = None,
) -> dict[str, Any]:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    client = session or requests.Session()
    author_bai, works = fetch_inspire_works(author, client, timeout)
    source_id = source_id or f"{SOURCE_ID_PREFIX}{author_bai}"
    source_url = f"https://inspirehep.net/authors/{author}"
    entities: dict[str, dict[str, Any]] = {}
    relations: dict[str, dict[str, Any]] = {}
    aliases: dict[str, dict[str, Any]] = {}
    scopes: dict[str, dict[str, Any]] = {}
    scope_relations: dict[str, dict[str, Any]] = {}
    if scope:
        scope_id = f"scope:{scope['key']}"
        scopes[scope_id] = {
            "id": scope_id, "type": "Graph Scope", "rdf_type": "schema:Collection",
            "name": scope["name"], "properties": {"scope_kind": scope["kind"], "source_url": source_url},
            "source_refs": [{"source_id": source_id, "source_url": source_url}],
        }

    papers = 0
    projected_authors = 0
    for hit in works:
        metadata = hit.get("metadata") or {}
        recid = str(hit.get("id") or metadata.get("control_number") or "").strip()
        if not recid:
            continue
        paper_id, identifiers = _paper_id(metadata, recid)
        ref = {"source_id": source_id, "source_url": source_url, "inspire_recid": recid}
        props: dict[str, Any] = {"source_url": f"https://inspirehep.net/literature/{recid}"}
        if _doi(metadata):
            props["doi"] = f"https://doi.org/{_doi(metadata)}"
        if _arxiv(metadata):
            props["arxiv_id"] = _arxiv(metadata)
        if metadata.get("citation_count") is not None:
            props["citation_count"] = metadata["citation_count"]
        texkeys = [
            item.get("value") if isinstance(item, dict) else item
            for item in metadata.get("texkeys") or []
        ]
        citation_key = next((str(value).strip() for value in texkeys if value), None)
        if citation_key:
            props["citation_key"] = citation_key
        publication = (metadata.get("publication_info") or [{}])[0]
        if publication.get("year"):
            props["publication_year"] = publication["year"]
        if publication.get("journal_title"):
            props["journal"] = publication["journal_title"]
        authors = metadata.get("authors") or []
        names = [str(a.get("full_name") or "").strip() for a in authors if a.get("full_name")]
        if names:
            props["author_list"] = "; ".join(names)
            props["author_count"] = len(names)
            props["author_list_incomplete"] = False
        abstracts = metadata.get("abstracts") or []
        if abstracts and abstracts[0].get("value"):
            props["description"] = abstracts[0]["value"]
        _add_record(entities, {
            "id": paper_id, "type": "Paper", "rdf_type": "bibo:AcademicArticle",
            "name": _title(metadata), "identifiers": identifiers, "properties": props,
            "source_refs": [ref],
        })
        _add_record(aliases, {
            "id": f"alias:inspire:{recid}", "alias": recid, "kind": "inspire_recid",
            "entity_id": paper_id, "source_refs": [ref],
        })
        projected_authors += _author_records(paper_id, authors, author_bai, ref, entities, relations, aliases)
        if scope:
            scope_id = f"scope:{scope['key']}"
            _add_record(scope_relations, _relation(paper_id, "in_scope", scope_id, ref))
            _add_record(scope_relations, _relation(scope_id, "contains", paper_id, ref))
        papers += 1

    generated = {"entities.jsonl": list(entities.values()), "relations.jsonl": list(relations.values()) + list(scope_relations.values()),
                 "aliases.jsonl": list(aliases.values()), "scopes.jsonl": list(scopes.values())}
    files: dict[str, dict[str, Any]] = {}
    for filename, new_records in generated.items():
        output = graph_dir / filename
        retained = [record for record in (_without_source(item, source_id) for item in _load_jsonl(output)) if record]
        merged = {record["id"]: record for record in retained}
        for record in new_records:
            _add_record(merged, record) if record["id"] in merged else merged.setdefault(record["id"], record)
        rendered = _render_jsonl(sorted(merged.values(), key=lambda row: row["id"]))
        previous = output.read_text(encoding="utf-8") if output.exists() else ""
        changed = rendered != previous
        files[filename] = {"records": len(merged), "source_records": len(new_records), "changed": changed}
        if changed and not dry_run:
            graph_dir.mkdir(parents=True, exist_ok=True)
            temporary = output.with_suffix(output.suffix + ".tmp")
            temporary.write_text(rendered, encoding="utf-8")
            temporary.replace(output)
    return {"dry_run": dry_run, "author_bai": author_bai, "works": len(works), "papers": papers,
            "projected_authors": projected_authors, "source_id": source_id, "scope": scope["key"] if scope else None,
            "changed_files": sum(item["changed"] for item in files.values()), "files": files}


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    parser = argparse.ArgumentParser(description="Ingest publications from INSPIRE")
    parser.add_argument("--author", default=os.getenv("INSPIRE_AUTHOR_ID"), help="INSPIRE author record ID or BAI (default: INSPIRE_AUTHOR_ID)")
    parser.add_argument("--graph-dir", type=Path, default=Path("graph"))
    parser.add_argument("--source-id")
    parser.add_argument("--scope", default="my-papers=portfolio:My Papers")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.author:
        raise SystemExit("INSPIRE_AUTHOR_ID is missing; set it in .env or pass --author")
    try:
        report = ingest_inspire(args.graph_dir, args.author, args.source_id, parse_scope_spec(args.scope), args.dry_run, args.timeout)
    except InspireError as exc:
        raise SystemExit(f"INSPIRE ingestion failed: {exc}") from exc
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
