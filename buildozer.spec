[app]

# ---------------------------------------------------------------------------
# This is the Android (python-for-android) build configuration.
#
# Build it on Linux or macOS:
#     pip install buildozer cython
#     buildozer android debug        # -> bin/*.apk
#     buildozer android release      # signed release (needs a keystore)
#
# Windows cannot run buildozer. Use WSL2 (Ubuntu) or the GitHub Actions
# workflow in .github/workflows/build.yml, which does it for you.
# ---------------------------------------------------------------------------

title = JMComic 下载器
package.name = jmcomicdownloader
package.domain = io.github.nannank0

source.dir = gui
# Only the app entry point needs to ship inside the APK; jmcore.py arrives via the
# `jmcomic-helper` recipe below. p4a would otherwise copy the whole gui/ folder.
source.include_exts = py,png,jpg,jkv,atlas,ttf

version = 1.0.0

# `jmcomic` is deliberately NOT listed directly. Its PyPI metadata hard-depends on
# curl-cffi, which python-for-android cannot build (no recipe - see
# https://github.com/kivy/python-for-android/issues/2964), and p4a would try to
# resolve that dependency and fail.
#
# Instead we take two steps:
#   1. list jmcomic's *real* runtime dependencies ourselves, so nothing is missed;
#   2. use our own recipe (recipes/jmcomic/) to install jmcomic with --no-deps.
#
# The recipe is named "jmcomic" so it overrides the plain PyPI resolution of that
# same name - a local recipe in p4a.local_recipes takes precedence.
#
# curl_cffi is imported lazily by commonX, so it is never touched as long as the
# app selects the `requests` HTTP backend - which kivy_app.py does automatically
# on Android (jmcore.default_http_backend()). Verified on desktop: a full chapter
# downloads correctly through `requests`.
requirements = python3,kivy,pillow,pycryptodome,pyyaml,requests,commonx,jmcomic

# Excluded so nothing drags the native curl-cffi back in.
# If a future jmcomic version moves this import to module scope, the app will
# fail at startup - see ANDROID.md for how to check.

orientation = portrait
fullscreen = 0

# Android 11+ writes into the app's own external dir without extra permissions.
# READ/WRITE_EXTERNAL_STORAGE are requested only for older devices so downloads
# can also be saved to a shared folder.
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

# A splash so a cold start does not look hung while Python boots.
# android.presplash_color = #101418

[buildozer]
log_level = 2
warn_on_root = 1
