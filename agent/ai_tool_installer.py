"""Code-in-DB tool installer.

Materialises ``ai_tools.code`` to ``storages/ai_tools/<name>.py``, resolves the
declared dependencies (check-before-install, so pip only touches the network the
first time a spec set changes), imports the module under the namespaced package
``whatsbot_ai_tools.<name>`` (mirroring ``whatsbot_plugins.<id>``), validates the
WhatsBot tool contract (``schema dict + execute(ctx, args)``) and registers it in
the handler's tool registry.

Fail-closed: any problem (bad name, dep install failure, import error, contract
violation) marks the row ``install_status='failed'`` with the error and the tool
is NOT registered — the app still boots and the webhook keeps working.

Precedence: the installer runs AFTER core + plugin tools are registered, so the
registry's collision no-op gives code precedence over the DB (a DB tool can never
hijack a core/plugin tool name).
"""

from __future__ import annotations

import importlib.util
import logging
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from db.repositories import tool_repo

logger = logging.getLogger(__name__)

# Tool name == schema function name == usage.call_type — keep it conservative.
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
PARENT_PACKAGE = "whatsbot_ai_tools"
_PIP_TIMEOUT = 600  # seconds


# --------------------------------------------------------------------------- #
# Dependency policy
# --------------------------------------------------------------------------- #
def is_dep_allowed(pkg: str) -> bool:
    """Allowlist gate for a dependency package name.

    OPEN in the MVP (returns ``True`` for everything) but the single choke point
    is here: closing the policy later is a one-line change, no refactor.
    """
    return True


def _pkg_name(spec: str) -> str:
    """Extract the bare package name from a pip spec (``httpx>=0.27`` → ``httpx``)."""
    m = re.match(r"^[A-Za-z0-9_.\-]+", (spec or "").strip())
    return m.group(0) if m else (spec or "").strip()


# --------------------------------------------------------------------------- #
# Filesystem / import helpers
# --------------------------------------------------------------------------- #
def ai_tools_dir(data_dir) -> Path:
    d = Path(data_dir) / "storages" / "ai_tools"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_parent_package(tools_dir: Path) -> None:
    """Register ``whatsbot_ai_tools`` as a synthetic namespace package."""
    existing = sys.modules.get(PARENT_PACKAGE)
    if existing is not None:
        # Keep the search path pointed at the current tools dir.
        path = getattr(existing, "__path__", None)
        if isinstance(path, list) and str(tools_dir) not in path:
            path.append(str(tools_dir))
        return
    spec = importlib.util.spec_from_loader(PARENT_PACKAGE, loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(tools_dir)]  # type: ignore[attr-defined]
    sys.modules[PARENT_PACKAGE] = module


def _import_tool_module(name: str, path: Path) -> ModuleType:
    full = f"{PARENT_PACKAGE}.{name}"
    # Drop any stale module so an edited tool re-imports cleanly.
    sys.modules.pop(full, None)
    spec = importlib.util.spec_from_file_location(full, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build import spec for {full}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# Contract validation
# --------------------------------------------------------------------------- #
def _validate_schema(schema, expected_name: str | None) -> str:
    if not isinstance(schema, dict):
        raise ValueError("tool schema must be a dict")
    fn = schema.get("function")
    if not isinstance(fn, dict) or not fn.get("name"):
        raise ValueError("tool schema must be {'type':'function','function':{'name':...}}")
    fname = fn["name"]
    if expected_name is not None and fname != expected_name:
        raise ValueError(
            f"schema function name '{fname}' must equal the row name '{expected_name}'"
        )
    return fname


def _extract_tools(module: ModuleType, name: str) -> list[tuple[dict, callable]]:
    """Pull (schema, executor) pairs from the tool module, validating contract."""
    schema = getattr(module, "SCHEMA", None) or getattr(module, "TOOL", None)
    execute = getattr(module, "execute", None)
    if schema is not None and callable(execute):
        _validate_schema(schema, name)
        return [(schema, execute)]

    core = getattr(module, "CORE_TOOLS", None)
    if core:
        pairs: list[tuple[dict, callable]] = []
        for entry in core:
            if not (isinstance(entry, tuple) and len(entry) == 2 and callable(entry[1])):
                raise ValueError("CORE_TOOLS entries must be (schema, executor) tuples")
            _validate_schema(entry[0], None)
            pairs.append(entry)
        names = {(s.get("function") or {}).get("name") for s, _ in pairs}
        if name not in names:
            raise ValueError(f"CORE_TOOLS must define a tool named '{name}' (matching the row)")
        return pairs

    raise ValueError(
        "module must define SCHEMA (dict) + execute(ctx, args), or CORE_TOOLS=[(schema, executor), ...]"
    )


# --------------------------------------------------------------------------- #
# Dependency install
# --------------------------------------------------------------------------- #
def _ensure_deps(row: dict) -> None:
    name = row["name"]
    deps = row.get("dependencies") or []
    if not deps:
        return
    # Cache marker: skip pip entirely when the installed set already matches.
    if row.get("installed_deps") == deps:
        return
    blocked = [d for d in deps if not is_dep_allowed(_pkg_name(d))]
    if blocked:
        raise PermissionError(f"dependencies not allowed by policy: {blocked}")

    logger.info("AI tool '%s': installing dependencies %s", name, deps)
    tool_repo.set_status(name, "installing", None)
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", *deps],
        capture_output=True, text=True, timeout=_PIP_TIMEOUT,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise RuntimeError(f"pip install failed (code {proc.returncode}): {tail}")
    tool_repo.set_installed_deps(name, deps)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def _process_tool(handler, row: dict, tools_dir: Path) -> int:
    name = row["name"]
    if not NAME_RE.match(name or ""):
        raise ValueError(f"invalid tool name {name!r} (must match {NAME_RE.pattern})")

    _ensure_deps(row)

    path = tools_dir / f"{name}.py"
    path.write_text(row.get("code") or "", encoding="utf-8")

    module = _import_tool_module(name, path)
    pairs = _extract_tools(module, name)
    registered = handler.register_ai_tools(pairs)
    tool_repo.set_status(name, "ok", None)
    return registered


def install_and_register(handler, data_dir) -> None:
    """Install + register every enabled code-in-DB tool. Best-effort per tool."""
    try:
        rows = tool_repo.list_enabled()
    except Exception as e:
        logger.warning("AI tools: cannot list enabled rows (%s)", e)
        return
    if not rows:
        return

    tools_dir = ai_tools_dir(data_dir)
    _ensure_parent_package(tools_dir)

    ok = 0
    for row in rows:
        name = row.get("name", "?")
        try:
            ok += _process_tool(handler, row, tools_dir)
        except Exception as e:
            logger.error("AI tool '%s' failed to install/register: %s", name, e)
            try:
                tool_repo.set_status(name, "failed", str(e))
            except Exception:
                pass
    if ok:
        logger.info("AI engine: registered %d code-in-DB tool(s)", ok)
