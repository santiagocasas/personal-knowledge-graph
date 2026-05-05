#!/usr/bin/env python3
"""Unified CLI for bookmark knowledge pipeline."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).parent
HELPER_DIR = Path("/home/casas/Personal/anytype-agents-skill")
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


def cmd_parse(args: argparse.Namespace) -> None:
    cmd = ["uv", "run", "categorize.py", "--parse-only", "--input", str(args.input)]
    run(cmd)


def cmd_categorize(args: argparse.Namespace) -> None:
    cmd = ["uv", "run", "categorize.py", "--skip-parse"]
    if args.resume:
        cmd.append("--resume")
    if args.batch_size:
        cmd.extend(["--batch-size", str(args.batch_size)])
    run(cmd)


def cmd_enrich(args: argparse.Namespace) -> None:
    fetch_cmd = ["uv", "run", "fetch_markdown.py", "--resume", "--workers", str(args.workers)]
    if args.limit:
        fetch_cmd.extend(["--limit", str(args.limit)])
    run(fetch_cmd)

    enrich_cmd = ["uv", "run", "enrich.py", "--resume"]
    if args.limit:
        enrich_cmd.extend(["--limit", str(args.limit)])
    run(enrich_cmd)

    synth_cmd = ["uv", "run", "synthesize.py", "--min-items", str(args.min_items)]
    run(synth_cmd)


def cmd_sync(args: argparse.Namespace) -> None:
    import_cmd = ["uv", "run", "import.py"]
    if args.report:
        import_cmd.append("--report")
    if args.yes:
        import_cmd.append("--yes")
    if args.dry_run:
        import_cmd.append("--dry-run")
    if args.limit:
        import_cmd.extend(["--limit", str(args.limit)])
    run(import_cmd)

    if args.with_pages:
        runtime = runtime_path()
        page_cmd = [
            runtime,
            "-e",
            ".env",
            "-m",
            str(HELPER_DIR),
            "sync_synthesis_anytype.js",
            "input=@data/topic_guides.json",
            f"dryRun={'true' if args.dry_run else 'false'}",
            "withObjectLinks=true",
        ]
        if args.limit:
            page_cmd.append(f"limit={args.limit}")
        run(page_cmd)


def cmd_verify(args: argparse.Namespace) -> None:
    verify_cmd = ["uv", "run", "import.py", "--verify"]
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
        "sync_synthesis_anytype.js",
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

    p_enrich = sub.add_parser("enrich", help="Fetch markdown, enrich, and generate topic guides")
    p_enrich.add_argument("--workers", type=int, default=12)
    p_enrich.add_argument("--min-items", type=int, default=2)
    p_enrich.add_argument("--limit", type=int, default=0)
    p_enrich.set_defaults(func=cmd_enrich)

    p_sync = sub.add_parser("sync", help="Sync bookmarks, optionally also topic pages")
    p_sync.add_argument("--with-pages", action="store_true")
    p_sync.add_argument("--report", action="store_true")
    p_sync.add_argument("--yes", action="store_true")
    p_sync.add_argument("--dry-run", action="store_true")
    p_sync.add_argument("--limit", type=int, default=0)
    p_sync.set_defaults(func=cmd_sync)

    p_verify = sub.add_parser("verify", help="Verify data vs Anytype status")
    p_verify.add_argument("--limit", type=int, default=0)
    p_verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
