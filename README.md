# knowledge

Personal knowledge management repo — scripts and automation for [Anytype](https://anytype.io).

## Structure

```
knowledge/
├── categorize.py        # Parse Firefox export + LLM tagging → data/categorized.json
├── import.py            # Python runner for helper-backed Anytype import
├── import_anytype.js    # Anytype sync via anytypeHelper.js
├── deduplicate_anytype.js # Archive duplicate Anytype bookmarks by source URL
├── fetch_markdown.py    # URL -> markdown via Jina Reader (r.jina.ai)
├── enrich.py            # LLM triage + summary + keywords per bookmark
├── synthesize.py        # Cluster enriched bookmarks into topic guides
├── sync_synthesis_anytype.js # Create/update Anytype topic guide pages
├── pyproject.toml       # uv project + dependencies
├── .env                 # API keys (gitignored)
└── data/                # gitignored — drop input files here
    └── bookmarks.html   # Firefox bookmark export (you provide this)
```

## Firefox → Anytype Bookmark Pipeline

Imports Firefox bookmarks into the **Ontologist** Anytype space, with LLM-assisted tagging
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
cd ~/Personal/knowledge
uv sync
cp .env.example .env
# Edit .env: set ANYTYPE_API_KEY and ANYTYPE_SPACE_ID
```

### Export bookmarks from Firefox

`Bookmarks menu → Manage Bookmarks → Import and Backup → Export Bookmarks to HTML`

Save as `data/bookmarks.html`.

---

### Step 1 — Categorize

Parses the HTML export and calls Blablador to match/propose tags:

```bash
uv run categorize.py                  # full run
uv run categorize.py --dry-run        # show LLM prompt, no API calls
uv run categorize.py --resume         # continue interrupted run
uv run categorize.py --skip-parse     # reuse existing bookmarks.json
uv run categorize.py --batch-size 5   # smaller batches
```

Output: `data/categorized.json`

---

### Step 2 — Import

Syncs categorized bookmarks into Anytype through `anytype-agent-runtime` and
`~/Personal/anytype-agents-skill/anytypeHelper.js`:

```bash
uv run import.py --report             # diff what will change, then confirm
uv run import.py --dry-run            # report only, no changes at all
uv run import.py --report --yes       # report then sync without prompting
uv run import.py --verify             # sync then verify all bookmarks exist
uv run import.py --limit 10 --report  # test with first 10 bookmarks
uv run import.py -v                   # full sync, verbose
```

`--report` shows a pre-sync breakdown (N to create, M to update, K to skip) before asking
whether to proceed. `--verify` only verifies existing Anytype bookmarks unless combined with
a sync run.

You can also run the JS backbone directly:

```bash
anytype-agent-runtime -e .env -m ~/Personal/anytype-agents-skill import_anytype.js input=@data/categorized.json mode=report
```

### Duplicate cleanup

Preview duplicate bookmark objects grouped by `source` URL:

```bash
anytype-agent-runtime -e .env -m ~/Personal/anytype-agents-skill deduplicate_anytype.js
```

Archive duplicate objects, keeping the first object for each URL:

```bash
anytype-agent-runtime -e .env -m ~/Personal/anytype-agents-skill deduplicate_anytype.js yes=true
```

---

## Knowledge Synthesis Pipeline

This extends plain bookmark import into a quick synthesis flow.

### Step A — Fetch page markdown (Jina Reader)

```bash
uv run fetch_markdown.py
uv run fetch_markdown.py --limit 50
uv run fetch_markdown.py --resume
uv run fetch_markdown.py --resume --workers 12
```

Input: `data/categorized.json`  
Output manifest: `data/fetched_markdown.json`  
Markdown cache: `data/markdown_cache/*.md`

If a URL does not parse well in Jina, we leave it as-is and keep moving.

### Step B — Enrich with Blablador

```bash
uv run enrich.py
uv run enrich.py --limit 100
uv run enrich.py --resume
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
uv run synthesize.py
uv run synthesize.py --min-items 3
```

Output manifest: `data/topic_guides.json`  
Guide markdown files: `data/topic_guides/*.md`

### Step D — Sync topic pages to Anytype

```bash
anytype-agent-runtime -e .env -m ~/Personal/anytype-agents-skill sync_synthesis_anytype.js input=@data/topic_guides.json dryRun=true withObjectLinks=true
anytype-agent-runtime -e .env -m ~/Personal/anytype-agents-skill sync_synthesis_anytype.js input=@data/topic_guides.json withObjectLinks=true
```

Behavior:
- Creates or updates `page` objects named `Topic Guide: <topic>`
- Optionally embeds matched Anytype bookmark object IDs in each page (`withObjectLinks=true`)
- Uses `anytypeHelper.js` as the only API backbone

---

## Anytype API

The Anytype desktop app exposes a REST API at `http://localhost:31009`.
Create an API key: `Settings → API Keys → Create new`.

The import itself uses `anytypeHelper.js` as the API backbone, so bookmark lookup is a single
bulk fetch instead of per-URL search.

Docs: https://developers.anytype.io

### Key IDs (Ontologist space)

```
Space ID:       bafyreig6fpie6n66zh7ive6chvjrsvwxbdue6kzh5b7ljrc3i5ny2z2jui.q4gkw8g0ft1i
Tag property:   bafyreiailumqalfxxfwcocgpwbxgjis3thxrqzsghx7cfnnhtcxp27nqqu
```
