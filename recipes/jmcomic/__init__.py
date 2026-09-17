"""
python-for-android recipe for jmcomic, installed WITHOUT its dependency metadata.

Why this exists
---------------
`jmcomic`'s PyPI metadata declares `curl-cffi` as a hard requirement. curl-cffi is
native code and python-for-android has no recipe for it, so the default install path
fails the whole build:

    pip install jmcomic  ->  tries to build curl-cffi  ->  no recipe  ->  ERROR

curl-cffi only provides browser TLS-fingerprint impersonation. The JM endpoints do
not require it - verified on desktop by downloading a full chapter through the
pure-Python `requests` backend - and commonX imports curl_cffi lazily, so it is
never touched as long as `requests` is selected. `jmcore.default_http_backend()`
does exactly that on Android.

So this recipe installs jmcomic with `--no-deps` and lets buildozer.spec's
`requirements` line supply the real dependencies (commonx, pillow, pycryptodome,
pyyaml, requests) as normal recipes - each of which HAS a p4a recipe for the native
parts (pillow, pycryptodome, cffi) and installs from PyPI for the pure-Python ones.

Verified: the recipe class API below (`PythonRecipe`, `_host_recipe.pip`,
`ctx.get_python_install_dir`) matches python-for-android's recipe.py on develop.
NOT verified: an actual APK build - this project has no Linux/macOS host available.
See ANDROID.md.
"""

from pythonforandroid.recipe import PythonRecipe
from pythonforandroid.logger import info, shprint


class JmcomicRecipe(PythonRecipe):
    version = None  # None -> resolve the newest release from PyPI
    url = None
    site_packages_name = "jmcomic"
    depends = ["python3", "commonx", "pillow", "pycryptodome", "pyyaml", "requests"]

    # jmcomic is pure Python, so installing it for the host first is unnecessary
    # and only risks pulling curl-cffi in on the host side.
    call_hostpython_via_targetpython = False

    def get_recipe_env(self, arch=None, with_flags_in_cc=True):
        env = super().get_recipe_env(arch, with_flags_in_cc)
        # Be explicit that curl-cffi must never be resolved.
        env["PIP_NO_DEPS"] = "1"
        return env

    def install_python_package(self, arch, name=None, env=None, is_dir=True):
        """
        Install jmcomic straight from the index with --no-deps.

        The stock implementation runs `pip install .` inside the build dir, which
        resolves metadata and drags curl-cffi in. We bypass that entirely.
        """
        env = env or self.get_recipe_env(arch)
        info("Installing jmcomic into site-packages with --no-deps")

        spec = "jmcomic" if self.version is None else f"jmcomic=={self.version}"
        shprint(
            self._host_recipe.pip,
            "install",
            spec,
            "--no-deps",
            "--compile",
            "--target",
            self.ctx.get_python_install_dir(arch.arch),
            _env=env,
        )

    def build_arch(self, arch):
        # Nothing to compile; skip Recipe.build_arch (which expects a source tree).
        self.install_python_package(arch)


recipe = JmcomicRecipe()
