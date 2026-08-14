# From Bookmark Dump to Knowledge Graph

## Context for Expanding This Draft

I am writing this blog post because a short exploratory coding session turned into a full knowledge-management design exercise. The original goal was simple: import Firefox bookmarks into Anytype. Through iterative debugging and design, this evolved into a pipeline that parses browser bookmarks, enriches them with LLM-generated metadata, clusters them into meaningful topics, and syncs both bookmarks and topic pages into Anytype as graph-connected objects.

The lesson I want to capture is not just the final tool, but the process: how agentic coding, MCP/API exploration, existing helper libraries, dry-runs, visual graph feedback, and repeated design pivots turned a flat bookmark export into a navigable personal knowledge graph. The screenshots should show the transformation: first a raw graph after importing only 53 bookmarks, then a richer graph after hundreds of bookmarks were enriched and linked through broad `Web Topic` pages.

## Working Title

From Bookmark Dump to Knowledge Graph: A Vibe-Coding Session with Anytype, LLMs, and Agentic Tooling

## Short Hook

What started as a simple “can I import my Firefox bookmarks into Anytype?” turned into a full knowledge synthesis pipeline: parsing browser exports, enriching links with an LLM, clustering them into meaningful topics, syncing them into Anytype, and watching the graph evolve from a raw bookmark cloud into a navigable knowledge map.

## Draft

