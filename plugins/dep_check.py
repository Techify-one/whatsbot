"""Plugin dependency inspection — close the "forgot to declare a dep" gap.

A plugin declares its third-party pip packages in ``plugin.yaml`` under
``dependencies``. When an author forgets, importing the plugin blows up with a
cryptic ``ModuleNotFoundError: No module named 'google'`` and the only fix used
to be editing the manifest by hand on every install.

This module removes that manual step in two complementary ways:

* **Runtime self-heal** (:func:`resolve_runtime_deps`) — used by the loader.
  After the declared deps are installed, it scans the plugin's source for
  third-party imports that are *still not importable* and resolves each to a pip
  package (curated map for the cases where the import name differs from the
  package name, e.g. ``google`` -> ``google-auth``; otherwise the import root is
  assumed to be the package name). The loader installs the result through the
  existing :mod:`plugins.pkg_deps` choke point (same security gate + cache), so
  a plugin with an undeclared dependency loads instead of failing.

* **Author/CI validator** (:func:`undeclared_dependencies`) — declaration-based
  (does NOT require the package to be installed). Lists imports that aren't
  covered by the manifest's ``dependencies`` so ``/new-plugin`` and the plugin
  store's CI can catch the mistake *before* publishing.

Resolution is best-effort: the curated map handles the well-known import/package
mismatches; everything else assumes ``import foo`` -> ``pip install foo`` (true
for the large majority — httpx, requests, numpy, openai, ...). When a guess is
wrong, the install fails and the loader surfaces a clear, actionable error
naming the module — never the raw ``ModuleNotFoundError`` again.
"""

from __future__ import annotations

import ast
import importlib.metadata as _im
import importlib.util
import logging
import sys
from functools import lru_cache
from pathlib import Path

from plugins import pkg_deps

logger = logging.getLogger(__name__)

# Import path (root or dotted prefix) -> pip package, ONLY where the two differ.
# Longest matching prefix wins, so ``google.oauth2`` -> ``google-auth`` while a
# hypothetical ``google.cloud.storage`` could map elsewhere if added later.
_PREFIX_TO_PACKAGE: dict[str, str] = {
    "google.auth": "google-auth",
    "google.oauth2": "google-auth",
    "googleapiclient": "google-api-python-client",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "bs4": "beautifulsoup4",
    "yaml": "pyyaml",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "sklearn": "scikit-learn",
    "jwt": "pyjwt",
    "Crypto": "pycryptodome",
    "Cryptodome": "pycryptodomex",
    "git": "GitPython",
    "serial": "pyserial",
    "docx": "python-docx",
    "fitz": "PyMuPDF",
    "magic": "python-magic",
}

# Core WhatsBot-Lite top-level packages a plugin may import absolutely (never deps).
_CORE_PACKAGES = {
    "server", "agent", "config", "gowa", "db", "plugins", "main",
    "whatsbot_plugins", "tests",
}

# Import roots the host ALWAYS provides — a plugin may use them without
# declaring. Anything in ``requirements.txt`` (and its transitive closure) is
# installed in every WhatsBot-Lite environment, so it is computed dynamically at
# runtime. This curated baseline is the fallback for contexts where the host's
# package metadata isn't importable (e.g. the plugin store's CI), and covers the
# requirements.txt roots + their most-used transitive deps (notably ``pydantic``
# / ``starlette`` from FastAPI, the documented plugin settings/router stack).
_HOST_BASELINE = {
    "agno", "openai", "httpx", "fastapi", "multipart", "uvicorn", "yaml",
    "sqlalchemy", "alembic", "psycopg", "segno",
    "pydantic", "pydantic_core", "starlette", "anyio", "sniffio", "click",
    "h11", "certifi", "idna", "typing_extensions", "annotated_types",
}


def _canon(name: str) -> str:
    return name.strip().lower().replace("_", "-")


@lru_cache(maxsize=1)
def _dist_to_import_roots() -> dict[str, set[str]]:
    """Inverse of ``packages_distributions``: distribution -> {import roots}."""
    out: dict[str, set[str]] = {}
    try:
        mapping = _im.packages_distributions()  # import_name -> [dist, ...]
    except Exception:  # pragma: no cover - defensive
        return out
    for import_name, dists in mapping.items():
        for dist in dists:
            out.setdefault(_canon(dist), set()).add(import_name.split(".")[0])
    return out


def _requirements_packages() -> set[str]:
    """Top-level package names declared in the host's ``requirements.txt``."""
    req = Path(__file__).resolve().parent.parent / "requirements.txt"
    pkgs: set[str] = set()
    try:
        lines = req.read_text(encoding="utf-8").splitlines()
    except OSError:
        return pkgs
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            pkgs.add(_canon(pkg_deps.pkg_name(line)))
    return pkgs


@lru_cache(maxsize=1)
def host_provided_roots() -> frozenset[str]:
    """Import roots a plugin may use without declaring: the transitive closure of
    ``requirements.txt`` mapped to import names, unioned with the curated
    baseline (so it still works when host metadata is unavailable).
    """
    dist_imports = _dist_to_import_roots()
    closure: set[str] = set()
    stack = list(_requirements_packages())
    while stack:
        name = _canon(stack.pop())
        if name in closure:
            continue
        closure.add(name)
        try:
            reqs = _im.requires(name) or []
        except _im.PackageNotFoundError:
            continue
        for raw in reqs:
            # Skip extra-gated deps (only pulled with optional extras).
            if "extra ==" in raw:
                continue
            dep = _canon(pkg_deps.pkg_name(raw))
            if dep and dep not in closure:
                stack.append(dep)
    roots: set[str] = set(_HOST_BASELINE)
    for dist in closure:
        roots |= dist_imports.get(dist, {dist.replace("-", "_")})
    return frozenset(roots)


