#!/usr/bin/env python3
"""Unified CLI for bookmark knowledge pipeline."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).parent
MODULE_DIR = REPO_ROOT / "src" / "knowledge_pipeline"
HELPER_DIR = Path(os.getenv("ANYTYPE_HELPER_DIR", str(REPO_ROOT.parent / "anytype-agents-skill"))).expanduser()
RUNTIME_CANDIDATES = [
    shutil.which("anytype-agent-runtime"),
    str(Path.home() / "go" / "bin" / "anytype-agent-runtime"),
]


def runtime_path() -> str:
    for candidate in RUNTIME_CANDIDATES:
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("anytype-agent-runtime not found")


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=REPO_ROOT, check=False)
    if proc.returncode != 0:
        sys.exit(proc.returncode)


def run_capture(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        if proc.stdout:
            print(proc.stdout, end="")
        if proc.stderr:
            print(proc.stderr, end="", file=sys.stderr)
        sys.exit(proc.returncode)
    return proc.stdout or ""


def parse_runtime_res(stdout: str) -> dict:
    for line in stdout.splitlines():
        if line.startswith("res: "):
            raw = line[5:].strip()
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                continue
    return {}


def print_page_sync_report(preview: dict, show_details: bool = False) -> None:
    stats = (preview or {}).get("stats", {})
    if not stats:
        return

    print("\nPage sync report:")
    print(f"  Guides loaded            : {stats.get('guides_loaded', 0)}")
    print(f"  Pages to create          : {stats.get('planned_pages_create', 0)}")
    print(f"  Pages to update          : {stats.get('planned_pages_update', 0)}")
    print(f"  Pages unchanged          : {stats.get('pages_unchanged', 0)}")
    print(f"  Bookmark links matched   : {stats.get('bookmarks_matched', 0)}")
    print(f"  Bookmark links unmatched : {stats.get('bookmarks_unmatched', 0)}")
    print(f"  Page tags skipped        : {stats.get('page_tags_skipped', 0)}")

    if not show_details:
        return

    details = (preview or {}).get("details") or []
    creates = [row for row in details if row.get("action") == "create"]
    updates = [row for row in details if row.get("action") == "update" and row.get("changed")]
    unchanged = [row for row in details if row.get("action") == "update" and not row.get("changed")]

    if creates:
        print("\nPages that would be created:")
        for row in creates:
            print(f"  - Web Topic: {row.get('topic')} ({row.get('matched', 0)} bookmarks)")
            skipped = row.get("skipped_tags") or []
            if skipped:
                print(f"    skipped tags: {', '.join(skipped[:8])}")

    if updates:
        print("\nPages that would be updated:")
        for row in updates:
            reasons = ", ".join(row.get("change_reasons") or []) or "changes"
            print(f"  - Web Topic: {row.get('topic')} ({row.get('matched', 0)} bookmarks; {reasons})")

    if unchanged:
        print("\nPages already unchanged:")
        for row in unchanged:
            print(f"  - Web Topic: {row.get('topic')} ({row.get('matched', 0)} bookmarks)")


def print_kg_provision_report(report: dict) -> None:
    stats = (report or {}).get("stats", {})
    if not stats:
        raise RuntimeError("Anytype runtime returned no provisioning report")

    mode = "Dry-run" if report.get("dry_run") else "Provision"
    print(f"\n{mode} report for {report.get('profile')} ({report.get('space_id')}):")
    print(f"  Types defined     : {stats.get('types_defined', 0)}")
    print(f"  Types to create   : {stats.get('planned_create', 0)}")
    print(f"  Types to update   : {stats.get('planned_update', 0)}")
    print(f"  Types unchanged   : {stats.get('unchanged', 0)}")
    if not report.get("dry_run"):
        print(f"  Types created     : {stats.get('created', 0)}")
        print(f"  Types updated     : {stats.get('updated', 0)}")
        print(f"  Errors            : {stats.get('errors', 0)}")

    for row in report.get("details", []):
        action = row.get("action", "unknown")
        suffix = ""
        if row.get("missing_properties"):
            suffix = f"; properties: {', '.join(row['missing_properties'])}"
        print(f"  - {row.get('name')} [{row.get('key')}]: {action}{suffix}")
        for warning in row.get("property_warnings", []):
            print(f"    warning: {warning}")
        if row.get("error"):
            print(f"    error: {row['error']}")


def print_kg_materialize_report(report: dict) -> None:
    if report.get("error"):
        raise RuntimeError(report["error"])
    stats = (report or {}).get("stats", {})
    if not stats:
        raise RuntimeError("Anytype runtime returned no materialization report")

    mode = "Dry-run" if report.get("dry_run") else "Materialize"
    print(f"\n{mode} report for {report.get('space_id')}:")
    print(f"  Canonical objects    : {stats.get('objects_loaded', 0)}")
    print(f"  Canonical relations  : {stats.get('relations_loaded', 0)}")
    print(f"  Objects to create    : {stats.get('planned_create', 0)}")
    print(f"  Objects to update    : {stats.get('planned_update', 0)}")
    print(f"  Link sets to update  : {stats.get('planned_link_update', 0)}")
    print(f"  Objects unchanged    : {stats.get('unchanged', 0)}")
    if not report.get("dry_run"):
        print(f"  Objects created      : {stats.get('created', 0)}")
        print(f"  Objects updated      : {stats.get('updated', 0)}")
        print(f"  Link sets updated    : {stats.get('links_updated', 0)}")
        print(f"  Errors               : {stats.get('errors', 0)}")

    for row in report.get("details", []):
        if row.get("action") == "unchanged" or (row.get("action") == "link" and row.get("ok")):
            continue
        suffix = f"; {', '.join(row.get('changes') or [])}" if row.get("changes") else ""
        print(f"  - {row.get('name') or row.get('canonical_id')}: {row.get('action')}{suffix}")
        if row.get("error"):
            print(f"    error: {row['error']}")


def cmd_parse(args: argparse.Namespace) -> None:
    cmd = ["uv", "run", "python", str(MODULE_DIR / "categorize.py"), "--parse-only", "--input", str(args.input)]
    run(cmd)


def cmd_categorize(args: argparse.Namespace) -> None:
    cmd = ["uv", "run", "python", str(MODULE_DIR / "categorize.py"), "--skip-parse"]
    if args.resume:
        cmd.append("--resume")
    if args.batch_size:
        cmd.extend(["--batch-size", str(args.batch_size)])
    run(cmd)


def cmd_enrich(args: argparse.Namespace) -> None:
    fetch_cmd = [
        "uv",
        "run",
        "python",
        str(MODULE_DIR / "fetch_markdown.py"),
        "--resume",
        "--workers",
        str(args.workers),
        "--timeout",
        str(args.fetch_timeout),
        "--retries",
        str(args.fetch_retries),
        "--delay",
        str(args.fetch_delay),
        "--backend",
        args.fetch_backend,
    ]
    if args.limit:
        fetch_cmd.extend(["--limit", str(args.limit)])
    run(fetch_cmd)

    enrich_cmd = ["uv", "run", "python", str(MODULE_DIR / "enrich.py"), "--resume"]
    if args.limit:
        enrich_cmd.extend(["--limit", str(args.limit)])
    run(enrich_cmd)

    synth_cmd = ["uv", "run", "python", str(MODULE_DIR / "synthesize.py"), "--min-items", str(args.min_items)]
    run(synth_cmd)


def cmd_sync(args: argparse.Namespace) -> None:
    if args.with_pages and args.report and not args.dry_run:
        runtime = runtime_path()
        preview_cmd = [
            runtime,
            "-e",
            ".env",
            "-m",
            str(HELPER_DIR),
            str(MODULE_DIR / "sync_synthesis_anytype.js"),
            "input=@data/topic_guides.json",
            "dryRun=true",
            "withObjectLinks=true",
        ]
        if args.limit:
            preview_cmd.append(f"limit={args.limit}")
        preview_out = run_capture(preview_cmd)
        preview = parse_runtime_res(preview_out)
        print_page_sync_report(preview, show_details=True)

    import_cmd = ["uv", "run", "python", str(MODULE_DIR / "import.py")]
    if args.report:
        import_cmd.append("--report")
    if args.yes:
        import_cmd.append("--yes")
    if args.dry_run:
        import_cmd.append("--dry-run")
    if args.limit:
        import_cmd.extend(["--limit", str(args.limit)])
    import_cmd.extend(["--retry-count", str(args.retry_count), "--retry-delay-ms", str(args.retry_delay_ms)])
    run(import_cmd)

    if args.with_pages:
        runtime = runtime_path()
        page_cmd = [
            runtime,
            "-e",
            ".env",
            "-m",
            str(HELPER_DIR),
            str(MODULE_DIR / "sync_synthesis_anytype.js"),
            "input=@data/topic_guides.json",
            f"dryRun={'true' if args.dry_run else 'false'}",
            "withObjectLinks=true",
            f"retryCount={args.retry_count}",
            f"retryDelayMs={args.retry_delay_ms}",
        ]
        if args.limit:
            page_cmd.append(f"limit={args.limit}")
        if args.dry_run:
            print("$", " ".join(page_cmd))
            page_out = run_capture(page_cmd)
            page_preview = parse_runtime_res(page_out)
            print_page_sync_report(page_preview, show_details=True)
        else:
            run(page_cmd)


def cmd_verify(args: argparse.Namespace) -> None:
    verify_cmd = ["uv", "run", "python", str(MODULE_DIR / "import.py"), "--verify"]
    if args.limit:
        verify_cmd.extend(["--limit", str(args.limit)])
    run(verify_cmd)

    runtime = runtime_path()
    dry_page_cmd = [
        runtime,
        "-e",
        ".env",
        "-m",
        str(HELPER_DIR),
        str(MODULE_DIR / "sync_synthesis_anytype.js"),
        "input=@data/topic_guides.json",
        "dryRun=true",
        "withObjectLinks=true",
    ]
    if args.limit:
        dry_page_cmd.append(f"limit={args.limit}")
    run(dry_page_cmd)


def cmd_kg_provision(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(MODULE_DIR))
    from kg_profile import ProfileError, load_profile

    try:
        profile = load_profile(args.profile)
    except ProfileError as exc:
        raise SystemExit(f"Invalid ontology profile: {exc}") from exc

    cmd = [
        runtime_path(),
        "-e",
        ".env",
        "-m",
        str(HELPER_DIR),
        str(MODULE_DIR / "provision_kg_anytype.js"),
        "profile=" + json.dumps(profile, separators=(",", ":")),
        f"dryRun={'true' if args.dry_run else 'false'}",
    ]
    report = parse_runtime_res(run_capture(cmd))
    try:
        print_kg_provision_report(report)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    if report.get("stats", {}).get("errors", 0):
        raise SystemExit(1)


def cmd_kg_ingest(args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        str(MODULE_DIR / "kg_ingest.py"),
        "--input",
        str(args.input),
        "--graph-dir",
        str(args.graph_dir),
        "--source-id",
        args.source_id,
    ]
    if args.source_url:
        cmd.extend(["--source-url", args.source_url])
    for scope in args.scope:
        cmd.extend(["--scope", scope])
    if args.dry_run:
        cmd.append("--dry-run")
    run(cmd)


def cmd_kg_enrich_ads(args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        str(MODULE_DIR / "kg_ads.py"),
        "--graph-dir",
        str(args.graph_dir),
        "--timeout",
        str(args.timeout),
    ]
    if args.limit:
        cmd.extend(["--limit", str(args.limit)])
    if args.dry_run:
        cmd.append("--dry-run")
    run(cmd)


def cmd_kg_ingest_orcid(args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        str(MODULE_DIR / "kg_orcid.py"),
        "--graph-dir",
        str(args.graph_dir),
        "--timeout",
        str(args.timeout),
    ]
    if args.limit:
        cmd.extend(["--limit", str(args.limit)])
    if args.dry_run:
        cmd.append("--dry-run")
    run(cmd)


def cmd_kg_ingest_inspire(args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        str(MODULE_DIR / "kg_inspire.py"),
        "--graph-dir",
        str(args.graph_dir),
        "--timeout",
        str(args.timeout),
    ]
    if args.author:
        cmd.extend(["--author", args.author])
    if args.source_id:
        cmd.extend(["--source-id", args.source_id])
    if args.scope:
        cmd.extend(["--scope", args.scope])
    if args.dry_run:
        cmd.append("--dry-run")
    run(cmd)


def cmd_kg_export(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(MODULE_DIR))
    from kg_rdf import RdfError, export_trig

    try:
        report = export_trig(args.graph_dir, args.profile, args.output)
    except RdfError as exc:
        raise SystemExit(f"Cannot export RDF: {exc}") from exc
    print(json.dumps(report, indent=2))


def cmd_kg_export_bibtex(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(MODULE_DIR))
    from kg_export_bibtex import export_bibtex

    try:
        report = export_bibtex(args.graph_dir, args.output, args.dry_run)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Cannot export BibTeX: {exc}") from exc
    print(json.dumps(report, indent=2))


def cmd_kg_query(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(MODULE_DIR))
    from kg_rdf import RdfError, query_graph

    query = args.query if args.query is not None else args.file.read_text(encoding="utf-8")
    try:
        rows = query_graph(args.graph_dir, args.profile, query)
    except (OSError, RdfError) as exc:
        raise SystemExit(f"Cannot query graph: {exc}") from exc
    print(json.dumps(rows, indent=2, ensure_ascii=False))


def cmd_kg_ask(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(MODULE_DIR))
    from kg_ask import AskError, ask

    try:
        report = ask(
            args.question,
            args.graph_dir,
            args.profile,
            args.queries_dir,
            model=args.model,
            max_repairs=args.max_repairs,
            execute=not args.no_execute,
            timeout=args.timeout,
        )
    except (OSError, AskError) as exc:
        raise SystemExit(f"Cannot ask graph: {exc}") from exc
    print(json.dumps(report, indent=2, ensure_ascii=False))


def cmd_kg_materialize(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(MODULE_DIR))
    from kg_materialize import MaterializationError, load_materialization_manifest
    from kg_profile import ProfileError, load_profile

    try:
        profile = load_profile(args.profile)
        manifest = load_materialization_manifest(
            args.graph_dir,
            profile["space_id"],
            profile.get("projection"),
        )
    except (MaterializationError, ProfileError) as exc:
        raise SystemExit(f"Cannot materialize graph: {exc}") from exc

    materializer_path = MODULE_DIR / "materialize_kg_anytype.js"
    materializer_source = materializer_path.read_text(encoding="utf-8")
    main_marker = "export function main(args) {\n  args = args || {};"
    if main_marker not in materializer_source:
        raise SystemExit("Cannot embed projection manifest: materializer entrypoint changed")
    embedded_manifest = json.dumps(manifest, separators=(",", ":"))
    runner_source = materializer_source.replace(
        main_marker,
        "const embeddedManifest = " + embedded_manifest + ";\n\n"
        "export function main(args) {\n  args = args || {};\n  args.manifest = embeddedManifest;",
        1,
    )
    runner_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".js",
            prefix=".kg_materialize_",
            dir=MODULE_DIR,
            delete=False,
        ) as runner:
            runner.write(runner_source)
            runner_path = Path(runner.name)
        cmd = [
            runtime_path(),
            "-e",
            ".env",
            "-m",
            str(HELPER_DIR),
            str(runner_path),
            f"dryRun={'true' if args.dry_run else 'false'}",
            f"retryCount={args.retry_count}",
            f"retryDelayMs={args.retry_delay_ms}",
        ]
        report = parse_runtime_res(run_capture(cmd))
    finally:
        if runner_path:
            runner_path.unlink(missing_ok=True)
    try:
        print_kg_materialize_report(report)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    if report.get("stats", {}).get("errors", 0):
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified CLI for personal knowledge pipelines")
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="Parse Firefox HTML into bookmarks.json")
    p_parse.add_argument("--input", type=Path, default=REPO_ROOT / "data" / "bookmarks.html")
    p_parse.set_defaults(func=cmd_parse)

    p_cat = sub.add_parser("categorize", help="LLM categorization and tag proposals")
    p_cat.add_argument("--resume", action="store_true")
    p_cat.add_argument("--batch-size", type=int, default=10)
    p_cat.set_defaults(func=cmd_categorize)

    p_enrich = sub.add_parser(
        "enrich",
        help="Fetch markdown, enrich, and generate topic guides",
        description="Fetches markdown (default: local trafilatura), enriches via LLM, then synthesizes topic guides.",
    )
    p_enrich.add_argument("--workers", type=int, default=12, help="Parallel fetch workers")
    p_enrich.add_argument(
        "--fetch-backend",
        choices=["trafilatura", "jina", "auto"],
        default="trafilatura",
        help="Fetch backend: local trafilatura, jina, or auto fallback",
    )
    p_enrich.add_argument("--fetch-timeout", type=int, default=12, help="Per-URL fetch timeout in seconds")
    p_enrich.add_argument("--fetch-retries", type=int, default=2, help="Retries for transient fetch failures")
    p_enrich.add_argument("--fetch-delay", type=float, default=0.0, help="Delay after each completed fetch")
    p_enrich.add_argument("--min-items", type=int, default=2, help="Minimum items per synthesized topic guide")
    p_enrich.add_argument("--limit", type=int, default=0, help="Process only first N bookmarks (0 = all)")
    p_enrich.set_defaults(func=cmd_enrich)

    p_sync = sub.add_parser("sync", help="Sync bookmarks, optionally also topic pages")
    p_sync.add_argument("--with-pages", action="store_true")
    p_sync.add_argument("--report", action="store_true")
    p_sync.add_argument("--yes", action="store_true")
    p_sync.add_argument("--dry-run", action="store_true")
    p_sync.add_argument("--limit", type=int, default=0)
    p_sync.add_argument("--retry-count", type=int, default=4, help="Retries per write when rate-limited")
    p_sync.add_argument("--retry-delay-ms", type=int, default=1200, help="Base backoff delay (ms)")
    p_sync.set_defaults(func=cmd_sync)

    p_verify = sub.add_parser("verify", help="Verify data vs Anytype status")
    p_verify.add_argument("--limit", type=int, default=0)
    p_verify.set_defaults(func=cmd_verify)

    p_kg = sub.add_parser("kg", help="Personal knowledge graph operations")
    kg_sub = p_kg.add_subparsers(dest="kg_command", required=True)
    p_provision = kg_sub.add_parser("provision", help="Provision Anytype types from an ontology profile")
    p_provision.add_argument(
        "--profile",
        type=Path,
        default=REPO_ROOT / "ontologies" / "cosmology.yaml",
        help="Ontology profile (default: ontologies/cosmology.yaml)",
    )
    p_provision.add_argument("--dry-run", action="store_true", help="Report changes without writing to Anytype")
    p_provision.set_defaults(func=cmd_kg_provision)

    p_ingest = kg_sub.add_parser("ingest", help="Ingest BibTeX into canonical graph JSONL")
    p_ingest.add_argument(
        "--input",
        type=Path,
        default=REPO_ROOT / "data" / "sources" / "cosmology_notes.bib",
        help="BibTeX source (default: data/sources/cosmology_notes.bib)",
    )
    p_ingest.add_argument("--graph-dir", type=Path, default=REPO_ROOT / "graph")
    p_ingest.add_argument(
        "--source-id",
        default="anytype:cosmology:bafyreigvzuxj6fnrm6a52xmoq7agbrdnksl66ngmbrw3vwy3m66wh6yftq",
        help="Stable provenance identifier for this source",
    )
    p_ingest.add_argument(
        "--source-url",
        default="anytype://bafyreigvzuxj6fnrm6a52xmoq7agbrdnksl66ngmbrw3vwy3m66wh6yftq",
        help="Optional source locator stored in provenance records",
    )
    p_ingest.add_argument("--dry-run", action="store_true", help="Report changes without writing graph files")
    p_ingest.add_argument(
        "--scope",
        action="append",
        default=[],
        metavar="KEY[=KIND:NAME]",
        help="Assign records to a graph scope; may be repeated",
    )
    p_ingest.set_defaults(func=cmd_kg_ingest)

    p_ads = kg_sub.add_parser("enrich-ads", help="Enrich canonical Papers from NASA ADS")
    p_ads.add_argument("--graph-dir", type=Path, default=REPO_ROOT / "graph")
    p_ads.add_argument(
        "--dry-run",
        action="store_true",
        help="Query ADS and report without writing graph files",
    )
    p_ads.add_argument("--timeout", type=int, default=30, help="Per-request timeout in seconds")
    p_ads.add_argument("--limit", type=int, default=0, help="Process only the first N papers (0 = all)")
    p_ads.set_defaults(func=cmd_kg_enrich_ads)

    p_orcid = kg_sub.add_parser(
        "ingest-orcid",
        help="Bootstrap a My Papers scope from ORCID and NASA ADS",
    )
    p_orcid.add_argument("--graph-dir", type=Path, default=REPO_ROOT / "graph")
    p_orcid.add_argument(
        "--dry-run",
        action="store_true",
        help="Query ORCID and ADS and report without writing graph files",
    )
    p_orcid.add_argument("--timeout", type=int, default=30, help="Per-request timeout in seconds")
    p_orcid.add_argument("--limit", type=int, default=0, help="Process only the first N ORCID groups (0 = all)")
    p_orcid.set_defaults(func=cmd_kg_ingest_orcid)

    p_inspire = kg_sub.add_parser("ingest-inspire", help="Ingest publications from INSPIRE")
    p_inspire.add_argument("--author", default=None, help="INSPIRE author record ID or BAI (default: INSPIRE_AUTHOR_ID)")
    p_inspire.add_argument("--graph-dir", type=Path, default=REPO_ROOT / "graph")
    p_inspire.add_argument("--source-id", default=None, help="Stable provenance identifier override")
    p_inspire.add_argument("--scope", default="my-papers=portfolio:My Papers")
    p_inspire.add_argument("--dry-run", action="store_true", help="Query INSPIRE without writing graph files")
    p_inspire.add_argument("--timeout", type=int, default=30, help="Per-request timeout in seconds")
    p_inspire.set_defaults(func=cmd_kg_ingest_inspire)

    p_export = kg_sub.add_parser("export", help="Export canonical JSONL as RDF TriG")
    p_export.add_argument("--graph-dir", type=Path, default=REPO_ROOT / "graph")
    p_export.add_argument(
        "--profile",
        type=Path,
        default=REPO_ROOT / "ontologies" / "cosmology.yaml",
    )
    p_export.add_argument("--output", type=Path, default=REPO_ROOT / "kg.trig")
    p_export.set_defaults(func=cmd_kg_export)

    p_export_bibtex = kg_sub.add_parser("export-bibtex", help="Export canonical Papers as Jekyll Scholar BibTeX")
    p_export_bibtex.add_argument("--graph-dir", type=Path, default=REPO_ROOT / "graph")
    p_export_bibtex.add_argument("--output", type=Path, default=REPO_ROOT / "exports" / "publications.bib")
    p_export_bibtex.add_argument("--dry-run", action="store_true", help="Report changes without writing the output")
    p_export_bibtex.set_defaults(func=cmd_kg_export_bibtex)

    p_query = kg_sub.add_parser("query", help="Run SPARQL directly against canonical JSONL")
    p_query.add_argument("--graph-dir", type=Path, default=REPO_ROOT / "graph")
    p_query.add_argument(
        "--profile",
        type=Path,
        default=REPO_ROOT / "ontologies" / "cosmology.yaml",
    )
    query_source = p_query.add_mutually_exclusive_group(required=True)
    query_source.add_argument("--file", type=Path, help="SPARQL query file")
    query_source.add_argument("--query", help="Inline SPARQL query")
    p_query.set_defaults(func=cmd_kg_query)

    p_ask = kg_sub.add_parser("ask", help="Ask a natural-language question using local SPARQL")
    p_ask.add_argument("question", help="Natural-language graph question")
    p_ask.add_argument("--graph-dir", type=Path, default=REPO_ROOT / "graph")
    p_ask.add_argument(
        "--profile",
        type=Path,
        default=REPO_ROOT / "ontologies" / "cosmology.yaml",
    )
    p_ask.add_argument("--queries-dir", type=Path, default=REPO_ROOT / "queries")
    p_ask.add_argument("--model", default="alias-code")
    p_ask.add_argument("--max-repairs", type=int, default=2)
    p_ask.add_argument("--no-execute", action="store_true", help="Generate and save the query without running it")
    p_ask.add_argument("--timeout", type=int, default=60)
    p_ask.set_defaults(func=cmd_kg_ask)

    p_materialize = kg_sub.add_parser("materialize", help="Project canonical graph objects and links into Anytype")
    p_materialize.add_argument(
        "--profile",
        type=Path,
        default=REPO_ROOT / "ontologies" / "cosmology.yaml",
        help="Ontology profile (default: ontologies/cosmology.yaml)",
    )
    p_materialize.add_argument("--graph-dir", type=Path, default=REPO_ROOT / "graph")
    p_materialize.add_argument("--dry-run", action="store_true", help="Report changes without writing to Anytype")
    p_materialize.add_argument("--retry-count", type=int, default=4, help="Retries per write when rate-limited")
    p_materialize.add_argument("--retry-delay-ms", type=int, default=1200, help="Base backoff delay (ms)")
    p_materialize.set_defaults(func=cmd_kg_materialize)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
