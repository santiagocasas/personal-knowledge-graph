# Keeping the Knowledge Graph Up to Date

## Recommended Cadence

Run the update workflow every 2-4 weeks, or whenever you have added a meaningful batch of Firefox bookmarks.

This pipeline is designed to be re-run. It is mostly idempotent:

- existing bookmarks are matched by URL
- unchanged bookmarks are skipped
- changed bookmarks are updated
- new bookmarks are created
- Web Topic pages are only rewritten when their content or relations changed

## Manual Update Workflow

### 1. Export bookmarks from Firefox

In Firefox:

```text
Bookmarks menu -> Manage Bookmarks -> Import and Backup -> Export Bookmarks to HTML
```

Save/overwrite:

```text
data/bookmarks.html
```

### 2. Parse and categorize new bookmarks

```bash
uv run pipeline.py parse
uv run pipeline.py categorize --resume
```

`--resume` avoids repeating LLM categorization for bookmarks that are already in `data/categorized.json`.

### 3. Fetch, enrich, and regenerate Web Topics

```bash
uv run pipeline.py enrich --workers 12
```

This refreshes:

- `data/fetched_markdown.json`
- `data/enriched_bookmarks.json`
- `data/topic_guides.json`
- `data/topic_guides/*.md`

The enrichment step checkpoints progress, so interrupted runs can be resumed.

### 4. Preview Anytype changes

```bash
uv run pipeline.py sync --with-pages --dry-run
```

Check:

- bookmarks to create/update
- Web Topic pages to create/update
- bookmark links matched/unmatched
- skipped generated tags

### 5. Sync to Anytype

```bash
uv run pipeline.py sync --with-pages --report --yes --retry-count 6 --retry-delay-ms 1500
```

Use the slower retry settings for larger syncs to avoid Anytype API rate limits.

### 6. Verify

```bash
uv run pipeline.py verify
```

## Reminder Suggestions

If using a reminder bot or calendar, use a recurring reminder like:

```text
Update personal knowledge graph: export Firefox bookmarks, then run parse/categorize/enrich/sync.
```

Good schedules:

- every 2 weeks on Friday morning
- first Friday of every month
- after research sprints or conference/workshop weeks

## Why Not Fully Automate Firefox Export?

The current reliable source is Firefox's manual HTML export. Fully automating bookmark extraction would require reading Firefox's profile database or using browser sync APIs, which can be fragile across machines, profiles, and corporate restrictions.

The safest workflow is therefore:

```text
manual Firefox export -> automated pipeline refresh -> Anytype graph sync
```
