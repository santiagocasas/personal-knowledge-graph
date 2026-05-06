# knowledge

Personal knowledge management repo — scripts and automation for [Anytype](https://anytype.io).

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
│   └── sync_synthesis_anytype.js
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
