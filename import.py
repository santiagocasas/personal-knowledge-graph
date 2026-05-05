#!/usr/bin/env python3
"""Run the helper-backed Anytype bookmark import."""

import argparse
import json
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
    print("ERROR: anytype-agent-runtime is not installed or not on PATH.", file=sys.stderr)
    print("Install it with: go install github.com/anyproto/anytype-agent-runtime@latest", file=sys.stderr)
    sys.exit(1)


def run_runtime(args: argparse.Namespace, mode: str) -> dict:
    categorized = REPO_ROOT / "data" / "categorized.json"
    if not categorized.exists():
        print(f"ERROR: {categorized} not found. Run 'uv run categorize.py' first.", file=sys.stderr)
        sys.exit(1)

    cmd = [
        runtime_path(),
        "-e",
        str(REPO_ROOT / ".env"),
        "-m",
        str(HELPER_DIR),
        str(REPO_ROOT / "import_anytype.js"),
        f"input=@{categorized}",
        f"mode={mode}",
    ]
    if args.limit:
        cmd.append(f"limit={args.limit}")
    if args.verbose:
        cmd.append("verbose=true")
    if args.verify and mode == "sync":
        cmd.append("verify=true")
    if args.dry_run:
        cmd.append("dryRun=true")

    completed = subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        sys.exit(completed.returncode)

    for line in completed.stdout.splitlines():
        if line.startswith("res: "):
            raw = line[5:]
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                if raw.startswith('"') and raw.endswith('"'):
                    return json.loads(json.loads(raw))
                raise
    return {}


def print_report(result: dict) -> None:
    report = result.get("report", {})
    print("\nPre-sync report:")
    print(f"  Existing bookmarks       : {result.get('existing_bookmarks', 0)}")
    print(f"  To create                : {report.get('create', 0)}")
    print(f"  To update                : {report.get('update', 0)}")
    print(f"  Unchanged                : {report.get('unchanged', 0)}")
    print(f"  To skip                  : {report.get('skip', 0)}")
    print(f"  Duplicate URLs in Anytype: {report.get('duplicate_urls_in_anytype', 0)}")
    print(f"  Duplicate objects cleanup: {report.get('duplicate_objects_to_cleanup', 0)}")
    reasons = report.get("change_reasons") or {}
    if reasons:
        joined = ", ".join(f"{key}={value}" for key, value in sorted(reasons.items()))
        print(f"  Update reasons           : {joined}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync categorized bookmarks into Anytype via anytypeHelper.js")
    parser.add_argument("--report", action="store_true", help="Show pre-sync diff before syncing")
    parser.add_argument("--verify", action="store_true", help="Verify only, unless used with sync/report")
    parser.add_argument("--dry-run", action="store_true", help="Report only, no changes")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--limit", type=int, default=0, help="Process only first N bookmarks")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.verify and not args.report and not args.dry_run:
        result = run_runtime(args, "verify")
        print("\nVerification:")
        verify = result.get("verify", {})
        print(f"  Found   : {verify.get('found', 0)} / {result.get('loaded', 0)}")
        print(f"  Missing : {verify.get('missing', 0)}")
        return

    if args.report or args.dry_run:
        result = run_runtime(args, "report")
        print_report(result)
        if args.dry_run:
            print("\n[DRY RUN] No changes made.")
            return
        if not args.yes:
            try:
                answer = input("\nProceed with sync? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                return
            if answer != "y":
                print("Aborted.")
                return

    result = run_runtime(args, "sync")
    sync = result.get("sync", {})
    print("\nSync complete:")
    print(f"  Created : {sync.get('created', 0)}")
    print(f"  Updated : {sync.get('updated', 0)}")
    print(f"  Unchanged : {sync.get('unchanged', 0)}")
    print(f"  Skipped : {sync.get('skipped', 0)}")
    print(f"  Errors  : {sync.get('errors', 0)}")
    if "verify" in result:
        verify = result["verify"]
        print("\nVerification:")
        print(f"  Found   : {verify.get('found', 0)} / {result.get('loaded', 0)}")
        print(f"  Missing : {verify.get('missing', 0)}")


if __name__ == "__main__":
    main()
