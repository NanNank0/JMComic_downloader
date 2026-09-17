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

version = 1.0.0

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
requirements = python3,pyjnius,requests,commonx,pillow,pycryptodome,pyyaml,jmcomic

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

[buildozer]
log_level = 2
warn_on_root = 1