def _iter_py_files(plugin_dir: Path):
    for path in plugin_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def scan_imports(plugin_dir: Path) -> dict[str, set[str]]:
    """Map ``import root -> {full dotted module names}`` for every ABSOLUTE
    import in the plugin's ``.py`` files. Relative imports (``from . import x``)
    are skipped — they reference the plugin's own package, never a dependency.
    """
    found: dict[str, set[str]] = {}
    for path in _iter_py_files(plugin_dir):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as e:
            logger.debug("dep_check: skipping unparseable %s (%s)", path, e)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    found.setdefault(root, set()).add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import -> plugin's own code
                    continue
                if not node.module:
                    continue
                root = node.module.split(".")[0]
                found.setdefault(root, set()).add(node.module)
    return found


def _local_module_names(plugin_dir: Path) -> set[str]:
    """Top-level module/package names defined inside the plugin itself."""
    names: set[str] = set()
    for path in plugin_dir.iterdir():
        if path.is_dir() and (path / "__init__.py").exists():
            names.add(path.name)
        elif path.suffix == ".py":
            names.add(path.stem)
    return names


def third_party_roots(plugin_dir: Path) -> dict[str, set[str]]:
    """:func:`scan_imports` reduced to genuine third-party roots — excludes the
    stdlib, core WhatsBot-Lite packages and the plugin's own modules.
    """
    stdlib = set(sys.stdlib_module_names)
    local = _local_module_names(plugin_dir)
    host = host_provided_roots()
    out: dict[str, set[str]] = {}
    for root, mods in scan_imports(plugin_dir).items():
        if root in stdlib or root in _CORE_PACKAGES or root in local or root in host:
            continue
        out[root] = mods
    return out


def resolve_package(root: str, full_modules: set[str]) -> str:
    """Best-effort import-name -> pip package. Longest curated prefix match wins;
    otherwise the import root is assumed to be the package name.
    """
    best: tuple[int, str] | None = None
    for mod in full_modules:
        for prefix, pkg in _PREFIX_TO_PACKAGE.items():
            if mod == prefix or mod.startswith(prefix + "."):
                if best is None or len(prefix) > best[0]:
                    best = (len(prefix), pkg)
    if best is not None:
        return best[1]
    return _PREFIX_TO_PACKAGE.get(root, root)


def resolve_runtime_deps(plugin_dir: Path) -> list[str]:
    """Pip packages for third-party imports that are NOT importable right now.

    Call AFTER the declared deps are installed, so anything already satisfied is
    excluded. Returns a de-duplicated list of pip package names to install.
    """
    pending: list[str] = []
    seen: set[str] = set()
    for root, mods in third_party_roots(plugin_dir).items():
        try:
            spec = importlib.util.find_spec(root)
        except (ImportError, ValueError, ModuleNotFoundError):
            spec = None
        if spec is not None:
            continue  # already importable -> satisfied
        pkg = resolve_package(root, mods)
        key = pkg_deps.pkg_name(pkg).lower()
        if key in seen:
            continue
        seen.add(key)
        pending.append(pkg)
        logger.warning(
            "Plugin dir %s imports '%s' but it is not declared/installed; "
            "auto-resolving to pip package '%s'", plugin_dir.name, root, pkg,
        )
    return pending


def undeclared_dependencies(plugin_dir: Path, declared: list[str]) -> list[tuple[str, str]]:
    """Declaration-based check for authors/CI (does not need installs).

    Returns ``(import_root, suggested_package)`` for every third-party import not
    covered by ``declared`` (the manifest's ``dependencies``). Coverage = the
    resolved package name (or the bare import root) matches a declared package.
    """
    declared_pkgs = {pkg_deps.pkg_name(d).lower() for d in (declared or [])}
    out: list[tuple[str, str]] = []
    for root, mods in sorted(third_party_roots(plugin_dir).items()):
        pkg = resolve_package(root, mods)
        if pkg_deps.pkg_name(pkg).lower() in declared_pkgs:
            continue
        if root.lower() in declared_pkgs:
            continue
        out.append((root, pkg))
    return out


def _main(argv: list[str]) -> int:
    """``python -m plugins.dep_check <plugin_dir> [<plugin_dir> ...]``

    Validator entry point for authoring/CI: prints the imports each plugin uses
    but does not declare, and exits non-zero if any are found.
    """
    import yaml  # local import: only the CLI path needs it

    if not argv:
        print("usage: python -m plugins.dep_check <plugin_dir> [...]")
        return 2
    problems = 0
    for arg in argv:
        plugin_dir = Path(arg)
        manifest = plugin_dir / "plugin.yaml"
        declared: list[str] = []
        if manifest.is_file():
            data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
            declared = list(data.get("dependencies") or [])
        missing = undeclared_dependencies(plugin_dir, declared)
        if missing:
            problems += 1
            print(f"✗ {plugin_dir.name}: dependências usadas mas NÃO declaradas:")
            for root, pkg in missing:
                print(f"    import '{root}'  →  declare '{pkg}' em dependencies")
        else:
            print(f"✓ {plugin_dir.name}: todas as dependências declaradas")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
