# Canonical Knowledge Graph

This directory is the tracked source of truth for the personal knowledge graph.
Each `*.jsonl` file contains one JSON object per line, sorted by stable `id`.

- `entities.jsonl`: people, institutions, papers, talks, and events
- `relations.jsonl`: directed semantic edges
- `concepts.jsonl`: SKOS-aligned concepts
- `aliases.jsonl`: source identifiers and names used for later resolution
- `scopes.jsonl`: user-facing collections for browsing focused subgraphs

Every generated record carries `source_refs`. Re-ingesting a source replaces only
that source's contribution and preserves records supported by other sources.
Anytype is a later, rebuildable projection of these files.

Scope membership is many-to-many and separate from provenance and concepts. A
record may appear in several focused subgraphs without being duplicated.

The canonical files can be queried locally as RDF without traversing Anytype:

```bash
uv run pipeline.py kg query --file queries/top_euclid_papers.rq
uv run pipeline.py kg export
```

`kg query` builds an in-memory RDF dataset from current JSONL. `kg export` writes
`kg.trig`, with semantic statements grouped into named graphs by `source_id`.
