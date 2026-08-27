"""Natural-language questions over the canonical graph."""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

import requests
from rdflib import RDF, URIRef
from rdflib.plugins.sparql import prepareQuery
from dotenv import load_dotenv

from kg_rdf import PKG, RESOURCE, SCHEMA, RdfError, _read_jsonl, query_graph, resource_uri


load_dotenv(Path(__file__).resolve().parents[2] / ".env")


BLABLADOR_BASE_URL = "https://api.helmholtz-blablador.fz-juelich.de/v1"
BLABLADOR_MODEL = "alias-code"
PREFIX_HEADER = (
    "PREFIX bibo: <http://purl.org/ontology/bibo/>\n"
    "PREFIX fabio: <http://purl.org/spar/fabio/>\n"
    "PREFIX foaf: <http://xmlns.com/foaf/0.1/>\n"
    "PREFIX pkg: <https://w3id.org/personal-kg/ontology/>\n"
    "PREFIX schema: <https://schema.org/>\n"
    "PREFIX skos: <http://www.w3.org/2004/02/skos/core#>\n"
    "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"
)


class AskError(ValueError):
    """Raised when a natural-language graph question cannot be answered safely."""


def _type_key(rdf_type: str | None) -> str:
    return rdf_type or "unknown"


def build_schema_card(graph_dir: Path) -> dict[str, Any]:
    """Census the vocabulary actually present in canonical JSONL."""
    records: list[dict[str, Any]] = []
    for filename in ("entities.jsonl", "concepts.jsonl", "scopes.jsonl"):
        records.extend(_read_jsonl(graph_dir / filename))
    relations = _read_jsonl(graph_dir / "relations.jsonl")
    aliases = _read_jsonl(graph_dir / "aliases.jsonl")

    id_to_type = {str(row.get("id")): _type_key(row.get("rdf_type")) for row in records}
    class_counts = Counter(id_to_type.values())
    properties: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, list[str]] = defaultdict(list)
    for row in records:
        kind = _type_key(row.get("rdf_type"))
        for key, value in (row.get("properties") or {}).items():
            if value is not None and value != "":
                properties[kind][key] += 1
        if row.get("name") and len(examples[kind]) < 3:
            examples[kind].append(str(row["name"]))

    predicate_counts = Counter(str(row.get("predicate")) for row in relations if row.get("predicate"))
    domains: dict[str, Counter[str]] = defaultdict(Counter)
    ranges: dict[str, Counter[str]] = defaultdict(Counter)
    for row in relations:
        predicate = row.get("predicate")
        if not predicate:
            continue
        domains[predicate][id_to_type.get(str(row.get("subject")), "unknown")] += 1
        ranges[predicate][id_to_type.get(str(row.get("object")), "unknown")] += 1

    known_pkg_terms = set(predicate_counts)
    for values in properties.values():
        known_pkg_terms.update(values)
    known_pkg_terms.add("canonical_id")
    return {
        "classes": dict(class_counts),
        "class_properties": {key: dict(value) for key, value in properties.items()},
        "predicates": {
            key: {
                "count": predicate_counts[key],
                "domains": dict(domains[key]),
                "ranges": dict(ranges[key]),
            }
            for key in sorted(predicate_counts)
        },
        "examples": dict(examples),
        "alias_count": len(aliases),
        "node_count": len(records),
        "relation_count": len(relations),
        "known_pkg_terms": sorted(known_pkg_terms),
        "known_classes": sorted(class_counts),
    }


def render_schema_card(card: dict[str, Any]) -> str:
    lines = [
        "RDF prefixes: bibo, fabio, foaf, pkg, schema, skos, rdf.",
        "Use schema:name for labels; there is no pkg:name. Use schema:alternateName for aliases.",
        "Every node has pkg:canonical_id. Use raw canonical predicates, not Anytype property names.",
        f"Graph size: {card['node_count']} nodes, {card['relation_count']} relations, {card['alias_count']} aliases.",
        "Classes present:",
    ]
    for name, count in sorted(card["classes"].items(), key=lambda item: (-item[1], item[0])):
        props = card["class_properties"].get(name, {})
        prop_text = ", ".join(
            key for key, _ in sorted(props.items(), key=lambda item: (-item[1], item[0]))[:20]
        ) or "none"
        sample = "; ".join(card["examples"].get(name, []))
        line = f"- {name}: {count} nodes; literal pkg properties: {prop_text}"
        if sample:
            line += f"; examples: {sample}"
        lines.append(line)
    lines.append("Relations:")
    for predicate, info in sorted(card["predicates"].items(), key=lambda item: (-item[1]["count"], item[0])):
        domains = ", ".join(f"{key} ({value})" for key, value in info["domains"].items())
        ranges = ", ".join(f"{key} ({value})" for key, value in info["ranges"].items())
        lines.append(f"- pkg:{predicate}: {info['count']} edges; from {domains}; to {ranges}")
    return "\n".join(lines)


def _leading_comments(path: Path) -> str:
    comments = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            comments.append(line.lstrip("# "))
        elif line.strip():
            break
    return " ".join(comments) or path.stem


