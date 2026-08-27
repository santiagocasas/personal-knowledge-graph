"""Bootstrap canonical Papers from a researcher's public ORCID works."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from kg_ads import AdsError, _clean_title, apply_ads_document, lookup_ads_paper, lookup_ads_title
from kg_ingest import (
    _add_record,
    _author_name,
    _load_jsonl,
    _normalise_doi,
    _relation,
    _render_jsonl,
    _slug,
    _without_source,
)


ORCID_PATTERN = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
ORCID_API = "https://pub.orcid.org/v3.0"
OUTPUT_FILES = ("entities.jsonl", "relations.jsonl", "aliases.jsonl", "scopes.jsonl")
RESEARCHER_ID = "person:name:s-casas"
FULL_AUTHOR_EXPANSION_MAX = 20
LARGE_AUTHOR_LIST_COAUTHORS = 5
DERIVED_AUTHORS_SOURCE_ID = "derived:bounded-authors"
DERIVED_CORPORATE_AUTHORS_SOURCE_ID = "derived:corporate-authors"
EUCLID_COLLABORATION_ID = "institution:name:euclid-collaboration"
EUCLID_TITLE_PATTERN = re.compile(
    r"^euclid(?:\s+preparation)?\s*:|^euclid\s+quick\s+data\s+release\b",
    re.IGNORECASE,
)


class OrcidError(ValueError):
    """Raised when ORCID works cannot be converted safely."""


def _request_json(session: Any, url: str, timeout: int) -> dict[str, Any]:
    try:
        response = session.get(
            url,
            headers={"Accept": "application/vnd.orcid+json"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise OrcidError(f"ORCID request failed: {exc}") from exc
    if response.status_code == 404:
        raise OrcidError("ORCID record was not found or has no public works")
    if response.status_code != 200:
        raise OrcidError(f"ORCID request failed: HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise OrcidError("ORCID returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise OrcidError("ORCID returned an invalid works document")
    return payload


def fetch_orcid_works(orcid_id: str, session: Any, timeout: int = 30) -> list[dict[str, Any]]:
    """Return the preferred summary from every public ORCID work group."""
    orcid_id = orcid_id.strip()
    if not ORCID_PATTERN.fullmatch(orcid_id):
        raise OrcidError("ORCID_ID must use the 0000-0000-0000-0000 format")
    payload = _request_json(session, f"{ORCID_API}/{orcid_id}/works", timeout)
    works = []
    for group in payload.get("group") or []:
        summaries = group.get("work-summary") or []
        if not summaries:
            continue
        preferred = max(summaries, key=lambda item: int(item.get("display-index") or 0))
        works.append(preferred)
    return works


def _text(container: Any, *keys: str) -> str | None:
    value = container
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _work_identifiers(work: dict[str, Any]) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    for item in (work.get("external-ids") or {}).get("external-id") or []:
        if item.get("external-id-relationship") not in (None, "self"):
            continue
        kind = str(item.get("external-id-type") or "").lower()
        value = str(item.get("external-id-value") or "").strip()
        if not value:
            continue
        if kind == "doi":
            identifiers.setdefault("doi", _normalise_doi(value))
        elif kind == "arxiv":
            identifiers.setdefault("arxiv", re.sub(r"^arxiv:\s*", "", value, flags=re.I).lower())
        elif kind in {"bibcode", "ads"}:
            identifiers.setdefault("ads", value)
    return identifiers


def _publication_year(work: dict[str, Any]) -> int | None:
    value = _text(work, "publication-date", "year", "value")
    return int(value) if value and value.isdigit() else None


def _canonical_paper_id(identifiers: dict[str, str], orcid_id: str, put_code: str) -> str:
    if identifiers.get("doi"):
        return f"paper:doi:{identifiers['doi']}"
    if identifiers.get("arxiv"):
        return f"paper:arxiv:{identifiers['arxiv']}"
    digest = hashlib.sha256(f"{orcid_id}\0{put_code}".encode("utf-8")).hexdigest()[:20]
    return f"paper:orcid:{digest}"


def _identifier_index(records: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    index: dict[tuple[str, str], str] = {}
    for record in records:
        if record.get("type") != "Paper":
            continue
        for kind, value in (record.get("identifiers") or {}).items():
            if isinstance(value, str) and value:
                normal = _normalise_doi(value) if kind == "doi" else value.lower()
                index[(kind, normal)] = record["id"]
    return index


def _existing_id(identifiers: dict[str, str], index: dict[tuple[str, str], str]) -> str | None:
    for kind in ("doi", "arxiv", "ads"):
        value = identifiers.get(kind)
        if value and (kind, value.lower()) in index:
            return index[(kind, value.lower())]
    return None


def _is_researcher_author(raw_name: str) -> bool:
    """Recognize the researcher's common ADS name forms without merging other Casas authors."""
    family, separator, given = raw_name.partition(",")
    if not separator:
        parts = raw_name.split()
        family = parts[-1] if parts else ""
        given = " ".join(parts[:-1])
    normalized_given = re.sub(r"[^a-z]", "", given.lower())
    return family.strip().lower() == "casas" and normalized_given in {"s", "santiago"}


