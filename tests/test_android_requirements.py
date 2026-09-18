#!/usr/bin/env python3
"""
Guard against the Android dependency trap that broke the APK build.

Background
----------
python-for-android resolves every requirement that has **no recipe** by running a pip
dry-run restricted to wheels for Android platform tags (see p4a's build.py):

    pip install <pkgs> --dry-run --break-system-packages --ignore-installed \
        --only-binary=:all: --report <f> --platform=android_24_arm64_v8a ...

`--only-binary=:all:` means wheels only. A pure-Python wheel (`*-py3-none-any.whl`)
matches any platform tag, so it resolves. A C extension that only ships
platform-specific wheels has no `android_*` wheel and no pure wheel, so the whole
dry-run fails and p4a reports just:

    [WARNING]: Auto module resolution failed: ...

That is exactly what `pyyaml` did, and it cost several 30-minute CI cycles to find.

What this test does
-------------------
It runs **the same pip command** for the packages p4a will have to resolve, so a
regression fails here in seconds with pip's actual message, e.g.:

    ERROR: Could not find a version that satisfies the requirement pyyaml
           (from versions: none)

Covered packages = buildozer.spec `requirements` + the local jmcomic recipe's `depends`,
minus everything p4a builds from a recipe (RECIPE_PROVIDED).

Run from the project root:  python tests/test_android_requirements.py
Needs network (and pip). Exits non-zero on violation.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC = PROJECT_ROOT / "buildozer.spec"
RECIPE = PROJECT_ROOT / "recipes" / "jmcomic" / "__init__.py"

# Packages p4a builds from its own recipes, so they never go through PyPI resolution.
# Keep this in sync with what buildozer.spec asks for - it is deliberately an explicit
# list rather than "every p4a recipe" so the test stays meaningful.
RECIPE_PROVIDED = {
    "python3",
    "pyjnius",       # webview bootstrap needs it; p4a has a recipe
    "pillow",
    "libwebp",       # NOT a PyPI package here: p4a's recipe, see check_webp_codec()
    "pycryptodome",
    "jmcomic",       # our own recipe in recipes/jmcomic/
}

# Pillow only compiles its WebP codec when `libwebp` is part of the p4a recipe build
# order (p4a's Pillow recipe declares it in `opt_depends` and then sets WEBP_ROOT).
# Without it, every .webp page image downloads fine and fails to decode with
# `PIL.UnidentifiedImageError`, so this is a hard requirement, not an optimisation.
REQUIRED_FOR_PILLOW = ("libwebp",)

# Tags p4a would use for `android.archs = arm64-v8a, armeabi-v7a` at ndk_api 24.
# See PyProjectRecipe.get_wheel_platform_tags().
PLATFORM_TAGS = ["android_24_arm64_v8a", "android_24_aarch64", "android_24_arm"]

# p4a passes its hostpython version; android wheels for pure packages are
# `py3-none-any`, so this only matters for packages that should not be here anyway.
PYTHON_VERSION = os.environ.get("JM_ANDROID_PYTHON_VERSION", "3.11")


def parse_requirements() -> list:
    text = SPEC.read_text(encoding="utf-8")
    match = re.search(r"^requirements\s*=\s*(.+)$", text, re.MULTILINE)
    if not match:
        raise SystemExit("could not find a `requirements =` line in buildozer.spec")
    return [item.strip() for item in match.group(1).split(",") if item.strip()]


def parse_recipe_depends() -> list:
    if not RECIPE.is_file():
        return []
    text = RECIPE.read_text(encoding="utf-8")
    match = re.search(r"^\s*depends\s*=\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
    if not match:
        return []
    return [item.strip().strip("'\"") for item in match.group(1).split(",") if item.strip()]


def check_webview_entrypoint() -> list:
    """
    The webview bootstrap must ship a `main.py` in its source directory.

    p4a does NOT enforce this for the webview bootstrap - its build script skips the
    main.py check with the comment "(webview doesn't need an entrypoint, apparently)"
    - but PythonActivity still launches `main.py` at runtime. Without the file the APK
    installs and launches fine and then shows the loading page forever, because the
    Java side loops pinging localhost:5000 that nothing ever binds.

    Returns a list of problems (empty when fine).
    """
    problems = []

    spec_text = SPEC.read_text(encoding="utf-8")
    source_match = re.search(r"^source\.dir\s*=\s*(\S+)", spec_text, re.MULTILINE)
    if not source_match:
        problems.append("buildozer.spec has no `source.dir`, so the app directory is unknown")
        return problems

    source_dir = PROJECT_ROOT / source_match.group(1).strip()
    if not source_dir.is_dir():
        problems.append(f"source.dir {source_match.group(1)!r} does not exist")
        return problems

    entry = source_dir / "main.py"
    if not entry.is_file():
        problems.append(
            f"{source_dir.name}/main.py is missing - the webview bootstrap will build "
            f"an APK whose WebView waits forever on localhost:5000")

    boot = re.search(r"^p4a\.bootstrap\s*=\s*(\S+)", spec_text, re.MULTILINE)
    if boot and boot.group(1).strip() == "webview":
        if entry.is_file():
            text = entry.read_text(encoding="utf-8")
            # The port is baked into the generated Java; they must agree.
            if "5000" not in text:
                problems.append(
                    "webui/main.py does not reference port 5000, which is the port "
                    "p4a's webview bootstrap pings by default")

    return problems


def check_webp_codec(requirements: list) -> list:
    """
    `libwebp` must stay in buildozer.spec requirements.

    It is not a PyPI package and nothing imports it, so it looks like dead weight - but
    p4a's Pillow recipe declares it in `opt_depends` and only enables the WebP codec
    when it is part of the recipe build order. JM serves its page images as .webp, so
    dropping it makes every image fail to decode AFTER a successful download:

        PIL.UnidentifiedImageError: cannot identify image file <_io.BytesIO ...>

    Returns a list of problems (empty when fine).
    """
    missing = [name for name in REQUIRED_FOR_PILLOW if name not in requirements]
    if not missing:
        return []
    return [
        "buildozer.spec `requirements` is missing " + ", ".join(missing) +
        " - Pillow would then be compiled without its WebP codec and every .webp "
        "page image would fail with PIL.UnidentifiedImageError "
        "(see ANDROID.md and gui/verify_apk.py)"
    ]


def main() -> int:
    requirements = parse_requirements()
    depends = parse_recipe_depends()

    print(f"buildozer.spec requirements : {requirements}")
    print(f"recipes/jmcomic depends     : {depends}")

    codec_problems = check_webp_codec(requirements)
    if codec_problems:
        print()
        print("PILLOW WEBP CODEC: FAIL")
        for problem in codec_problems:
            print(f"  - {problem}")
        print()
        print("JM 的图片是 .webp，而 Pillow 只有在 p4a 的构建顺序里有 libwebp 时才会编译")
        print("WebP 编解码器：缺了它图片能下载成功但一张都解不开（UnidentifiedImageError）。")
        return 1
    print("Pillow webp codec (libwebp) : OK")

    entry_problems = check_webview_entrypoint()
    if entry_problems:
        print()
        print("WEBVIEW ENTRYPOINT: FAIL")
        for problem in entry_problems:
            print(f"  - {problem}")
        print()
        print("webview bootstrap 不会在构建时检查 main.py，但运行时 PythonActivity 会启动它；")
        print("缺少它的话 APK 能装能开，WebView 会永远停在加载页。")
        return 1
    print("webview entrypoint          : OK (main.py 存在)")

    packages = []
    for name in requirements + depends:
        if name not in packages:
            packages.append(name)

    to_resolve = [p for p in packages if p not in RECIPE_PROVIDED]
    print(f"p4a recipe-provided         : {sorted(RECIPE_PROVIDED & set(packages))}")
    print(f"must resolve from PyPI      : {to_resolve}")
    print()

    if not to_resolve:
        print("ANDROID REQUIREMENTS: PASS - nothing needs PyPI resolution")
        return 0

    with __import__("tempfile").TemporaryDirectory(prefix="jm-android-req-") as tmp:
        cmd = [
            sys.executable, "-m", "pip", "install",
            "--dry-run",
            "--ignore-installed",
            "--only-binary=:all:",
            "--disable-pip-version-check",
            "-q",
            *[f"--platform={tag}" for tag in PLATFORM_TAGS],
            "--python-version", PYTHON_VERSION,
            "--target", str(Path(tmp) / "out"),
            *to_resolve,
        ]
        print("running the same pip resolution p4a performs:")
        print("  " + " ".join(cmd))
        print()
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")

    if result.returncode == 0:
        print("ANDROID REQUIREMENTS: PASS - p4a can resolve every package")
        return 0

    print("ANDROID REQUIREMENTS: FAIL - p4a's resolution would abort the build")
    print()
    for line in (result.stdout or "").splitlines() + (result.stderr or "").splitlines():
        if "ERROR" in line or "No matching distribution" in line:
            print("  " + line.strip())
    print()
    print("失败原因：p4a 只用 wheel（--only-binary=:all:）且平台标签是 android_*，")
    print("所以上面这些包既没有纯 Python wheel、也没有 android_* wheel。")
    print()
    print("解决办法二选一：")
    print("  1. 给它写一个 recipes/<name>/ 下的 recipe（参考 recipes/jmcomic/），")
    print("     并把它加进本文件的 RECIPE_PROVIDED；")
    print("  2. 确认运行时真的不需要它，然后从 buildozer.spec 的 requirements 和")
    print("     recipes/jmcomic 的 depends 里都删掉（前提：依赖是惰性 import，")
    print("     见 ANDROID.md 的 pyyaml 案例）。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