def load_examples(queries_dir: Path) -> str:
    examples = [
        "Question: How many papers are in the graph?\nSELECT (COUNT(?paper) AS ?count) WHERE { ?paper a bibo:AcademicArticle . }",
        "Question: Which papers did Santiago Casas author?\nSELECT ?paperName WHERE { ?paper a bibo:AcademicArticle ; schema:name ?paperName ; pkg:authored_by <https://w3id.org/personal-kg/resource/person%3Aname%3As-casas> . } LIMIT 20",
    ]
    for path in sorted(queries_dir.glob("*.rq")):
        examples.append(f"Example: {_leading_comments(path)}\n{path.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(examples)


def call_llm(messages: list[dict[str, str]], model: str = BLABLADOR_MODEL, timeout: int = 60, api_key: str | None = None) -> str:
    key = api_key or os.getenv("BLABLADOR_API_KEY", "")
    if not key:
        raise AskError("BLABLADOR_API_KEY is missing")
    response = requests.post(
        f"{BLABLADOR_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "temperature": 0},
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise AskError(f"Blablador request failed with HTTP {response.status_code}")
    try:
        payload = response.json()
        return str(payload["choices"][0]["message"]["content"])
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AskError("Blablador returned an invalid completion") from exc


def clean_query(raw: str) -> str:
    value = raw.strip()
    value = re.sub(r"^```(?:sparql)?\s*|\s*```$", "", value, flags=re.IGNORECASE | re.DOTALL).strip()
    if value.upper() == "UNANSWERABLE":
        raise AskError("The question is outside the graph vocabulary")
    value = re.sub(r"^\s*PREFIX\s+\w+:\s*<[^>]+>\s*$", "", value, flags=re.IGNORECASE | re.MULTILINE).strip()
    if not value:
        raise AskError("The model returned an empty query")
    return PREFIX_HEADER + "\n" + value


def _uri_terms(node: Any) -> set[URIRef]:
    terms: set[URIRef] = set()
    if isinstance(node, URIRef):
        terms.add(node)
    elif isinstance(node, (list, tuple, set)):
        for item in node:
            terms.update(_uri_terms(item))
        return terms
    children = node.values() if isinstance(node, Mapping) else getattr(node, "__dict__", {}).values()
    for child in children:
        if isinstance(child, (list, tuple, set)):
            for item in child:
                terms.update(_uri_terms(item))
        else:
            terms.update(_uri_terms(child))
    return terms


def validate_query(query: str, card: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    try:
        parsed = prepareQuery(query)
    except Exception as exc:
        return [f"SPARQL syntax error: {exc}"]
    known = {str(PKG[term]) for term in card["known_pkg_terms"]}
    for term in _uri_terms(parsed.algebra):
        if str(term).startswith(str(PKG)) and str(term) not in known:
            local = str(term)[len(str(PKG)):]
            hint = "; use schema:name for labels" if local == "name" else ""
            problems.append(f"Unknown pkg term pkg:{local}{hint}")
    return problems


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (result or "question")[:50].rstrip("-")


def save_query(query: str, question: str, model: str, queries_dir: Path) -> Path:
    target_dir = queries_dir / "generated"
    target_dir.mkdir(parents=True, exist_ok=True)
    base = _slug(question)
    candidate = target_dir / f"{base}.rq"
    suffix = 2
    while candidate.exists() and candidate.read_text(encoding="utf-8") != query:
        candidate = target_dir / f"{base}-{suffix}.rq"
        suffix += 1
    if not candidate.exists():
        candidate.write_text(f"# Question: {question}\n# Model: {model}\n\n{query}\n", encoding="utf-8")
    return candidate


def ask(question: str, graph_dir: Path, profile_path: Path, queries_dir: Path, model: str = BLABLADOR_MODEL,
        max_repairs: int = 2, execute: bool = True, timeout: int = 60,
        llm: Callable[[list[dict[str, str]]], str] | None = None) -> dict[str, Any]:
    card = build_schema_card(graph_dir)
    system = (
        "Generate only one SPARQL SELECT query, without PREFIX lines. Use only the vocabulary in the schema card. "
        "Return exactly UNANSWERABLE when the graph cannot answer the question. Always bind schema:name for labels. "
        "Use LIMIT 20 except aggregate counts. Numeric pkg properties can be ordered directly.\n\n"
        + render_schema_card(card) + "\n\nExamples:\n" + load_examples(queries_dir)
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": question}]
    generate = llm or (lambda prompt: call_llm(prompt, model=model, timeout=timeout))
    repairs = 0
    attempts = 0
    final_query = ""
    rows: list[dict[str, Any]] = []
    while True:
        attempts += 1
        try:
            final_query = clean_query(generate(messages))
        except AskError:
            raise
        problems = validate_query(final_query, card)
        if problems:
            if repairs >= max_repairs:
                raise AskError("Generated query failed validation: " + " | ".join(problems))
            repairs += 1
            messages.extend([
                {"role": "assistant", "content": final_query},
                {"role": "user", "content": "Repair the query. Problems: " + "; ".join(problems)},
            ])
            continue
        if execute:
            try:
                rows = query_graph(graph_dir, profile_path, final_query)
            except RdfError as exc:
                raise AskError(str(exc)) from exc
            if not rows and repairs == 0 and max_repairs > 0:
                repairs += 1
                messages.extend([
                    {"role": "assistant", "content": final_query},
                    {"role": "user", "content": "The valid query returned zero rows. Try one corrected query using only the schema card."},
                ])
                continue
        break
    query_file = save_query(final_query, question, model, queries_dir)
    return {
        "question": question,
        "model": model,
        "attempts": attempts,
        "query_file": str(query_file),
        "query": final_query,
        "executed": execute,
        "row_count": len(rows),
        "rows": rows,
    }
