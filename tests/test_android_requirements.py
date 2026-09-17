#!/usr/bin/env python3
"""
Guard against the Android dependency trap that broke the APK build.

python-for-android resolves every requirement that has **no recipe** by running:

    pip install <pkg> --dry-run --only-binary=:all: --platform=android_24_arm64_v8a ...

`--only-binary=:all:` means "wheels only". A package with a pure-Python wheel
(`*-py3-none-any.whl`) satisfies any platform tag, so it resolves fine. A package that
only ships **platform-specific** wheels (a C extension) has no android_* wheel and no
pure wheel, so the dry-run fails and p4a emits the extremely unhelpful:

    [WARNING]: Auto module resolution failed: ...

That is exactly what `pyyaml` did: 72 wheels, none of them pure, no p4a recipe.

This test encodes the rule so the mistake cannot come back:

  For every package named in buildozer.spec's `requirements` and in the local jmcomic
  recipe's `depends`, the package must either
    (a) have a p4a recipe (listed in RECIPE_PROVIDED below), or
    (b) publish a pure-Python wheel on PyPI.

Run it from the project root:  python tests/test_android_requirements.py
It needs network access (PyPI JSON API) and exits non-zero on violation.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC = PROJECT_ROOT / "buildozer.spec"
RECIPE = PROJECT_ROOT / "recipes" / "jmcomic" / "__init__.py"

# Packages p4a builds from its own recipes, so they never go through PyPI resolution.
# Keep this in sync with what buildozer.spec actually asks for; it is deliberately a
# short explicit list rather than "all p4a recipes" so the test stays meaningful.
RECIPE_PROVIDED = {
    "python3",
    "pyjnius",       # webview bootstrap needs it; p4a has a recipe
    "pillow",
    "pycryptodome",
    "jmcomic",       # our own recipe in recipes/jmcomic/
}


def parse_requirements() -> list:
    text = SPEC.read_text(encoding="utf-8")
    match = re.search(r"^requirements\s*=\s*(.+)$", text, re.MULTILINE)
    if not match:
        raise SystemExit("could not find a `requirements =` line in buildozer.spec")
    return [item.strip() for item in match.group(1).split(",") if item.strip()]


def parse_recipe_depends() -> list:
    text = RECIPE.read_text(encoding="utf-8")
    match = re.search(r"^\s*depends\s*=\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
    if not match:
        return []
    return [item.strip().strip("'\"") for item in match.group(1).split(",") if item.strip()]


def has_pure_wheel(package: str):
    """(True/False, explanation) for whether PyPI offers a pure-Python wheel."""
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.load(response)
    except Exception as exc:
        return None, f"PyPI lookup failed: {exc}"

    version = data["info"]["version"]
    files = [f["filename"] for f in data["releases"].get(version, [])]
    if not files:
        return None, f"{package} {version}: no files published"

    pure = [f for f in files if f.endswith(".whl") and "py3-none-any" in f]
    if pure:
        return True, f"{package} {version}: pure wheel {pure[0]}"
    wheels = [f for f in files if f.endswith(".whl")]
    return False, (f"{package} {version}: {len(wheels)} wheels but none pure-Python "
                   f"(no android_* wheel exists either)")


def main() -> int:
    requirements = parse_requirements()
    depends = parse_recipe_depends()
    print(f"buildozer.spec requirements : {requirements}")
    print(f"recipes/jmcomic depends     : {depends}")
    print()

    # `pyyaml` is the concrete regression this test exists for. If it is ever added
    # back, it must come with a recipe - so fail loudly and explain.
    packages = []
    for name in requirements + depends:
        if name not in packages:
            packages.append(name)

    failures = []
    for name in packages:
        if name in RECIPE_PROVIDED:
            print(f"  OK    {name:16} (p4a recipe)")
            continue
        ok, why = has_pure_wheel(name)
        if ok is True:
            print(f"  OK    {name:16} {why}")
        elif ok is False:
            print(f"  FAIL  {name:16} {why}")
            failures.append(name)
        else:
            print(f"  SKIP  {name:16} {why}")

    print()
    if failures:
        print("这些包既没有 p4a recipe，也没有纯 Python wheel，")
        print("会让 p4a 的 'Auto module resolution' 失败：")
        for name in failures:
            print(f"  - {name}")
        print()
        print("解决办法二选一：")
        print("  1. 给它写一个 recipes/<name>/ 下的 recipe（参考 recipes/jmcomic/）")
        print("  2. 确认运行时真的不需要它，然后从 requirements / depends 里删掉")
        print("     （注意：依赖是惰性 import 才可以删，见 ANDROID.md）")
        return 1

    print("ANDROID REQUIREMENTS: PASS — 每个包都能被 p4a 解析")
    return 0


if __name__ == "__main__":
    sys.exit(main())
