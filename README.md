# knowledge

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/managed%20with-uv-6f42c1.svg)](https://docs.astral.sh/uv/)
[![Anytype](https://img.shields.io/badge/syncs%20to-Anytype-111111.svg)](https://anytype.io/)
[![Firefox](https://img.shields.io/badge/imports-Firefox%20bookmarks-ff7139.svg)](https://www.mozilla.org/firefox/)
[![Status](https://img.shields.io/badge/status-experimental-orange.svg)](#)

Personal knowledge management repo — scripts and automation for [Anytype](https://anytype.io).

## Personal Knowledge Graph

The Cosmology pilot uses `ontologies/cosmology.yaml` as a declarative schema for
seven Anytype types: `Person`, `Institution`, `Paper`, `Talk`, `Concept`,
`Event`, and `Graph Scope`. The profile fixes the target space, records RDF class mappings, and
defines the Anytype properties used by later ingestion and materialization
phases.

Preview schema changes before writing:

```bash
uv run pipeline.py kg provision --dry-run
```

Provision missing types and properties idempotently:

```bash
uv run pipeline.py kg provision
```

Re-running either command reports all seven types as unchanged once the schema is
current. Provisioning creates no knowledge objects and does not materialize any
graph data.

Phase 2 ingests local BibTeX into the tracked canonical graph without writing
objects to Anytype. The default source is the BibTeX exported from the Cosmology
notes page into `data/sources/cosmology_notes.bib`:

```bash
uv run pipeline.py kg ingest --dry-run
uv run pipeline.py kg ingest
```

The command writes deterministic, source-attributed records to
`graph/entities.jsonl`, `graph/relations.jsonl`, `graph/concepts.jsonl`, and
`graph/aliases.jsonl`. Re-running it replaces only this source's contribution;
an unchanged source produces no file changes. BibTeX `and others` is retained as
an incomplete literal author list rather than expanded into invented people.

Assign generated records to one or more browsable graph scopes. Scope membership
is independent of source provenance and semantic concepts, so a paper can belong
to `My Papers`, `Euclid`, and a conference simultaneously:

```bash
uv run pipeline.py kg ingest \
  --input data/sources/my_papers.bib \
  --source-id "bibtex:my-papers" \
  --scope "my-papers=portfolio:My Papers" \
  --scope "euclid=project:Euclid"
```

The syntax is `KEY[=KIND:NAME]`. A bare key defaults to kind `collection` and a
title-cased name. Scope records live in `graph/scopes.jsonl`; membership edges
are added or removed idempotently when the source is re-ingested.

Structured conference programs can be ingested from track-grouped Markdown.
The Cosmo-26 source creates Talk, Person, Institution, Event, and track-level
Concept records, links every listed author as a presenter, and assigns all
generated objects to a conference scope:

```bash
uv run pipeline.py kg ingest-talks --dry-run
uv run pipeline.py kg ingest-talks
```

The default source is `data/sources/cosmo26_talks.md`; the default provenance
identifier is `indico:cosmo26:talks`, and the default scope is
`cosmo26=conference:Cosmo-26 2026`. Missing affiliations are preserved as an
absence of an affiliation edge rather than represented as invented institutions.
Fine-grained concepts from talk titles and abstracts are intentionally deferred
to a separate reviewable extraction workflow; the deterministic ingestor only
uses the ten source-defined topic tracks.

Phase 3 enriches existing canonical Papers through NASA ADS. Configure
`ADS_API_TOKEN` in `.env`, then preview and apply metadata updates:

```bash
uv run pipeline.py kg enrich-ads --dry-run
uv run pipeline.py kg enrich-ads
```

ADS matching prefers DOI and falls back to arXiv ID. It adds the ADS bibcode,
abstract, publication, citation count, keywords, full author list, and ADS
provenance. Complete hyperauthor lists remain in canonical JSONL; Anytype receives
only the configured first five names plus the number of additional authors.

Bootstrap the researcher's complete curated publication list from the public
ORCID record and resolve each work through ADS. Configure `ORCID_ID` and
`ADS_API_TOKEN` in `.env`, then preview before applying:

```bash
uv run pipeline.py kg ingest-orcid --dry-run
uv run pipeline.py kg ingest-orcid
```

ORCID duplicate source entries are collapsed to the researcher-selected work
groups. DOI and arXiv identifiers deduplicate them against existing canonical
Papers. Every retained work is assigned to the `My Papers` Graph Scope; works
that ADS cannot resolve remain represented with their public ORCID metadata.
Every ORCID work links to the canonical `Santiago Casas` Person. The same author
reconciliation is applied to ADS-enriched Papers from other sources when their
stored author list identifies Santiago. Papers with at most 20 authors link all
listed coauthors; larger collaboration papers link only the first five listed
people, excluding corporate author markers such as `Euclid Collaboration`.
Corporate authorship is reconciled independently of ingestion source: Papers
with an explicit Euclid Collaboration/Consortium author marker or an official
Euclid publication-series title link to the canonical `Euclid Collaboration`
Institution. Individual author affiliations remain a later curation step.

INSPIRE provides a structured publication source for HEP and cosmology. Set
`INSPIRE_AUTHOR_ID` in `.env` (or pass `--author`), preview the current list,
and then apply it when the counts and changes look correct:

```bash
uv run pipeline.py kg ingest-inspire --dry-run
uv run pipeline.py kg ingest-inspire
```

The importer uses the INSPIRE BAI behind the configured author record, fetches
the paginated literature list, preserves INSPIRE provenance and citation counts,
deduplicates DOI/arXiv records, and assigns them to `My Papers`. Complete author
lists remain canonical while large collaboration papers receive bounded Person
projection. Re-running the same source is idempotent.

Generate the website bibliography from the canonical graph. The exporter is
local and deterministic; it classifies non-Euclid papers as `personal`, Euclid
papers with Santiago in INSPIRE's first ten author positions as `euclid_core`,
and other Euclid papers as `euclid_collab`:

```bash
uv run pipeline.py kg export-bibtex --dry-run
uv run pipeline.py kg export-bibtex
```

The default output is `exports/publications.bib`. Use `--output PATH` to write
to another projection, such as the website's
`_bibliography/INSPIRE-CiteAll.bib`, after reviewing the dry-run report.

Project the canonical records into the Cosmology space after provisioning the
latest schema:

```bash
uv run pipeline.py kg provision
uv run pipeline.py kg materialize --dry-run
uv run pipeline.py kg materialize
```

Materialization is idempotent and uses `canonical_id` to match Anytype objects.
By default it first pulls additive live edits from Anytype, then projects the
complete canonical graph back to Anytype. This protects manually added object
relations from being overwritten by the projection. Anytype removals are not
imported: remove relations from canonical JSONL when deletion is intended.

New Concept objects created in Anytype are adopted during the pull, assigned a
deterministic `concept:anytype:<slug>` ID, and stamped with that `canonical_id`.
Slug collisions and links to objects without canonical IDs are reported rather
than guessed. Use phase-only modes when needed:

```bash
uv run pipeline.py kg materialize --pull-only --dry-run
uv run pipeline.py kg materialize --pull-only
uv run pipeline.py kg materialize --push-only --dry-run
uv run pipeline.py kg materialize --push-only
```

The canonical JSONL remains the source of truth. Papers, bounded author links,
Euclid Collaboration, concepts, and Graph Scopes are projected with their
semantic and scope-membership links. Each Graph Scope also gets a derived
Anytype Collection named `<scope> list`, exposed through its `List view`
property. The Collection is an exact, navigable projection of the canonical
`contains` relations; edit canonical scope membership when removing entries.

For graph questions, query the canonical data directly with local SPARQL instead
of making many Anytype MCP object calls:

```bash
uv run pipeline.py kg query --file queries/top_euclid_papers.rq
uv run pipeline.py kg export  # writes kg.trig with one named graph per source
```

`kg query` always builds from the current JSONL, so it cannot become stale. This
is suitable for an agent shell tool today and can later be exposed as a small MCP
tool accepting a read-only SPARQL query.

Ask questions in natural language with the local `kg ask` command. It uses the
live JSONL vocabulary to generate and validate SPARQL through Blablador, executes
the query by default, and saves the final query for reuse:

```bash
export BLABLADOR_API_KEY=...
uv run pipeline.py kg ask "Which Euclid Collaboration papers have the most citations?"
uv run pipeline.py kg ask "How many people are in the graph?" --no-execute
```

Generated queries are stored under `queries/generated/`. The validator rejects
projection names such as `corporate_authors` when the canonical graph uses
`corporate_authored_by`; failed queries receive up to two repair attempts.

## Structure

```
knowledge/
├── pipeline.py          # single entrypoint CLI
├── src/knowledge_pipeline/
│   ├── categorize.py
│   ├── import.py
│   ├── import_anytype.js
│   ├── deduplicate_anytype.js
│   ├── fetch_markdown.py
│   ├── enrich.py
│   ├── synthesize.py
│   ├── sync_synthesis_anytype.js
│   ├── kg_profile.py
│   ├── kg_ingest.py
│   ├── kg_orcid.py
│   ├── kg_ads.py
│   ├── kg_materialize.py
│   ├── kg_rdf.py
│   ├── kg_ask.py
│   ├── provision_kg_anytype.js
│   └── materialize_kg_anytype.js
├── ontologies/
│   └── cosmology.yaml   # declarative KG schema and Anytype projection
├── graph/               # tracked canonical entities, relations, concepts, scopes, aliases
├── review/              # future merge and concept review queues
├── pyproject.toml       # uv project + dependencies
├── .env                 # API keys (gitignored)
└── data/                # gitignored — drop input files here
    └── bookmarks.html   # Firefox bookmark export (you provide this)
```

## Firefox → Anytype Bookmark Pipeline

Imports Firefox bookmarks into a target Anytype space, with LLM-assisted tagging
via [Blablador](https://helmholtz-blablador.fz-juelich.de/).

## Simple Commands (recommended)

Use the unified CLI so the workflow stays 3-4 commands:

```bash
uv run pipeline.py parse
uv run pipeline.py categorize --resume
uv run pipeline.py enrich --workers 12
uv run pipeline.py sync --with-pages --report --yes
uv run pipeline.py verify
```

`sync --with-pages` imports bookmarks and topic-guide pages together.

For the recurring update workflow, see [`MAINTENANCE.md`](MAINTENANCE.md).

### Setup

```bash
cd knowledge
uv sync
cp .env.example .env
# Edit .env: set ANYTYPE_API_KEY, ANYTYPE_SPACE_ID, and ANYTYPE_HELPER_DIR
```

### Export bookmarks from Firefox

`Bookmarks menu → Manage Bookmarks → Import and Backup → Export Bookmarks to HTML`

Save as `data/bookmarks.html`.

---

### Step 1 — Categorize

Parses the HTML export and calls Blablador to match/propose tags:

```bash
uv run pipeline.py parse
uv run pipeline.py categorize --resume
```

Output: `data/categorized.json`

---

### Step 2 — Import

Syncs categorized bookmarks into Anytype through `anytype-agent-runtime` and
the configured `anytypeHelper.js` helper directory:

```bash
uv run pipeline.py sync --report --yes
uv run pipeline.py sync --with-pages --report --yes
uv run pipeline.py verify
```

`--report` shows a pre-sync breakdown (N to create, M to update, K to skip) before asking
whether to proceed. `--verify` only verifies existing Anytype bookmarks unless combined with
a sync run.

You can also run the JS backbone directly:

```bash
anytype-agent-runtime -e .env -m "$ANYTYPE_HELPER_DIR" src/knowledge_pipeline/import_anytype.js input=@data/categorized.json mode=report
```

### Duplicate cleanup

Preview duplicate bookmark objects grouped by `source` URL:

```bash
anytype-agent-runtime -e .env -m "$ANYTYPE_HELPER_DIR" src/knowledge_pipeline/deduplicate_anytype.js
```

Archive duplicate objects, keeping the first object for each URL:

```bash
anytype-agent-runtime -e .env -m "$ANYTYPE_HELPER_DIR" src/knowledge_pipeline/deduplicate_anytype.js yes=true
```

---

## Knowledge Synthesis Pipeline

This extends plain bookmark import into a quick synthesis flow.

### Step A — Fetch page markdown (local Trafilatura)

```bash
uv run pipeline.py enrich --workers 12
uv run pipeline.py enrich --limit 50 --workers 12
uv run pipeline.py enrich --workers 12 --fetch-backend trafilatura
uv run pipeline.py enrich --workers 8 --fetch-backend auto
```

Input: `data/categorized.json`  
Output manifest: `data/fetched_markdown.json`  
Markdown cache: `data/markdown_cache/*.md`

Default backend is local `trafilatura` (no cloud service required). Use `--fetch-backend auto`
to try trafilatura first and fall back to Jina only when needed.

### Step B — Enrich with Blablador

```bash
uv run pipeline.py enrich --workers 12
uv run pipeline.py enrich --limit 100 --workers 12
```

Model: `alias-qwen36-35b`  
Output: `data/enriched_bookmarks.json`

Each item gets:
- `triage` (`knowledge_resource`, `tool`, `login_page`, `admin_page`, `other`)
- `synthesis_summary`
- `keywords`
- `topic_label`

### Step C — Build topic guides

```bash
uv run pipeline.py enrich --min-items 3
```

Output manifest: `data/topic_guides.json`  
Guide markdown files: `data/topic_guides/*.md`

By default, synthesis groups enriched bookmarks into broad topic families such as
`Helmholtz and HMC`, `AI, LLMs, and Scientific Agents`, and
`Research Data Management and FAIR Practice`. The original LLM `topic_label`
values are retained as subtopics inside each guide.

### Step D — Sync topic pages to Anytype

```bash
uv run pipeline.py sync --with-pages --dry-run
uv run pipeline.py sync --with-pages --report --yes
```

Behavior:
- Creates or updates `page` objects named `Web Topic: <topic>`
- Links topic pages to matched bookmarks with real Anytype object relations (`withObjectLinks=true`)
- Uses `anytypeHelper.js` as the only API backbone

---

## Anytype API

The Anytype desktop app exposes a REST API at `http://localhost:31009`.
Create an API key: `Settings → API Keys → Create new`.

The import itself uses `anytypeHelper.js` as the API backbone, so bookmark lookup is a single
bulk fetch instead of per-URL search.

Docs: https://developers.anytype.io

### Key IDs

Store space-specific IDs in `.env`, not in tracked files:

```bash
ANYTYPE_SPACE_ID=your_anytype_space_id_here
ANYTYPE_TAG_PROPERTY_ID=optional_tag_property_id_override
```
