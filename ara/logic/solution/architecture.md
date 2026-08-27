# Architecture

## A01: Separate Browsing Scopes from Provenance and Concepts
- **Decision**: Represent user-visible subgraphs as many-to-many Graph Scope collections. Keep ingestion source provenance and semantic concepts as independent dimensions.
- **Provenance**: user-revised
- **Crystallized via**: verbal-affirmation
- **Rationale**: One entity may belong to several useful views, such as My Papers, Euclid, and a conference, while retaining one canonical identity and exact source attribution.
- **Implementation**: `ontologies/cosmology.yaml`, `src/knowledge_pipeline/kg_ingest.py`, `src/knowledge_pipeline/kg_materialize.py`, `src/knowledge_pipeline/materialize_kg_anytype.js`
- **Canonical records**: `graph/scopes.jsonl`, `graph/relations.jsonl`
- **From staging**: O01
