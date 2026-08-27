"""Export and query the canonical JSONL graph as RDF."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from rdflib import Dataset, Literal, Namespace, RDF, URIRef

from kg_profile import load_profile


PKG = Namespace("https://w3id.org/personal-kg/ontology/")
RESOURCE = "https://w3id.org/personal-kg/resource/"
SOURCE = "https://w3id.org/personal-kg/source/"
SCHEMA = Namespace("https://schema.org/")


class RdfError(ValueError):
    """Raised when canonical records cannot be represented or queried safely."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RdfError(f"{path}:{number}: invalid JSON") from exc
        if not isinstance(record, dict):
            raise RdfError(f"{path}:{number}: expected a JSON object")
        records.append(record)
    return records


def resource_uri(canonical_id: str) -> URIRef:
    return URIRef(RESOURCE + quote(canonical_id, safe=""))


def _source_uri(source_id: str) -> URIRef:
    return URIRef(SOURCE + quote(source_id, safe=""))


def _expand(value: str, namespaces: dict[str, str]) -> URIRef:
    prefix, separator, local = value.partition(":")
    if not separator or prefix not in namespaces:
        raise RdfError(f"unknown compact RDF identifier: {value}")
    return URIRef(namespaces[prefix] + local)


def _graphs_for(dataset: Dataset, refs: Iterable[dict[str, Any]]) -> list[Any]:
    graph_ids = {
        str(ref.get("source_id"))
        for ref in refs
        if isinstance(ref, dict) and ref.get("source_id")
    }
    return [dataset.graph(_source_uri(source_id)) for source_id in sorted(graph_ids)] or [dataset.default_graph]


def _literal(value: Any) -> Literal:
    if isinstance(value, (str, int, float, bool)):
        return Literal(value)
    return Literal(json.dumps(value, sort_keys=True, ensure_ascii=False))


def _add_to_graphs(graphs: Iterable[Any], triples: Iterable[tuple[Any, Any, Any]]) -> None:
    triples = list(triples)
    for graph in graphs:
        for triple in triples:
            graph.add(triple)


def build_dataset(graph_dir: Path, profile_path: Path) -> Dataset:
    """Build an in-memory RDF dataset with one named graph per source."""
    profile = load_profile(profile_path)
    namespaces = profile["namespaces"]
    dataset = Dataset(default_union=True)
    dataset.bind("pkg", PKG)
    dataset.bind("schema", SCHEMA)
    for prefix, uri in namespaces.items():
        dataset.bind(prefix, Namespace(uri))

    records = []
    for filename in ("entities.jsonl", "concepts.jsonl", "scopes.jsonl"):
        records.extend(_read_jsonl(graph_dir / filename))

    for record in records:
        canonical_id = record.get("id")
        if not isinstance(canonical_id, str) or not canonical_id:
            raise RdfError("canonical object is missing an id")
        subject = resource_uri(canonical_id)
        triples = [(subject, PKG.canonical_id, Literal(canonical_id))]
        if record.get("rdf_type"):
            triples.append((subject, RDF.type, _expand(record["rdf_type"], namespaces)))
        if record.get("name"):
            triples.append((subject, SCHEMA.name, Literal(record["name"])))
        for key, value in sorted((record.get("properties") or {}).items()):
            if value is not None and value != "":
                triples.append((subject, PKG[key], _literal(value)))
        _add_to_graphs(_graphs_for(dataset, record.get("source_refs") or []), triples)

    for relation in _read_jsonl(graph_dir / "relations.jsonl"):
        subject = relation.get("subject")
        predicate = relation.get("predicate")
        object_id = relation.get("object")
        if not all(isinstance(value, str) and value for value in (subject, predicate, object_id)):
            raise RdfError(f"relation {relation.get('id', '<unknown>')} has invalid endpoints")
        triple = (resource_uri(subject), PKG[predicate], resource_uri(object_id))
        _add_to_graphs(_graphs_for(dataset, relation.get("source_refs") or []), [triple])

    for alias in _read_jsonl(graph_dir / "aliases.jsonl"):
        if alias.get("entity_id") and alias.get("alias"):
            triple = (resource_uri(alias["entity_id"]), SCHEMA.alternateName, Literal(alias["alias"]))
            _add_to_graphs(_graphs_for(dataset, alias.get("source_refs") or []), [triple])
    return dataset


def export_trig(graph_dir: Path, profile_path: Path, output: Path) -> dict[str, Any]:
    dataset = build_dataset(graph_dir, profile_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    dataset.serialize(destination=output, format="trig")
    return {
        "output": str(output),
        "triples": len(dataset),
        "named_graphs": sum(1 for graph in dataset.graphs() if graph.identifier != dataset.default_graph.identifier),
    }


def query_graph(graph_dir: Path, profile_path: Path, query: str) -> list[dict[str, Any]]:
    try:
        result = build_dataset(graph_dir, profile_path).query(query)
    except Exception as exc:
        raise RdfError(f"SPARQL query failed: {exc}") from exc
    rows = []
    for row in result:
        item = {}
        for variable, value in row.asdict().items():
            item[str(variable)] = value.toPython() if isinstance(value, Literal) else str(value)
        rows.append(item)
    return rows
