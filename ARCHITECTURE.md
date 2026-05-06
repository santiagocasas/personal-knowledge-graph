# Knowledge Pipeline Architecture

## Purpose

This document explains the code architecture for the Firefox bookmarks to Anytype knowledge graph pipeline. It is written so an LLM or diagramming tool can generate an architecture diagram from it.

The pipeline turns a local Firefox bookmark export into:

- Anytype `bookmark` objects
- Enriched bookmark metadata
- Broad `Web Topic` pages
- Real graph relations between Web Topics and bookmarks

## High-Level Diagram Prompt

Generate a left-to-right architecture diagram with these layers:

1. **Inputs**: Firefox bookmark HTML export, `.env` API configuration.
2. **Local Python Pipeline**: parse, categorize, fetch, enrich, synthesize.
3. **Local Data Artifacts**: JSON manifests and markdown cache under `data/`.
4. **Anytype Runtime Bridge**: `anytype-agent-runtime` plus `anytypeHelper.js`.
5. **Anytype Desktop API**: local REST API at `http://localhost:31009`.
6. **Anytype Knowledge Graph**: bookmark objects, Web Topic pages, tags, object relations.

Show that Python owns data preparation and synthesis, while JavaScript owns Anytype object writes through the helper/runtime bridge.

## Main Entry Point

The user interacts with a single CLI:

```bash
uv run pipeline.py parse
uv run pipeline.py categorize --resume
uv run pipeline.py enrich
uv run pipeline.py sync --with-pages --dry-run
uv run pipeline.py sync --with-pages --report --yes
uv run pipeline.py verify
```

`pipeline.py` is an orchestrator. It does not contain the domain logic itself. It calls scripts in `src/knowledge_pipeline/` and passes arguments through to them.

## Components

### `pipeline.py`

Role: user-facing command router.

Responsibilities:

- Provides subcommands: `parse`, `categorize`, `enrich`, `sync`, `verify`.
- Calls Python scripts with `uv run python ...`.
- Calls Anytype JavaScript scripts through `anytype-agent-runtime`.
- Prints dry-run reports for bookmark sync and Web Topic page sync.
- Keeps the workflow simple by hiding internal script paths from the user.

### `src/knowledge_pipeline/categorize.py`

Role: bookmark parsing and first-pass LLM categorization.

Responsibilities:

- Parses `data/bookmarks.html` from Firefox.
- Preserves bookmark folder paths as context.
- Writes `data/bookmarks.json`.
- Calls the LLM to match existing tags and propose new tags.
- Writes `data/categorized.json`.

Important behavior:

- The parser handles nested Firefox bookmark folders.
- `--parse-only` refreshes parsed bookmarks without running LLM categorization.
- `--resume` avoids repeating completed categorization work.

### `src/knowledge_pipeline/fetch_markdown.py`

Role: webpage content retrieval.

Responsibilities:

- Reads categorized bookmarks.
- Fetches web pages in parallel.
- Extracts readable markdown, usually with local `trafilatura`.
- Optionally uses another fetch backend when configured.
- Writes `data/fetched_markdown.json`.
- Writes markdown cache files under `data/markdown_cache/`.

Important behavior:

- Uses parallel workers.
- Supports retries, timeouts, resume behavior, and partial progress.
- Fails fast on non-retryable HTTP errors.

### `src/knowledge_pipeline/enrich.py`

Role: LLM enrichment of fetched bookmark content.

Responsibilities:

- Reads fetched markdown and categorized bookmark metadata.
- Calls the LLM for triage, summaries, keywords, and topic labels.
- Writes `data/enriched_bookmarks.json`.

Important behavior:

- Uses checkpointed writes so long runs do not lose progress.
- Supports `--resume`.
- Classifies items into categories such as `knowledge_resource`, `tool`, `login_page`, and `other`.

### `src/knowledge_pipeline/synthesize.py`

Role: topic clustering and guide generation.

Responsibilities:

- Reads `data/enriched_bookmarks.json`.
- Filters for useful items, mainly `knowledge_resource` and `tool`.
- Groups bookmarks into broad topic families.
- Writes `data/topic_guides.json`.
- Writes human-readable markdown guides under `data/topic_guides/*.md`.

Current broad topic families:

- AI, LLMs, and Scientific Agents
- Helmholtz and HMC
- Knowledge Graphs and Ontologies
- Research Data Management and FAIR Practice
- Research Software and Open Tools
- Metadata Standards, PIDs, and Scholarly Metadata
- Research Data Infrastructure and Repositories
- Training and Learning Resources

Important behavior:

- The default topic mode is broad clustering.
- Original LLM topic labels are retained as subtopics inside each guide.
- Broad clustering avoids creating too many tiny pages.

### `src/knowledge_pipeline/import.py`

Role: Python wrapper around bookmark sync.

Responsibilities:

- Calls `import_anytype.js` through `anytype-agent-runtime`.
- Streams runtime output live so sync progress is visible.
- Parses the final JSON result printed by the runtime.
- Provides `--report`, `--dry-run`, `--verify`, `--yes`, retry flags, and limits.

### `src/knowledge_pipeline/import_anytype.js`

Role: bookmark object sync into Anytype.

Responsibilities:

- Uses `anytypeHelper.js` to talk to Anytype.
- Bulk-fetches existing bookmark objects.
- Builds a URL index from bookmark `source` properties.
- Creates missing bookmarks.
- Updates changed bookmarks.
- Skips unchanged bookmarks.

