[app]

# ---------------------------------------------------------------------------
# Android (python-for-android) build configuration.
#
# Build it on Linux or macOS:
#     pip install buildozer cython==0.29.36
#     python gui/build_android.py debug      # -> bin/*.apk
#
# Windows cannot run buildozer. Use WSL2 (Ubuntu) or the GitHub Actions workflow
# in .github/workflows/android.yml, which does it for you.
# ---------------------------------------------------------------------------

title = JMComic 下载器
package.name = jmcomicdownloader
package.domain = io.github.nannank0

# server.py imports `ui` and `jmcore`; build_android.py stages jmcore into webui/.
source.dir = webui
source.include_exts = py,png,jpg,ttf

version = 1.1.0

# THE KEY SETTING: no Kivy.
#
# The UI is a local web page shown in an Android WebView, so the app needs no GUI
# toolkit at all. That removes the whole native graphics stack (Kivy + SDL2) which is
# what made desktop freezing fragile, and it removes Kivy's collect_submodules()
# analysis step from the build.
#
# `jmcomic` is deliberately NOT listed directly. Its PyPI metadata hard-depends on
# curl-cffi, which python-for-android cannot build (no recipe - see
# https://github.com/kivy/python-for-android/issues/2964), and p4a would try to
# resolve that dependency and fail.
#
# Instead we take two steps:
#   1. list jmcomic's *real* runtime dependencies ourselves, so nothing is missed;
#   2. use our own recipe (recipes/jmcomic/) to install jmcomic with --no-deps.
#
# curl_cffi is imported lazily by commonX, so it is never touched as long as the app
# selects the `requests` HTTP backend - which it does automatically on Android
# (jmcore.default_http_backend()). Verified on desktop: a full chapter downloads
# correctly through `requests`.
#
# pyjnius is required by the webview bootstrap's Java layer (PythonActivity).
#
# pyyaml is deliberately ABSENT. It is a C extension: 72 wheels on PyPI, none of them
# pure-Python, and no android_* wheel, so p4a's resolver (`--only-binary=:all:`) cannot
# satisfy it and aborts the whole build with the very unhelpful
# "[WARNING]: Auto module resolution failed". p4a has no pyyaml recipe either.
#
# Dropping it is safe here because PyYAML is only ever imported lazily, and only on
# code paths this app never takes (verified by reading the sources):
#   common/base/packer.py  -> inside YmlPacker methods (YAML option files)
#   jmcomic/jm_option.py   -> inside a legacy `zip: level:` migration advice function
# The Android UI builds its option from JmOption.default() and never loads a YAML file.
# `python gui/build_android.py` and tests/test_android_requirements.py guard this rule.
# If YAML ever becomes genuinely required on Android, add a recipes/pyyaml/ that builds
# PyYAML's pure-Python fallback (its setup.py skips the C extension when libyaml is
# absent), or switch the call sites to ruamel.yaml, for which p4a HAS a recipe.
#
# libwebp is NOT optional here, despite the name: it is what makes Pillow's WebP
# codec exist on Android, and JM serves its page images as .webp.
#
# v1.3.7 shipped without it. The APK contained PIL/WebPImagePlugin.pyc (pure Python,
# always present) but no PIL/_webp.so, so Pillow could neither decode nor encode WebP
# and EVERY image failed after a successful HTTP download:
#
#     PIL.UnidentifiedImageError: cannot identify image file <_io.BytesIO object ...>
#     -> PartialDownloadFailedException: 部分下载失败 共50个图片下载失败
#
# while the same album downloaded fine on Windows (whose Pillow wheel bundles WebP).
# Verified against the artifact: `tar -tzf` of lib/arm64-v8a/libpybundle.so lists
# PIL/_imaging.so but no _webp*, and the APK has no libwebp.so.
#
# p4a's Pillow recipe treats webp as an OPTIONAL dependency:
#
#     opt_depends = ['libwebp']
#     if 'libwebp' in self.ctx.recipe_build_order:
#         env["WEBP_ROOT"] = "<libwebp build dir>/installation/{lib,include}"
#
# and p4a's dependency graph (pythonforandroid/graph.py) turns an opt_depend that IS
# in the requirements into a real dependency edge, so listing it here both orders
# libwebp BEFORE Pillow and enables WEBP_ROOT. That is the entire fix.
#
# Do not remove it: gui/verify_apk.py fails the build when PIL/_webp*.so is missing.
requirements = python3,libwebp,pyjnius,requests,commonx,pillow,pycryptodome,jmcomic

p4a.bootstrap = webview

orientation = portrait
fullscreen = 0

# Android 11+ writes into the app's own external dir without extra permissions.
android.permissions = INTERNET,ACCESS_NETWORK_STATE,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE

# Keeps the download alive when the screen turns off mid-transfer.
android.wakelock = True

android.api = 34
android.minapi = 24
android.ndk_api = 24
android.archs = arm64-v8a, armeabi-v7a

android.allow_backup = True
android.logcat_filters = *:S python:D

# Our recipe lives here (jmcomic with --no-deps).
p4a.local_recipes = recipes

# Use python-for-android's develop branch instead of the default 'master'.
#
# Reason: buildozer git-clones p4a into .buildozer/android/platform/python-for-android
# and runs THAT copy (not the pip-installed one). The master copy that CI had cached
# contains
#     from pip._internal.exceptions import BuildDependencyInstallError
# which modern pip no longer exports, and p4a upgrades pip inside its own build venv,
# so the build always died with:
#     ImportError: cannot import name 'BuildDependencyInstallError'
# develop no longer references that name (verified against its source).
#
# buildozer re-clones whenever the configured branch differs from the cached clone's
# branch, so this also repairs an existing stale cache. For a fully reproducible build,
# pin `p4a.commit` to a specific sha instead of tracking a branch.
p4a.branch = develop

[buildozer]
log_level = 2
warn_on_root = 1
