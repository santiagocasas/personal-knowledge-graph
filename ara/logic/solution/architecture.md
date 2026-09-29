# Architecture

## A01: Separate Browsing Scopes from Provenance and Concepts
- **Decision**: Represent user-visible subgraphs as many-to-many Graph Scope collections. Keep ingestion source provenance and semantic concepts as independent dimensions.
- **Provenance**: user-revised
- **Crystallized via**: verbal-affirmation
- **Rationale**: One entity may belong to several useful views, such as My Papers, Euclid, and a conference, while retaining one canonical identity and exact source attribution.
- **Implementation**: `ontologies/cosmology.yaml`, `src/knowledge_pipeline/kg_ingest.py`, `src/knowledge_pipeline/kg_materialize.py`, `src/knowledge_pipeline/materialize_kg_anytype.js`
- **Canonical records**: `graph/scopes.jsonl`, `graph/relations.jsonl`
- **From staging**: O01

## A02: Pull Additive Anytype Edits Before Canonical Projection
- **Decision**: Make `kg materialize` pull live object-relation additions before pushing the canonical graph. Do not interpret live removals as canonical deletions. Adopt new live Concepts with deterministic IDs and reject ambiguous collisions.
- **Provenance**: user-revised
- **Crystallized via**: artifact-commitment
- **Rationale**: The existing projection replaces managed relation lists, so capturing manual additions first prevents data loss while retaining canonical JSONL as the sole deletion authority.
- **Implementation**: `pipeline.py`, `src/knowledge_pipeline/kg_pull_anytype.py`, `src/knowledge_pipeline/pull_kg_anytype.js`, `src/knowledge_pipeline/materialize_kg_anytype.js`
- **Canonical records**: `graph/concepts.jsonl`, `graph/relations.jsonl`
- **From staging**: O02

## A03: Project Graph Scopes as Navigable Collections
- **Decision**: Derive one Anytype Collection for each canonical Graph Scope, link it through the scope's `List view` property, and synchronize its membership exactly from canonical `contains` relations.
- **Provenance**: user-revised
- **Crystallized via**: artifact-commitment
- **Rationale**: A custom basic-layout Graph Scope preserves canonical scope identity and relations but cannot itself provide Anytype's list/table interface. A derived Collection adds that interface without introducing projection-only objects into canonical JSONL.
- **Implementation**: `ontologies/cosmology.yaml`, `src/knowledge_pipeline/kg_materialize.py`, `src/knowledge_pipeline/materialize_kg_anytype.js`
- **Canonical records**: `graph/scopes.jsonl`, `graph/relations.jsonl`
- **From staging**: O03
