# Publications Synchronization Execution Plan

## Purpose

Keep the public Jekyll website's publication bibliography derived from the
canonical knowledge graph. The knowledge graph is the source of truth; the
website BibTeX file is a generated projection.

This file is an execution runbook. Follow the steps in order. Do not reset,
checkout, or overwrite unrelated worktree changes.

## Repositories

- Knowledge graph: `/home/casas/Personal/knowledge`
- Website: `/home/casas/Personal/santiagocasas.github.io`
- Website bibliography: `_bibliography/INSPIRE-CiteAll.bib`
- Website publication page: `_pages/publications.md`
- Knowledge graph data: `graph/entities.jsonl` and `graph/relations.jsonl`
- Default generated output: `exports/publications.bib`

The knowledge repository already contains uncommitted graph-pipeline work.
Preserve it. Do not stage or commit anything unless the user explicitly asks.

## Locked Decisions

1. Generate first into `knowledge/exports/publications.bib`.
2. The exporter must accept `--output PATH`, so a later invocation can write
   directly to the website bibliography.
3. `personal` means a paper that is not Euclid-corporate.
4. Euclid papers with Santiago at INSPIRE author position 1 through 10 are
   `euclid_core`.
5. Other Euclid papers, including unresolved Santiago positions, are
   `euclid_collab`.
6. Author position uses the author order returned by INSPIRE. Do not infer a
   contribution order or add a manual override list in this iteration.
7. The website page will show three filtered sections now.
8. A recurring cross-repository GitHub Action is a future enhancement, not part
   of this execution.

## Phase 0: Inspect Before Editing

Run from each repository as appropriate:

```bash
git status --short --untracked-files=all
```

In the knowledge repository, inspect:

- `pipeline.py` CLI registration
- `src/knowledge_pipeline/kg_inspire.py`
- `src/knowledge_pipeline/kg_orcid.py`
- `src/knowledge_pipeline/kg_ingest.py`
- `tests/test_kg_ingest.py`
- `README.md` and `MAINTENANCE.md`

In the website repository, inspect:

- `_config.yml`, especially `scholar.bibliography`, `group_by`, and
  `filtered_bibtex_keywords`
- `_pages/publications.md`
- `_bibliography/INSPIRE-CiteAll.bib`
- `.github/workflows/update-citations.yml` if present

Do not assume the graph is clean or that the website repository has no local
changes.

## Phase 1: INSPIRE Citation Keys

### 1.1 Modify the INSPIRE request

In `kg_inspire.py`, add `texkeys` to the INSPIRE literature API `fields`
parameter. Keep all existing fields.

### 1.2 Store a stable citation key

For each literature metadata record:

- Read `metadata["texkeys"]`.
- Select the first non-empty `value`.
- Store it as `properties["citation_key"]`.
- If no texkey exists, leave the property absent. Do not invent a key during
  ingestion.

The exporter will report missing keys and use a deterministic fallback only if
necessary.

### 1.3 Test the mapping

Extend the existing INSPIRE fake-session tests or add focused tests proving:

- `texkeys` is requested.
- A metadata record with `texkeys: [{"value": "Euclid:2026abc"}]` produces
  `citation_key: "Euclid:2026abc"`.
- Repeated ingestion remains idempotent.

## Phase 2: Reconcile Euclid Corporate Authorship

The existing ORCID reconciliation code detects Euclid corporate authorship and
writes relations with:

- predicate: `corporate_authored_by`
- object: `institution:name:euclid-collaboration`

After Phase 1, preview and then apply the reconciliation:

```bash
uv run pipeline.py kg ingest-orcid --dry-run
uv run pipeline.py kg ingest-orcid
```

Do not apply if the dry-run reports unexpected deletions or a network/API
error. The expected operation is to preserve existing sources and add or
refresh derived relations. Record the reported paper/relation counts in the
execution notes.

## Phase 3: Implement the BibTeX Exporter

### 3.1 New module

Create `src/knowledge_pipeline/kg_export_bibtex.py`.

The module must be pure local transformation: read canonical JSONL and write
BibTeX. It must not call Anytype, INSPIRE, ORCID, ADS, or an LLM.

