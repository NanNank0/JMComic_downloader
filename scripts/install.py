#!/usr/bin/env python3
"""
Install the jmcomic skill into the skill roots that agent harnesses scan.

Harnesses disagree about where skills live, so this writes the same bundle into
each requested root:

    ~/.agents/skills   both DSH and Codex scan this (the shared, recommended target)
    ~/.dsh/skills      DSH-specific root
    ~/.codex/skills    Codex-specific root
    ~/.claude/skills   Claude Code

Usage:
    python install.py                       # install into ~/.agents/skills
    python install.py --target agents dsh   # install into several roots
    python install.py --target all
    python install.py --list                # show resolved roots and current state
    python install.py --uninstall           # remove from every resolved root
    python install.py --dry-run             # report actions without touching disk

Re-running is safe: an existing install is replaced when it differs.
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

SKILL_NAME = "jmcomic"
# This file lives in <skill>/scripts/, so the bundle root is its parent.
SOURCE_DIR = Path(__file__).resolve().parent.parent

# Files that belong in a shipped skill bundle.
INCLUDE = ("SKILL.md", "scripts", "assets", "README.md")

ROOTS = {
    "agents": lambda home: home / ".agents" / "skills",
    "dsh": lambda home: home / ".dsh" / "skills",
    "codex": lambda home: home / ".codex" / "skills",
    "claude": lambda home: home / ".claude" / "skills",
}

DEFAULT_TARGETS = ["agents"]


def home_dir() -> Path:
    return Path.home()


def resolve_roots(names) -> dict:
    home = home_dir()
    resolved = {}
    for name in names:
        if name == "all":
            resolved.update({k: v(home) for k, v in ROOTS.items()})
        elif name in ROOTS:
            resolved[name] = ROOTS[name](home)
        else:
            raise SystemExit(f"unknown target {name!r}; choose from {', '.join(ROOTS)} or 'all'")
    return resolved


def iter_sources():
    for item in INCLUDE:
        path = SOURCE_DIR / item
        if path.exists():
            yield path


def install_one(root: Path, dry_run: bool) -> str:
    destination = root / SKILL_NAME
    if destination.exists():
        if same_tree(SOURCE_DIR, destination):
            return f"up to date    {destination}"
        action = "updated"
    else:
        action = "installed"

    if dry_run:
        return f"would be {action:<9} {destination}"

    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for source in iter_sources():
        target = destination / source.name
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(source, target)
    return f"{action:<13} {destination}"


def same_tree(left: Path, right: Path) -> bool:
    """Compare only the files this installer ships."""
    for source in iter_sources():
        target = right / source.name
        if source.is_file():
            if not target.is_file() or not filecmp.cmp(source, target, shallow=False):
                return False
        else:
            for child in source.rglob("*"):
                if not child.is_file() or "__pycache__" in child.parts:
                    continue
                mirror = target / child.relative_to(source)
                if not mirror.is_file() or not filecmp.cmp(child, mirror, shallow=False):
                    return False
    return True


def uninstall_one(root: Path, dry_run: bool) -> str:
    destination = root / SKILL_NAME
    if not destination.exists():
        return f"not present   {destination}"
    if dry_run:
        return f"would remove  {destination}"
    shutil.rmtree(destination)
    return f"removed       {destination}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", nargs="+", default=DEFAULT_TARGETS,
                        choices=sorted(ROOTS) + ["all"],
                        help="Which skill roots to install into (default: agents).")
    parser.add_argument("--list", action="store_true", help="Report roots and current state.")
    parser.add_argument("--uninstall", action="store_true", help="Remove the skill instead.")
    parser.add_argument("--dry-run", action="store_true", help="Report without touching disk.")
    args = parser.parse_args(argv)

    if not (SOURCE_DIR / "SKILL.md").is_file():
        raise SystemExit(f"SKILL.md not found beside {__file__}; run this from the skill directory.")

    roots = resolve_roots(args.target)

    if args.list:
        print(f"source: {SOURCE_DIR}")
        for name, root in resolve_roots(list(ROOTS)).items():
            destination = root / SKILL_NAME
            state = "installed" if destination.exists() else "-"
            scanned = "scanned by default" if name in roots else ""
            print(f"  {name:<7} {state:<10} {destination}  {scanned}")
        return 0

    verb = uninstall_one if args.uninstall else install_one
    failures = 0
    for name, root in roots.items():
        try:
            root.mkdir(parents=True, exist_ok=True)
            print(f"[{name}] {verb(root, args.dry_run)}")
        except OSError as e:
            failures += 1
            print(f"[{name}] FAILED: {e}", file=sys.stderr)

    if not args.uninstall and not args.dry_run and not failures:
        print()
        print("Start a new agent session (or refresh the skill catalog) so the skill is discovered.")
        print(f"Verify with: python {SOURCE_DIR / 'scripts' / 'jmctl.py'} doctor")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