Important behavior:

- Sync is idempotent: unchanged bookmarks are not rewritten.
- Uses retry and exponential backoff for Anytype rate limits.
- Does not use direct raw fetch calls; it uses the helper layer.

### `src/knowledge_pipeline/sync_synthesis_anytype.js`

Role: Web Topic page sync into Anytype.

Responsibilities:

- Reads `data/topic_guides.json`.
- Creates or updates Anytype `page` objects named `Web Topic: <topic>`.
- Adds valid existing Anytype tags to each page.
- Adds `related_bookmarks` object relations from pages to bookmarks.
- Adds reverse `topic_guides` object relations from bookmarks back to pages.

Important behavior:

- Unknown generated tags are skipped instead of auto-created.
- Dry-run reports show pages to create, pages to update, unchanged pages, matched bookmark links, and skipped tags.
- Diff detection prevents rewriting unchanged pages.
- Uses retry and backoff for Anytype rate limits.

### Utility Scripts

`deduplicate_anytype.js`:

- Finds duplicate bookmark objects by URL.
- Can archive duplicate objects after a dry-run.

`archive_old_guides.js`:

- Archives old pages whose names start with `Topic Guide:`.
- Used after renaming the new synthesized pages to `Web Topic:`.

`audit_graph_anytype.js`:

- Counts graph objects and relation edges.
- Helps detect runaway graph fan-out or duplicate topic pages.

## Data Artifacts

All generated data lives under `data/` and should remain local/private.

```text
data/bookmarks.html
data/bookmarks.json
data/categorized.json
data/fetched_markdown.json
data/markdown_cache/*.md
data/enriched_bookmarks.json
data/topic_guides.json
data/topic_guides/*.md
```

These files may contain personal browsing history, work links, private URLs, summaries of internal resources, and derived metadata. They are intentionally gitignored.

## Anytype Integration

Anytype writes are handled by:

```text
pipeline.py
  -> src/knowledge_pipeline/import.py
  -> anytype-agent-runtime
  -> anytypeHelper.js
  -> Anytype desktop REST API
  -> Anytype space
```

For Web Topic pages:

```text
pipeline.py
  -> anytype-agent-runtime
  -> src/knowledge_pipeline/sync_synthesis_anytype.js
  -> anytypeHelper.js
  -> Anytype desktop REST API
  -> Anytype page/bookmark objects
```

The Anytype desktop REST API runs locally, typically at:

```text
http://localhost:31009
```

API keys and space IDs are read from `.env`.

## Graph Model

The final Anytype graph has these object types and relations:

### Bookmark Objects

Type: `bookmark`

Important properties:

- `name`: bookmark title
- `source`: original URL
- `tag`: existing Anytype tags
- `description`: LLM-generated short description
- `topic_guides`: object relation pointing to Web Topic pages

### Web Topic Pages

Type: `page`

Name format:

```text
Web Topic: <topic>
```

Important properties:

- `markdown`: generated guide content
- `tag`: valid existing Anytype tags
- `related_bookmarks`: object relation pointing to bookmark objects

### Relations

The graph is intentionally bidirectional:

- `Web Topic -> related_bookmarks -> Bookmark`
- `Bookmark -> topic_guides -> Web Topic`

This makes the Anytype graph view show broad topic hubs connected to the bookmark neighborhoods they summarize.

## End-to-End Flow

```text
Firefox bookmarks.html
  -> parse nested bookmark folders
  -> bookmarks.json
  -> LLM categorization
  -> categorized.json
  -> fetch readable markdown
  -> fetched_markdown.json + markdown cache
  -> LLM enrichment
  -> enriched_bookmarks.json
  -> broad topic synthesis
  -> topic_guides.json + guide markdown
  -> Anytype bookmark sync
  -> Anytype Web Topic page sync
  -> Anytype graph with topic hubs and bookmark neighborhoods
```

## Operational Safeguards

- `.env` is gitignored and contains API keys.
- `data/` is gitignored and contains personal browsing/export data.
- Dry-runs preview creates/updates before writes.
- Syncs use diff detection to avoid rewriting unchanged objects.
- Syncs use retry/backoff to handle Anytype rate limits.
- Unknown generated tags are skipped to avoid polluting the Anytype tag vocabulary.
- Old fragmented `Topic Guide:` pages can be archived separately.

## Suggested Diagram Variants

### Diagram 1: System Architecture

Show the pipeline as boxes:

```text
Firefox Export -> Python Pipeline -> Local Data Artifacts -> Anytype Runtime Bridge -> Anytype API -> Anytype Graph
```

### Diagram 2: Data Flow

Show each artifact in order:

```text
bookmarks.html -> bookmarks.json -> categorized.json -> fetched_markdown.json -> enriched_bookmarks.json -> topic_guides.json -> Anytype objects
```

### Diagram 3: Graph Model

Show object nodes:

```text
Web Topic Page <-> Bookmark Objects
Web Topic Page -> tag options
Bookmark Object -> source URL
```

### Diagram 4: Runtime Boundary

Show that Python prepares data and JavaScript writes to Anytype:

```text
Python scripts: parse/fetch/enrich/synthesize
JavaScript runtime scripts: import_anytype/sync_synthesis_anytype
Shared boundary: JSON files under data/
```