This session began with a modest goal: connect my [Firefox](https://www.mozilla.org/firefox/) bookmarks to [Anytype](https://anytype.io/). I wanted my saved web resources to stop being a flat browser list and become part of my personal knowledge graph.

The first step was infrastructure. We explored Anytype’s APIs and discovered an important distinction: the [`anytype-cli`](https://github.com/anyproto/anytype-cli) headless API and the Anytype desktop REST API are different things. For my use case, the desktop API on port `31009` was the right target because it gave access to my existing Anytype space. After setting up API keys, environment variables, and a small local repository, we had a working bridge between scripts and my Anytype knowledge base.

At first, the pipeline was simple: parse a Firefox bookmark HTML export, categorize links with an LLM, and sync them as Anytype bookmark objects. The first visible result was already satisfying: around 53 bookmarks entered the graph. But the graph still looked like a raw ingestion result. There were bookmarks, icons, and links, but no real interpretive structure yet.

Suggested image caption: First ingestion: bookmarks are in Anytype, but the graph still lacks higher-level structure.

Then the debugging started. The Firefox parser was silently missing hundreds of nested bookmarks because the HTML export format was trickier than expected. Once fixed, the parsed set jumped from 53 to 649 bookmarks. That changed the project from a small import script into a real knowledge pipeline.

The next iteration added enrichment. Instead of merely importing URLs, the pipeline fetched web content, extracted readable markdown using local tooling such as [trafilatura](https://trafilatura.readthedocs.io/), and used [Blablador](https://helmholtz-blablador.fz-juelich.de/) LLM calls to classify, summarize, tag, and describe resources. This produced local artifacts first, not immediate Anytype writes, which turned out to be important: long-running enrichment needed checkpointing, incremental saves, retry behavior, and resume support.

A major design pivot happened when we moved from “bookmark import” to “knowledge synthesis.” The idea was not only to store links, but to generate topic pages that explain clusters of related bookmarks. The first synthesis attempt created too many tiny pages. It clustered by exact LLM topic labels, so only 53 bookmarks were linked into guides and many pages had only two or three resources. The graph technically worked, but conceptually it was too fragmented.

The breakthrough was switching from fine-grained labels to broad topic families. Instead of dozens of tiny “Topic Guide” pages, the pipeline now generates a small set of broader “Web Topic” pages, such as:

- AI, LLMs, and Scientific Agents
- Helmholtz and HMC
- Knowledge Graphs and Ontologies
- Research Data Management and FAIR Practice
- Research Software and Open Tools
- Metadata Standards, PIDs, and Scholarly Metadata
- Research Data Infrastructure and Repositories
- Training and Learning Resources

This linked 447 enriched bookmarks into 8 meaningful topic hubs.

Suggested image caption: After broad topic synthesis: the graph now has interpretable hubs instead of isolated bookmark islands.

The Anytype sync also evolved a lot. Early versions wrote raw object IDs into page bodies, which was ugly and not graph-native. Later versions used real Anytype object relations: each Web Topic page links to its related bookmarks, and each bookmark links back to its Web Topics. This made the graph view meaningful without polluting the markdown content.

There were also practical lessons around API behavior. Bulk syncing hundreds of bookmarks triggered Anytype rate limits, so the pipeline gained retry and backoff logic. Re-running syncs initially rewrote pages unnecessarily, causing Anytype to re-index and briefly freeze. That led to diff detection: only changed pages are updated now. This was a useful reminder that “works once” is different from “safe to run repeatedly.”

Another important design lesson was tag hygiene. The LLM generated many specific tags like `ai_environmental_impact`, `ontology_mappings`, or `pid_record`, but Anytype rejected unknown multi-select tag options. The final design keeps this safe: unknown generated tags are skipped instead of auto-created. That preserves a cleaner controlled tag vocabulary while still allowing graph links and page creation.

The repository itself also evolved. Scripts were first scattered in the root, then reorganized into a clean module under `src/knowledge_pipeline/`, with a single user-facing entry point:

```bash
uv run pipeline.py parse
uv run pipeline.py categorize --resume
uv run pipeline.py enrich
uv run pipeline.py sync --with-pages --dry-run
uv run pipeline.py sync --with-pages --report --yes
```

Under the hood, the Python pipeline handles parsing, fetching, enrichment, and synthesis, while Anytype writes go through [`anytype-agent-runtime`](https://github.com/anyproto/anytype-agent-runtime) and an existing `anytypeHelper.js` layer. This avoided rewriting the Anytype API layer from scratch and made the system fit better with the existing Anytype agent tooling.

## Key Lessons

1. Start with ingestion, but do not stop there. A bookmark dump becomes useful only after structure is added.
2. Graphs need the right granularity. Too many tiny pages create noise; a few broad hubs create navigation.
3. LLM labels are not automatically good ontology terms. Broad deterministic topic mapping worked better than exact generated labels.
4. Sync tools must be idempotent. Diff detection matters when every update triggers indexing and graph refresh.
5. Use real object relations, not textual IDs. The graph becomes meaningful only when links are first-class data.
6. Keep generated tags under control. Auto-creating every LLM-proposed tag would pollute the taxonomy.
7. Vibe coding works best when paired with inspection, dry-runs, checkpoints, and rollback-safe iteration.

## Possible Closing Paragraph

The final graph feels qualitatively different from the first import. It is no longer just “my bookmarks in Anytype.” It is a synthesized map of my web-based research interests, with AI, Helmholtz, FAIR, metadata, ontologies, tools, and infrastructure emerging as visible knowledge neighborhoods. The most interesting part is that the structure was not designed perfectly upfront. It emerged through iteration: import, inspect, fail, patch, enrich, cluster, sync, look at the graph, and refine again.

## Suggested Image Sequence

1. Before: Anytype graph after the first 53 bookmarks, no synthesis pages.
2. Middle: expanded bookmark import, more objects but still limited semantic structure.
3. After: 8 `Web Topic` hubs linking 447 bookmarks into coherent graph neighborhoods.

## Relevant Links

- [Anytype](https://anytype.io/)
- [Anytype API documentation](https://doc.anytype.io/anytype-docs/developers/anytype-api)
- [`anytype-cli`](https://github.com/anyproto/anytype-cli)
- [`anytype-agent-runtime`](https://github.com/anyproto/anytype-agent-runtime)
- [Firefox](https://www.mozilla.org/firefox/)
- [trafilatura](https://trafilatura.readthedocs.io/)
- [Blablador](https://helmholtz-blablador.fz-juelich.de/)
- [Helmholtz Metadata Collaboration](https://helmholtz-metadaten.de/)
- [FAIR Principles](https://www.go-fair.org/fair-principles/)
