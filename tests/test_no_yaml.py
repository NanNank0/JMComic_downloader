#!/usr/bin/env python3
"""
Prove that the app works with PyYAML UNAVAILABLE.

Android cannot install pyyaml (C extension, no pure/Android wheel, no p4a recipe), so
it is omitted from buildozer.spec. That is only safe because every `import yaml` in the
dependency tree is lazy and on code paths this app does not use:

    common/base/packer.py  -> inside YmlPacker methods   (YAML option files)
    jmcomic/jm_option.py   -> inside a legacy zip-level migration advice function

This test makes that claim falsifiable: it installs an import hook that raises
ImportError for `yaml`, then exercises what the Android app actually does - load the
engine, answer the web UI's config request, and run a real download.

Run from the project root:  python tests/test_no_yaml.py
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "webui"))

BLOCKED = ("yaml",)  # PyYAML; `ruamel.yaml` is unaffected


class BlockYaml(importlib.abc.MetaPathFinder):
    """Refuse to import PyYAML, simulating the Android environment."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "yaml" or fullname.startswith("yaml."):
            raise ImportError(f"blocked for this test: {fullname} (PyYAML unavailable)")
        return None


def main() -> int:
    sys.meta_path.insert(0, BlockYaml())

    # Sanity: the block is real.
    try:
        import yaml  # noqa: F401
        print("FAIL: the yaml block is not working; test would be meaningless")
        return 1
    except ImportError:
        print("yaml is blocked (simulating Android)")

    import jmcore  # noqa: E402  (must import after the hook is installed)

    print(f"jmcore imported; android={jmcore.is_android()} "
          f"backend={jmcore.default_http_backend()}")

    jmcomic = jmcore.load_jmcomic()
    print(f"jmcomic {jmcomic.__version__} imported without yaml")

    # What the web UI asks for at startup.
    import server  # noqa: E402
    cfg = server._config_payload()
    print(f"web UI config payload OK: backend={cfg['backend']} "
          f"saveDir set={bool(cfg['saveDir'])}")

    # Things the CLI/desktop can do that Android must NOT need:
    yaml_dependent = []
    try:
        jmcore.export_default_option_yaml(jmcomic)
        yaml_dependent.append("export_default_option_yaml worked (unexpected)")
    except ImportError as exc:
        yaml_dependent.append(f"export_default_option_yaml needs yaml ({exc})")
    except Exception as exc:
        yaml_dependent.append(f"export_default_option_yaml failed: {type(exc).__name__}")
    print("  (known YAML-only API) " + yaml_dependent[0])

    # The real proof: an actual download through the same engine the Android UI uses.
    with tempfile.TemporaryDirectory(prefix="jm-noyaml-") as tmp:
        settings = jmcore.DownloadSettings(
            targets=["438696"],
            kind="photo",
            exports=[],
            save_dir=tmp,
            threads=4,
            http_backend=jmcore.default_http_backend(),
        )
        try:
            payload = jmcore.run_download(settings, jmcomic=jmcomic)
        except Exception as exc:
            print(f"FAIL: download without yaml: {type(exc).__name__}: {exc}")
            return 1

        files = [p for p in Path(tmp).rglob("*") if p.is_file()]
        print(f"download OK: images={payload.get('imageCount')} files={len(files)}")

        if not payload.get("imageCount"):
            print("FAIL: nothing downloaded")
            return 1

    print()
    print("NO-YAML TEST: PASS — Android 省略 pyyaml 是安全的")
    return 0


if __name__ == "__main__":
    sys.exit(main())