def _author_ref(ref: dict[str, str], paper: dict[str, Any]) -> dict[str, str]:
    author_ref = dict(ref)
    bibcode = (paper.get("identifiers") or {}).get("ads")
    if bibcode:
        author_ref["evidence_source_id"] = f"ads:{bibcode}"
        author_ref["evidence_url"] = f"https://ui.adsabs.harvard.edu/abs/{bibcode}/abstract"
    return author_ref


def _is_euclid_corporate_paper(paper: dict[str, Any]) -> bool:
    author_list = (paper.get("properties") or {}).get("author_list")
    if isinstance(author_list, str):
        markers = {value.strip().lower() for value in author_list.split(";")}
        if markers & {"euclid collaboration", "euclid consortium"}:
            return True
    return bool(EUCLID_TITLE_PATTERN.match(str(paper.get("name") or "").strip()))


def _add_euclid_corporate_author(
    paper: dict[str, Any],
    entities: dict[str, dict[str, Any]],
    relations: dict[str, dict[str, Any]],
) -> None:
    ref = {"source_id": DERIVED_CORPORATE_AUTHORS_SOURCE_ID}
    bibcode = (paper.get("identifiers") or {}).get("ads")
    if bibcode:
        ref["source_url"] = f"https://ui.adsabs.harvard.edu/abs/{bibcode}/abstract"
    _add_record(
        entities,
        {
            "id": EUCLID_COLLABORATION_ID,
            "type": "Institution",
            "rdf_type": "foaf:Organization",
            "name": "Euclid Collaboration",
            "properties": {"institution_kind": "collaboration"},
            "source_refs": [ref],
        },
    )
    _add_record(
        relations,
        _relation(paper["id"], "corporate_authored_by", EUCLID_COLLABORATION_ID, ref),
    )


def _add_person_alias(
    aliases: dict[str, dict[str, Any]], raw_name: str, person_id: str, ref: dict[str, str]
) -> None:
    _add_record(
        aliases,
        {
            "id": f"alias:author_name:{_slug(raw_name)}",
            "alias": raw_name,
            "kind": "author_name",
            "entity_id": person_id,
            "source_refs": [ref],
        },
    )


def _add_authors(
    paper: dict[str, Any],
    orcid_id: str,
    ref: dict[str, str],
    entities: dict[str, dict[str, Any]],
    relations: dict[str, dict[str, Any]],
    aliases: dict[str, dict[str, Any]],
) -> int:
    """Add the researcher plus a bounded set of coauthors for one Paper."""
    researcher_ref = dict(ref)
    researcher = {
        "id": RESEARCHER_ID,
        "type": "Person",
        "rdf_type": "foaf:Person",
        "name": "Santiago Casas",
        "properties": {
            "name_as_cited": "Santiago Casas",
            "orcid": f"https://orcid.org/{orcid_id}",
        },
        "source_refs": [researcher_ref],
    }
    _add_record(entities, researcher)
    _add_record(relations, _relation(paper["id"], "authored_by", RESEARCHER_ID, researcher_ref))
    for alias in ("Santiago Casas", "Casas, Santiago", "S. Casas", "Casas, S."):
        _add_person_alias(aliases, alias, RESEARCHER_ID, researcher_ref)

    author_list = (paper.get("properties") or {}).get("author_list")
    if not isinstance(author_list, str):
        return 0
    raw_authors = [value.strip() for value in author_list.split(";") if value.strip()]
    candidates = [
        value
        for value in raw_authors
        if not value.lower().endswith(" collaboration") and not _is_researcher_author(value)
    ]
    author_count = (paper.get("properties") or {}).get("author_count")
    limit = len(candidates) if isinstance(author_count, int) and author_count <= FULL_AUTHOR_EXPANSION_MAX else LARGE_AUTHOR_LIST_COAUTHORS
    evidence_ref = _author_ref(ref, paper)
    added_ids: set[str] = set()
    for raw_name in candidates:
        name = _author_name(raw_name)
        person_id = f"person:name:{_slug(name)}"
        if person_id in added_ids:
            continue
        added_ids.add(person_id)
        _add_record(
            entities,
            {
                "id": person_id,
                "type": "Person",
                "rdf_type": "foaf:Person",
                "name": name,
                "properties": {"name_as_cited": raw_name},
                "source_refs": [evidence_ref],
            },
        )
        _add_record(relations, _relation(paper["id"], "authored_by", person_id, evidence_ref))
        _add_person_alias(aliases, raw_name, person_id, evidence_ref)
        if len(added_ids) >= limit:
            break
    return len(added_ids)


