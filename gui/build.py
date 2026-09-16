#!/usr/bin/env python3
"""
Build jmcomic-downloader.exe from gui/app.py with PyInstaller.

    python gui/build.py                # one-file, windowed exe in dist/
    python gui/build.py --onedir       # faster start, a folder instead of one file
    python gui/build.py --console      # keep a console for debugging
    python gui/build.py --clean        # wipe build/ and dist/ first

The GUI imports the sibling `scripts/jmctl.py`, which PyInstaller cannot discover on
its own, so that module is passed as a hidden import. `jmcomic` bundles data files
(and curl_cffi ships native libraries), so metadata and submodules are collected too.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENTRY = PROJECT_ROOT / "gui" / "app.py"
APP_NAME = "jmcomic-downloader"


def project_relative(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def check_environment() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit(
            "PyInstaller is not installed.\n"
            f'Install it with:  "{sys.executable}" -m pip install pyinstaller'
        )

    try:
        import jmcomic  # noqa: F401
    except ImportError:
        raise SystemExit(
            "jmcomic is not installed, but the exe must bundle it.\n"
            f'Install it with:  "{sys.executable}" -m pip install "jmcomic[plugins]"'
        )

    if not (PROJECT_ROOT / "scripts" / "jmctl.py").is_file():
        raise SystemExit(f"scripts/jmctl.py missing under {PROJECT_ROOT}")


def build_args(args) -> list[str]:
    mode = "--onedir" if args.onedir else "--onefile"
    windowed = "--console" if args.console else "--windowed"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(ENTRY),
        "--name", APP_NAME,
        mode,
        windowed,
        "--noconfirm",
        "--clean",
        # Keep the build tree inside the project instead of the cwd.
        "--distpath", str(PROJECT_ROOT / "dist"),
        "--workpath", str(PROJECT_ROOT / "build"),
        "--specpath", str(PROJECT_ROOT / "build"),
        # app.py imports jmctl from ../scripts, which static analysis cannot see.
        "--paths", str(PROJECT_ROOT / "scripts"),
        "--hidden-import", "jmctl",
        # jmcomic and curl_cffi ship data files and native libs.
        "--collect-all", "jmcomic",
        "--collect-all", "curl_cffi",
        "--collect-submodules", "common",
        "--exclude-module", "numpy",
        "--exclude-module", "matplotlib",
        "--exclude-module", "pytest",
        "--exclude-module", "IPython",
    ]

    if args.icon:
        icon = Path(args.icon).expanduser().resolve()
        if not icon.is_file():
            raise SystemExit(f"icon not found: {icon}")
        cmd += ["--icon", str(icon)]

    return cmd


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--onedir", action="store_true",
                        help="Build a folder bundle instead of a single file (starts faster).")
    parser.add_argument("--console", action="store_true",
                        help="Keep a console window; useful for diagnosing a broken build.")
    parser.add_argument("--clean", action="store_true",
                        help="Delete build/ and dist/ before building.")
    parser.add_argument("--icon", default=None, help="Path to a .ico file for the exe.")
    parser.add_argument("--verify", action="store_true",
                        help="After building, run the exe's --selftest to prove it works.")
    parser.add_argument("--verify-id", default="438696",
                        help="Album id used by --verify.")
    args = parser.parse_args(argv)

    check_environment()

    if args.clean:
        for directory in (PROJECT_ROOT / "build", PROJECT_ROOT / "dist"):
            if directory.exists():
                shutil.rmtree(directory)
                print(f"removed {project_relative(directory)}")

    cmd = build_args(args)
    print("running:")
    print("  " + " ".join(cmd))
    print()

    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        print("\nBuild failed. Re-run with --console to see the runtime error.", file=sys.stderr)
        return result.returncode

    target = (PROJECT_ROOT / "dist" / APP_NAME / f"{APP_NAME}.exe") if args.onedir \
        else (PROJECT_ROOT / "dist" / f"{APP_NAME}.exe")
    if target.is_file():
        size_mb = target.stat().st_size / (1024 * 1024)
        print(f"\nBuilt: {project_relative(target)}  ({size_mb:.1f} MB)")
        print("Double-click it to open the download window.")
    else:
        print(f"\nPyInstaller reported success but {target} is missing.", file=sys.stderr)
        return 1

    if args.verify:
        if not args.console:
            print("\n--verify on a --windowed build cannot show output; rebuild with "
                  "--console if this fails.", file=sys.stderr)
        print(f"\nRunning selftest ({args.verify_id})…")
        check = subprocess.run([str(target), "--selftest", args.verify_id], cwd=str(PROJECT_ROOT))
        if check.returncode != 0:
            print("Selftest FAILED.", file=sys.stderr)
            return check.returncode

    return 0


if __name__ == "__main__":
    sys.exit(main())
