"""Export canonical publication records as a Jekyll Scholar BibTeX file."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


EUCLID_ID = "institution:name:euclid-collaboration"
RESEARCHER_ALIASES = {
    "santiago casas",
    "casas, santiago",
    "s. casas",
    "casas, s.",
}
CATEGORY_NAMES = ("personal", "euclid_core", "euclid_collab")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _normalise_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def author_position(author_list: str | None) -> int | None:
    if not isinstance(author_list, str):
        return None
    authors = [item.strip() for item in author_list.split(";") if item.strip()]
    for index, author in enumerate(authors, start=1):
        normalised = _normalise_name(author)
        if normalised in RESEARCHER_ALIASES:
            return index
        if re.fullmatch(r"(?:s\.?\s+casas|casas,\s*s\.?)", normalised):
            return index
    return None


def _is_euclid(paper: dict[str, Any], relations: list[dict[str, Any]]) -> bool:
    paper_id = paper.get("id")
    if any(
        relation.get("subject") == paper_id
        and relation.get("predicate") == "corporate_authored_by"
        and relation.get("object") == EUCLID_ID
        for relation in relations
    ):
        return True
    collaboration = str((paper.get("properties") or {}).get("collaboration") or "")
    return collaboration.strip().lower() in {"euclid", "euclid collaboration"}


def _citation_key(paper: dict[str, Any]) -> tuple[str, bool]:
    properties = paper.get("properties") or {}
    if properties.get("citation_key"):
        return str(properties["citation_key"]).strip(), False
    for reference in paper.get("source_refs") or []:
        if reference.get("citation_key"):
            return str(reference["citation_key"]).strip(), False
    identifiers = paper.get("identifiers") or {}
    raw = str(identifiers.get("arxiv") or identifiers.get("doi") or paper.get("id") or "paper")
    fallback = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_")
    return f"paper_{fallback}", True


def _paper_identity(paper: dict[str, Any]) -> str:
    identifiers = paper.get("identifiers") or {}
    return str(identifiers.get("doi") or identifiers.get("arxiv") or paper.get("id"))


def _deduplicate_papers(papers: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    selected: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for paper in papers:
        identity = _paper_identity(paper)
        previous = selected.get(identity)
        if previous is None:
            selected[identity] = paper
            continue
        duplicates += 1
        previous_score = len(previous.get("properties") or {}) + len(previous.get("identifiers") or {})
        current_score = len(paper.get("properties") or {}) + len(paper.get("identifiers") or {})
        if (current_score, str(paper.get("id"))) > (previous_score, str(previous.get("id"))):
            selected[identity] = paper
    return list(selected.values()), duplicates


def classify_paper(paper: dict[str, Any], relations: list[dict[str, Any]]) -> dict[str, Any]:
    properties = paper.get("properties") or {}
    euclid = _is_euclid(paper, relations)
    position = author_position(properties.get("author_list"))
    if not euclid:
        category = "personal"
    elif position is not None and position <= 10:
        category = "euclid_core"
    else:
        category = "euclid_collab"
    key, generated_key = _citation_key(paper)
    return {
        "paper": paper,
        "category": category,
        "author_position": position,
        "citation_key": key,
        "generated_key": generated_key,
    }


def _escape(value: Any) -> str:
    text = str(value).strip()
    return text.replace('"', '\\"').replace("\n", " ").replace("\r", " ")


def _author_value(author_list: str | None) -> str | None:
    if not isinstance(author_list, str) or not author_list.strip():
        return None
    authors = [item.strip() for item in author_list.split(";") if item.strip()]
    rendered = []
    for author in authors:
        if _normalise_name(author) in {"euclid collaboration", "euclid consortium"}:
            rendered.append("{" + author + "}")
        else:
            rendered.append(author)
    return " and ".join(rendered)


def render_bibtex(papers: list[dict[str, Any]], relations: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    rows = [classify_paper(paper, relations) for paper in papers]
    rows.sort(key=lambda row: (row["citation_key"].lower(), str(row["paper"].get("id"))))
    seen: set[str] = set()
    counts = {category: 0 for category in CATEGORY_NAMES}
    missing_keys = 0
    unresolved_positions = 0
    entries: list[str] = []
    for row in rows:
        paper = row["paper"]
        key = row["citation_key"]
        if key in seen:
            raise ValueError(f"duplicate citation key: {key}")
        seen.add(key)
        properties = paper.get("properties") or {}
        fields: list[tuple[str, Any]] = []
        author = _author_value(properties.get("author_list"))
        if author:
            fields.append(("author", author))
        if row["category"] != "personal":
            fields.append(("collaboration", "Euclid"))
        fields.append(("title", "{" + _escape(paper.get("name") or "Untitled") + "}"))
        if properties.get("arxiv_id"):
            fields.extend([("eprint", properties["arxiv_id"]), ("archivePrefix", "arXiv")])
        for source, target in (("primary_class", "primaryClass"), ("journal", "journal"), ("volume", "volume"), ("pages", "pages"), ("publication_year", "year")):
            if properties.get(source) is not None:
                fields.append((target, properties[source]))
        doi = (paper.get("identifiers") or {}).get("doi") or properties.get("doi")
        if doi:
            fields.append(("doi", str(doi).removeprefix("https://doi.org/")))
        fields.append(("keywords", row["category"]))
        body = "\n".join(f'    {name} = "{_escape(value)}",' for name, value in fields)
        entries.append(f"@article{{{key},\n{body}\n}}")
        counts[row["category"]] += 1
        if row["generated_key"]:
            missing_keys += 1
        if row["author_position"] is None and row["category"] != "personal":
            unresolved_positions += 1
    report = {
        "papers": len(rows),
        "categories": counts,
        "missing_citation_keys": missing_keys,
        "unresolved_euclid_author_positions": unresolved_positions,
    }
    return "\n\n".join(entries) + ("\n" if entries else ""), report


def export_bibtex(graph_dir: Path, output: Path, dry_run: bool = False) -> dict[str, Any]:
    papers = [record for record in _load_jsonl(graph_dir / "entities.jsonl") if record.get("type") == "Paper" or record.get("rdf_type") == "bibo:AcademicArticle"]
    papers, duplicate_papers = _deduplicate_papers(papers)
    relations = _load_jsonl(graph_dir / "relations.jsonl")
    rendered, report = render_bibtex(papers, relations)
    previous = output.read_text(encoding="utf-8") if output.exists() else ""
    changed = rendered != previous
    if changed and not dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(output)
    return {"dry_run": dry_run, "output": str(output), "changed": changed, "duplicate_papers_collapsed": duplicate_papers, **report}
