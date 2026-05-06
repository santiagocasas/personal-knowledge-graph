#!/usr/bin/env python3
"""Unified CLI for bookmark knowledge pipeline."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Simple unified CLI for bookmarks -> Anytype pipeline")
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

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