Recommended public functions:

- `load_graph(graph_dir: Path) -> tuple[list[dict], list[dict]]`
- `classify_paper(paper: dict, relations: list[dict]) -> dict`
- `render_bibtex(papers: list[dict], relations: list[dict]) -> tuple[str, dict]`
- `export_bibtex(graph_dir: Path, output: Path, dry_run: bool = False) -> dict`

Keep helper functions small and deterministic.

### 3.2 Paper selection

Export only entities where:

- `type == "Paper"`, or
- `rdf_type == "bibo:AcademicArticle"`.

Sort entries deterministically by citation key, then paper ID. Do not rely on
JSONL input order.

### 3.3 Euclid detection

A paper is Euclid-corporate if either condition is true:

1. It has a relation whose predicate is `corporate_authored_by` and whose
   object is `institution:name:euclid-collaboration`.
2. Its paper properties contain a non-empty `collaboration` value equal to
   `Euclid` or `Euclid Collaboration` (case-insensitive).

The explicit relation is preferred, but the metadata fallback is required so
fresh INSPIRE records remain classifiable before reconciliation is applied.

### 3.4 Santiago author position

Read `properties.author_list`, which is a semicolon-separated ordered list.
Ignore empty items. Match Santiago using normalized case/whitespace and these
known forms:

- `Santiago Casas`
- `Casas, Santiago`
- `S. Casas`
- `Casas, S.`

Also accept a name containing the surname `Casas` and initial `S` when the
normalized exact forms above do not match, but do not match unrelated names.
Return a 1-based position, or `None` when no match is found.

For very large author lists, use the complete canonical `author_list`; do not
use the truncated Anytype projection.

### 3.5 Categories

Assign exactly one keyword:

- `personal`: not Euclid-corporate
- `euclid_core`: Euclid-corporate and Santiago position is 1 through 10
- `euclid_collab`: Euclid-corporate and Santiago position is greater than 10
  or unresolved

The exporter report must include counts for all categories and a count of
missing citation keys and unresolved author positions.

### 3.6 BibTeX fields

Render one `@article{KEY, ...}` per paper. Prefer graph properties and
identifiers as follows:

- `author`: `author_list`, preserving INSPIRE order; convert semicolons to
  BibTeX `and` separators only if needed by the existing website convention.
- `title`: paper `name`, escaped for BibTeX.
- `collaboration`: `Euclid` for Euclid-corporate papers, omitted otherwise.
- `eprint`: `arxiv_id`, if present.
- `archivePrefix`: `arXiv` when `eprint` is present.
- `primaryClass`: `primary_class`, if present.
- `doi`: normalized DOI value, preferably without a duplicate URL wrapper if
  that matches existing website entries.
- `journal`: `journal`, if present.
- `volume`, `pages`: include when present.
- `year`: `publication_year`, if present.
- `note`: include useful existing note metadata only when available.
- `keywords`: exactly one of `personal`, `euclid_core`, `euclid_collab`.

Use `citation_key` first. If missing, generate a stable fallback from the
paper ID (for example `paper_arxiv_2405_13491`) and include it in the report
as a warning. Never emit duplicate citation keys; fail clearly or suffix
duplicates deterministically.

Escape BibTeX-sensitive characters without corrupting LaTeX already present in
titles. Preserve Unicode unless the existing project requires ASCII.

### 3.7 CLI wiring

In `pipeline.py`, add `kg export-bibtex` with:

```text
--graph-dir PATH       default graph/
--output PATH          default exports/publications.bib
--dry-run              report without writing
```

The command should print JSON or a concise structured report including output,
paper count, category counts, missing keys, unresolved positions, and changed
status. Create the output parent directory when applying.

## Phase 4: Tests and Local Export

Add `tests/test_kg_export_bibtex.py` using a temporary graph. Cover at least:

