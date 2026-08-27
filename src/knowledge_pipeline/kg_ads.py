"""Enrich canonical Paper records with NASA ADS metadata."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from kg_ingest import _add_record, _load_jsonl, _normalise_doi, _render_jsonl, _slug


ADS_API_URL = "https://api.adsabs.harvard.edu/v1/search/query"
ADS_FIELDS = (
    "bibcode,title,author,doi,identifier,year,pub,abstract,keyword,"
    "citation_count,property"
)


class AdsError(ValueError):
    """Raised when ADS metadata cannot be matched or persisted safely."""


def _clean_title(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"</?(?:sub|sup|i|b|em|strong)>", "", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip()


def _title_key(value: str) -> str:
    """Normalize punctuation and markup for conservative title matching."""
    return re.sub(r"[^a-z0-9]+", " ", _clean_title(value).casefold()).strip()


def _first(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _normalise_arxiv(value: str) -> str:
    value = re.sub(r"^arxiv:\s*", "", value.strip(), flags=re.I)
    return re.sub(r"v\d+$", "", value, flags=re.I).lower()


def _paper_identifiers(paper: dict[str, Any]) -> list[str]:
    identifiers = paper.get("identifiers") or {}
    properties = paper.get("properties") or {}
    doi = identifiers.get("doi") or properties.get("doi")
    arxiv = identifiers.get("arxiv") or properties.get("arxiv_id")
    values = []
    if isinstance(doi, str) and doi.strip():
        values.append(_normalise_doi(doi))
    if isinstance(arxiv, str) and arxiv.strip():
        values.append(_normalise_arxiv(arxiv))
    return list(dict.fromkeys(values))


def lookup_ads_paper(
    paper: dict[str, Any],
    token: str,
    session: requests.Session | Any,
    timeout: int = 30,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return one unambiguous ADS document and the identifier that matched it."""
    identifiers = _paper_identifiers(paper)
    if not identifiers:
        return None, None

    for identifier in identifiers:
        try:
            response = session.get(
                ADS_API_URL,
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "q": f'identifier:"{identifier}"',
                    "fl": ADS_FIELDS,
                    "rows": 2,
                },
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise AdsError(f"ADS request failed for {identifier}: {exc}") from exc
        if response.status_code == 401:
            raise AdsError("ADS rejected ADS_API_TOKEN")
        if response.status_code != 200:
            raise AdsError(f"ADS request failed for {identifier}: HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise AdsError(f"ADS returned invalid JSON for {identifier}") from exc
        documents = payload.get("response", {}).get("docs", [])
        if len(documents) > 1:
            raise AdsError(f"ADS identifier {identifier} matched {len(documents)} records")
        if documents:
            return documents[0], identifier
    return None, None


def lookup_ads_title(
    title: str,
    token: str,
    session: requests.Session | Any,
    timeout: int = 30,
) -> dict[str, Any] | None:
    """Return one ADS document only when its normalized title matches exactly."""
    clean_title = _clean_title(title)
    escaped = clean_title.replace("\\", "\\\\").replace('"', '\\"')
    try:
        response = session.get(
            ADS_API_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"q": f'title:"{escaped}"', "fl": ADS_FIELDS, "rows": 5},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise AdsError(f"ADS title request failed: {exc}") from exc
    if response.status_code == 401:
        raise AdsError("ADS rejected ADS_API_TOKEN")
    if response.status_code != 200:
        raise AdsError(f"ADS title request failed: HTTP {response.status_code}")
    try:
        documents = response.json().get("response", {}).get("docs", [])
    except ValueError as exc:
        raise AdsError("ADS returned invalid JSON for title lookup") from exc
    exact = [
        document
        for document in documents
        if _title_key(_first(document.get("title")) or "") == _title_key(clean_title)
    ]
    if exact:
        return exact[0] if len(exact) == 1 else None

    # ADS title phrase matching is punctuation-sensitive. Retry with terms so
    # ORCID titles using a colon can match ADS records using a period.
    terms = re.findall(r"[a-z0-9]+", clean_title.casefold())
    if not terms:
        return None
    try:
        response = session.get(
            ADS_API_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"q": "title:(" + " ".join(terms) + ")", "fl": ADS_FIELDS, "rows": 5},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise AdsError(f"ADS title fallback request failed: {exc}") from exc
    if response.status_code == 401:
        raise AdsError("ADS rejected ADS_API_TOKEN")
    if response.status_code != 200:
        raise AdsError(f"ADS title fallback request failed: HTTP {response.status_code}")
    try:
        fallback_documents = response.json().get("response", {}).get("docs", [])
    except ValueError as exc:
        raise AdsError("ADS returned invalid JSON for title fallback") from exc
    exact = [
        document
        for document in fallback_documents
        if _title_key(_first(document.get("title")) or "") == _title_key(clean_title)
    ]
    return exact[0] if len(exact) == 1 else None


def _ads_source_ref(bibcode: str) -> dict[str, str]:
    source_url = f"https://ui.adsabs.harvard.edu/abs/{bibcode}/abstract"
    return {"source_id": f"ads:{bibcode}", "source_url": source_url}


def _replace_ads_ref(record: dict[str, Any], ref: dict[str, str]) -> None:
    refs = [
        item
        for item in record.get("source_refs", [])
        if not str(item.get("source_id", "")).startswith("ads:")
    ]
    refs.append(ref)
    record["source_refs"] = sorted(refs, key=lambda item: json.dumps(item, sort_keys=True))


def _document_arxiv_ids(document: dict[str, Any]) -> list[str]:
    values = []
    for identifier in document.get("identifier") or []:
        if not isinstance(identifier, str):
            continue
        match = re.fullmatch(r"(?:arXiv:)?(\d{4}\.\d{4,5})(?:v\d+)?", identifier, flags=re.I)
        if match:
            values.append(match.group(1).lower())
    return list(dict.fromkeys(values))


def apply_ads_document(paper: dict[str, Any], document: dict[str, Any]) -> list[dict[str, Any]]:
    """Merge one ADS document into a canonical Paper and return alias records."""
    bibcode = document.get("bibcode")
    if not isinstance(bibcode, str) or not bibcode.strip():
        raise AdsError(f"ADS result for {paper.get('id')} has no bibcode")
    bibcode = bibcode.strip()
    ref = _ads_source_ref(bibcode)
    properties = dict(paper.get("properties") or {})

    title = _first(document.get("title"))
    if title:
        paper["name"] = _clean_title(title)

    managed = (
        "ads_bibcode",
        "ads_url",
        "description",
        "publication",
        "citation_count",
        "author_count",
        "ads_keywords",
    )
    for key in managed:
        properties.pop(key, None)
    properties["ads_bibcode"] = bibcode
    properties["ads_url"] = ref["source_url"]
    abstract = _first(document.get("abstract"))
    if abstract:
        properties["description"] = re.sub(r"\s+", " ", abstract)
    publication = _first(document.get("pub"))
    if publication:
        properties["publication"] = publication
    year = document.get("year")
    if str(year).isdigit():
        properties["publication_year"] = int(year)
    citation_count = document.get("citation_count")
    if isinstance(citation_count, int):
        properties["citation_count"] = citation_count
    authors = [
        value.strip()
        for value in document.get("author") or []
        if isinstance(value, str) and value.strip()
    ]
    if authors:
        properties["author_count"] = len(authors)
        properties["author_list"] = "; ".join(authors)
        properties["author_list_incomplete"] = False
    keywords = list(
        dict.fromkeys(
            value.strip()
            for value in document.get("keyword") or []
            if isinstance(value, str) and value.strip()
        )
    )
    if keywords:
        properties["ads_keywords"] = "; ".join(keywords)

    identifiers = dict(paper.get("identifiers") or {})
    identifiers["ads"] = bibcode
    doi_values = [
        _normalise_doi(value)
        for value in document.get("doi") or []
        if isinstance(value, str) and value.strip()
    ]
    if doi_values:
        identifiers.setdefault("doi", doi_values[0])
        properties.setdefault("doi", f"https://doi.org/{doi_values[0]}")
    arxiv_values = _document_arxiv_ids(document)
    if arxiv_values:
        identifiers.setdefault("arxiv", arxiv_values[0])
        properties.setdefault("arxiv_id", arxiv_values[0])

    paper["identifiers"] = identifiers
    paper["properties"] = properties
    _replace_ads_ref(paper, ref)

    alias_values = [("ads_bibcode", bibcode)]
    alias_values.extend(("doi", value) for value in doi_values)
    alias_values.extend(("arxiv", value) for value in arxiv_values)
    return [
        {
            "id": f"alias:{kind}:{_slug(value)}",
            "alias": value,
            "kind": kind,
            "entity_id": paper["id"],
            "source_refs": [ref],
        }
        for kind, value in alias_values
    ]


def _write_if_changed(path: Path, records: list[dict[str, Any]], dry_run: bool) -> bool:
    rendered = _render_jsonl(sorted(records, key=lambda record: record["id"]))
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    changed = rendered != previous
    if changed and not dry_run:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(path)
    return changed


def enrich_graph_from_ads(
    graph_dir: Path,
    token: str,
    dry_run: bool = False,
    timeout: int = 30,
    limit: int = 0,
    session: requests.Session | Any | None = None,
) -> dict[str, Any]:
    """Enrich existing canonical papers without creating author entities."""
    if not token.strip():
        raise AdsError("ADS_API_TOKEN is not configured")
    entities_path = graph_dir / "entities.jsonl"
    aliases_path = graph_dir / "aliases.jsonl"
    entities = _load_jsonl(entities_path)
    aliases = _load_jsonl(aliases_path)
    papers = [record for record in entities if record.get("type") == "Paper"]
    if limit > 0:
        papers = papers[:limit]
    client = session or requests.Session()
    matched: list[dict[str, Any]] = []
    unmatched: list[str] = []
    generated_aliases: list[dict[str, Any]] = []

    for paper in papers:
        document, identifier = lookup_ads_paper(paper, token, client, timeout)
        if not document:
            unmatched.append(paper["id"])
            continue
        generated_aliases.extend(apply_ads_document(paper, document))
        matched.append(
            {
                "canonical_id": paper["id"],
                "matched_identifier": identifier,
                "bibcode": document["bibcode"],
                "authors": len(document.get("author") or []),
            }
        )

    matched_ids = {item["canonical_id"] for item in matched}
    retained_aliases = []
    for alias in aliases:
        if alias.get("entity_id") not in matched_ids:
            retained_aliases.append(alias)
            continue
        refs = [
            ref
            for ref in alias.get("source_refs", [])
            if not str(ref.get("source_id", "")).startswith("ads:")
        ]
        if refs:
            retained = dict(alias)
            retained["source_refs"] = refs
            retained_aliases.append(retained)
    alias_index = {record["id"]: record for record in retained_aliases}
    for alias in generated_aliases:
        _add_record(alias_index, alias)

    file_changes = {
        "entities.jsonl": _write_if_changed(entities_path, entities, dry_run),
        "aliases.jsonl": _write_if_changed(aliases_path, list(alias_index.values()), dry_run),
    }
    return {
        "dry_run": dry_run,
        "papers_considered": len(papers),
        "matched": len(matched),
        "unmatched": len(unmatched),
        "changed_files": sum(file_changes.values()),
        "files": file_changes,
        "details": matched,
        "unmatched_ids": unmatched,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich canonical Paper records from NASA ADS")
    parser.add_argument("--graph-dir", type=Path, default=Path("graph"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    try:
        report = enrich_graph_from_ads(
            args.graph_dir,
            os.getenv("ADS_API_TOKEN", ""),
            args.dry_run,
            args.timeout,
            args.limit,
        )
    except AdsError as exc:
        raise SystemExit(f"ADS enrichment failed: {exc}") from exc
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