def _merge_records(
    graph_dir: Path,
    generated: dict[str, list[dict[str, Any]]],
    source_ids: tuple[str, ...],
    dry_run: bool,
) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for filename in OUTPUT_FILES:
        path = graph_dir / filename
        retained = _load_jsonl(path)
        for source_id in source_ids:
            retained = [record for record in (_without_source(item, source_id) for item in retained) if record]
        merged = {record["id"]: record for record in retained}
        for record in generated.get(filename, []):
            if filename == "entities.jsonl" and record["id"] == RESEARCHER_ID and record["id"] in merged:
                merged[record["id"]]["name"] = record["name"]
            _add_record(merged, record)
        records = sorted(merged.values(), key=lambda record: record["id"])
        rendered = _render_jsonl(records)
        previous = path.read_text(encoding="utf-8") if path.exists() else ""
        changed = rendered != previous
        files[filename] = {
            "records": len(records),
            "source_records": len(generated.get(filename, [])),
            "changed": changed,
        }
        if changed and not dry_run:
            graph_dir.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(rendered, encoding="utf-8")
            temporary.replace(path)
    return files


def bootstrap_orcid_works(
    graph_dir: Path,
    orcid_id: str,
    ads_token: str,
    dry_run: bool = False,
    timeout: int = 30,
    limit: int = 0,
    orcid_session: Any | None = None,
    ads_session: Any | None = None,
) -> dict[str, Any]:
    """Build a source-attributed My Papers proposal from ORCID and ADS."""
    if not ads_token.strip():
        raise OrcidError("ADS_API_TOKEN is not configured")
    orcid_id = orcid_id.strip()
    source_id = f"orcid:{orcid_id}"
    source_url = f"https://orcid.org/{orcid_id}"
    works = fetch_orcid_works(orcid_id, orcid_session or requests.Session(), timeout)
    if limit > 0:
        works = works[:limit]

    current_entities = _load_jsonl(graph_dir / "entities.jsonl")
    current_by_id = {record["id"]: record for record in current_entities}
    identifier_index = _identifier_index(current_entities)
    entities: dict[str, dict[str, Any]] = {}
    relations: dict[str, dict[str, Any]] = {}
    aliases: dict[str, dict[str, Any]] = {}
    scopes: dict[str, dict[str, Any]] = {}
    ads_client = ads_session or requests.Session()
    details = []
    ads_matched = 0
    ads_unmatched = 0
    existing_count = 0
    new_count = 0
    coauthor_links = 0
    processed_paper_ids: set[str] = set()
    derived_author_papers = 0
    corporate_author_papers = 0

    for work in works:
        put_code = str(work.get("put-code") or "").strip()
        raw_title = _text(work, "title", "title", "value")
        title = _clean_title(raw_title) if raw_title else None
        if not put_code or not title:
            raise OrcidError("ORCID work summary is missing its put-code or title")
        identifiers = _work_identifiers(work)
        matched_existing = _existing_id(identifiers, identifier_index)
        paper_id = matched_existing or _canonical_paper_id(identifiers, orcid_id, put_code)
        ref = {"source_id": source_id, "put_code": put_code, "source_url": source_url}
        existing = current_by_id.get(paper_id)
        paper = json.loads(json.dumps(existing)) if existing else {
            "id": paper_id,
            "type": "Paper",
            "rdf_type": "bibo:AcademicArticle",
            "name": title,
            "identifiers": {},
            "properties": {},
            "source_refs": [],
        }
        paper["identifiers"] = {**(paper.get("identifiers") or {}), **identifiers}
        properties = dict(paper.get("properties") or {})
        properties["entry_type"] = str(work.get("type") or "work")
        properties.setdefault("source_url", _text(work, "url", "value") or source_url)
        journal = _text(work, "journal-title", "value")
        if journal:
            properties.setdefault("journal", journal)
        year = _publication_year(work)
        if year:
            properties.setdefault("publication_year", year)
        paper["properties"] = properties
        refs = {json.dumps(item, sort_keys=True): item for item in paper.get("source_refs", [])}
        refs[json.dumps(ref, sort_keys=True)] = ref
        paper["source_refs"] = [refs[key] for key in sorted(refs)]

        ads_status = "existing"
        if not (paper.get("identifiers") or {}).get("ads"):
            try:
                document, _ = lookup_ads_paper(paper, ads_token, ads_client, timeout)
                if not document:
                    document = lookup_ads_title(title, ads_token, ads_client, timeout)
            except AdsError as exc:
                raise OrcidError(str(exc)) from exc
            if document:
                for alias in apply_ads_document(paper, document):
                    _add_record(aliases, alias)
                ads_matched += 1
                ads_status = "matched"
            else:
                ads_unmatched += 1
                ads_status = "unmatched"

        _add_record(entities, paper)
        coauthor_links += _add_authors(paper, orcid_id, ref, entities, relations, aliases)
        processed_paper_ids.add(paper_id)
        work_alias = {
            "id": f"alias:orcid_work:{_slug(orcid_id + '-' + put_code)}",
            "alias": f"{orcid_id}/{put_code}",
            "kind": "orcid_work",
            "entity_id": paper_id,
            "source_refs": [ref],
        }
        _add_record(aliases, work_alias)
        for kind, value in identifiers.items():
            _add_record(
                aliases,
                {
                    "id": f"alias:{kind}:{_slug(value)}",
                    "alias": value,
                    "kind": kind,
                    "entity_id": paper_id,
                    "source_refs": [ref],
                },
            )

        scope_id = "scope:my-papers"
        _add_record(
            scopes,
            {
                "id": scope_id,
                "type": "GraphScope",
                "rdf_type": "schema:Collection",
                "name": "My Papers",
                "properties": {"scope_kind": "portfolio", "source_url": source_url},
                "source_refs": [ref],
            },
        )
        _add_record(relations, _relation(paper_id, "in_scope", scope_id, ref))
        _add_record(relations, _relation(scope_id, "contains", paper_id, ref))

        if existing:
            existing_count += 1
        else:
            new_count += 1
        details.append(
            {
                "title": paper["name"],
                "canonical_id": paper_id,
                "action": "update" if existing else "create",
                "ads": ads_status,
                "work_type": work.get("type"),
            }
        )

    # ADS-enriched Papers may come from reading lists rather than ORCID. Reconcile
    # their author edges too when the stored author list identifies the researcher.
    for existing in current_entities:
        if existing.get("type") != "Paper" or existing.get("id") in processed_paper_ids:
            continue
        author_list = (existing.get("properties") or {}).get("author_list")
        if not isinstance(author_list, str) or not any(
            _is_researcher_author(value.strip()) for value in author_list.split(";")
        ):
            continue
        bibcode = (existing.get("identifiers") or {}).get("ads")
        ref = {"source_id": DERIVED_AUTHORS_SOURCE_ID}
        if bibcode:
            ref["source_url"] = f"https://ui.adsabs.harvard.edu/abs/{bibcode}/abstract"
        coauthor_links += _add_authors(existing, orcid_id, ref, entities, relations, aliases)
        derived_author_papers += 1

    # Corporate authorship is independent of ingestion source. ADS sometimes includes
    # an explicit collaboration marker and sometimes exposes only the Euclid series title.
    effective_papers = {
        record["id"]: record for record in current_entities if record.get("type") == "Paper"
    }
    effective_papers.update(
        (record["id"], record) for record in entities.values() if record.get("type") == "Paper"
    )
    for paper in effective_papers.values():
        if not _is_euclid_corporate_paper(paper):
            continue
        _add_euclid_corporate_author(paper, entities, relations)
        corporate_author_papers += 1

    generated = {
        "entities.jsonl": list(entities.values()),
        "relations.jsonl": list(relations.values()),
        "aliases.jsonl": list(aliases.values()),
        "scopes.jsonl": list(scopes.values()),
    }
    files = _merge_records(
        graph_dir,
        generated,
        (source_id, DERIVED_AUTHORS_SOURCE_ID, DERIVED_CORPORATE_AUTHORS_SOURCE_ID),
        dry_run,
    )
    return {
        "dry_run": dry_run,
        "orcid_work_groups": len(works),
        "existing_papers": existing_count,
        "new_papers": new_count,
        "ads_matched": ads_matched,
        "ads_already_present": len(works) - ads_matched - ads_unmatched,
        "ads_unmatched": ads_unmatched,
        "researcher": RESEARCHER_ID,
        "coauthor_links": coauthor_links,
        "derived_author_papers": derived_author_papers,
        "corporate_author_papers": corporate_author_papers,
        "author_policy": {
            "full_expansion_max_authors": FULL_AUTHOR_EXPANSION_MAX,
            "large_author_list_coauthors": LARGE_AUTHOR_LIST_COAUTHORS,
        },
        "scope": "my-papers",
        "changed_files": sum(item["changed"] for item in files.values()),
        "files": files,
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap canonical Papers from ORCID and NASA ADS")
    parser.add_argument("--graph-dir", type=Path, default=Path("graph"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    try:
        report = bootstrap_orcid_works(
            args.graph_dir,
            os.getenv("ORCID_ID", ""),
            os.getenv("ADS_API_TOKEN", ""),
            args.dry_run,
            args.timeout,
            args.limit,
        )
    except OrcidError as exc:
        raise SystemExit(f"ORCID ingestion failed: {exc}") from exc
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