1. Personal paper classification.
2. Euclid core classification at positions 1 and 10.
3. Euclid collaboration classification at position 11.
4. Unresolved Santiago position goes to `euclid_collab`.
5. Explicit `corporate_authored_by` relation detection.
6. `collaboration` metadata fallback detection.
7. Name-variant matching.
8. Deterministic output and idempotent second export.
9. Missing citation-key fallback/reporting.
10. BibTeX escaping and expected keywords.

Run one focused verification round after implementation:

```bash
uv run python -m unittest tests.test_kg_ingest tests.test_kg_export_bibtex
uv run pipeline.py kg export-bibtex --dry-run
uv run pipeline.py kg export-bibtex
```

Inspect the generated report and sample output. Use a small script or standard
tools to count `keywords` values and verify no duplicate keys. Do not rerun
network ingestion repeatedly.

Checkpoint: stop and review the category counts and at least three generated
entries before replacing the website file. If counts are implausible, inspect
graph relations and author-list formats rather than weakening the classifier.

## Phase 5: Website Projection

After the generated output is reviewed, copy it into the website repository:

```bash
cp /home/casas/Personal/knowledge/exports/publications.bib \
  /home/casas/Personal/santiagocasas.github.io/_bibliography/INSPIRE-CiteAll.bib
```

Before editing, inspect the website diff and preserve unrelated changes.

Update `_pages/publications.md` to render three sections inside the existing
`<div class="publications">`:

```liquid
<h2>Personal Publications</h2>
{% bibliography -f {{ site.scholar.bibliography }} -q "keywords: personal" %}

<h2>Euclid Publications with Santiago in the First 10 Authors</h2>
{% bibliography -f {{ site.scholar.bibliography }} -q "keywords: euclid_core" %}

<h2>Other Euclid Collaboration Publications</h2>
{% bibliography -f {{ site.scholar.bibliography }} -q "keywords: euclid_collab" %}
```

Confirm that `keywords` is listed in `_config.yml`'s
`filtered_bibtex_keywords`; if absent, add it so internal classification tags
are not shown in citation details.

Run one website verification round, if dependencies are already available:

```bash
bundle exec jekyll build
```

If dependencies are unavailable, perform static checks instead:

- bibliography file parses structurally
- every entry has exactly one category keyword
- publication page contains all three filters
- no accidental changes outside the intended bibliography/page files

Do not commit or push either repository unless explicitly requested.

## Future Phase: Recurring GitHub Synchronization

Do not implement during this run. Possible design:

1. A scheduled workflow in the knowledge repo runs INSPIRE ingestion and export.
2. It opens an authenticated pull request in the website repo, or publishes a
   versioned artifact consumed by a website workflow.
3. The website workflow validates the BibTeX and updates
   `_bibliography/INSPIRE-CiteAll.bib`.
4. Secrets/tokens, cross-repository permissions, failure notifications, and
   review policy must be decided before implementation.

## Execution Results

The plan was executed on 2026-08-27. INSPIRE returned 242 works. ORCID
reconciliation reported 135 Euclid-corporate papers and 179 derived
author-paper updates. The exporter produced 247 unique publication entries:
114 `personal`, 12 `euclid_core`, and 121 `euclid_collab`. Four duplicate
canonical records were collapsed by DOI/arXiv identity; seven entries used
deterministic fallback citation keys; one Euclid paper had an unresolved
Santiago position. The generated file parsed as 247 entries with 247 unique
keys and was copied byte-for-byte to the website bibliography.

The website build was not run because `bundle` is unavailable in the current
environment. Static validation passed. No commits or pushes were made.

## Completion Checklist

- [x] Durable plan remains at `PUBLICATIONS_SYNC_PLAN.md`.
- [x] INSPIRE stores `citation_key` from `texkeys`.
- [x] ORCID reconciliation was previewed and applied only after review.
- [x] Exporter is pure, deterministic, and has no Anytype/network dependency.
- [x] CLI supports dry-run and custom output path.
- [x] Tests cover classification, matching, rendering, and idempotence.
- [x] `exports/publications.bib` was generated and inspected.
- [x] Website bibliography was updated only after graph output review.
- [x] Website page renders three filtered categories.
- [x] Verification was run once and results recorded.
- [x] No unrequested commits or pushes were made.
