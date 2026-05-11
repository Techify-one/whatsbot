"""Plugin loader and registry.

Discovers plugins under ``storages/plugins/<id>/``, parses each manifest,
runs pending SQL migrations for enabled plugins, then imports their Python
entry modules and collects tools, prompt fragments, FastAPI routers and
Pydantic settings classes.

The loader is called once at app startup (``server/app.py`` lifespan) after
``init_db()`` and before route registration.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import logging
import shutil
import sys
import traceback
from pathlib import Path
from types import ModuleType

from db.repositories import plugin_repo

from plugins.manifest import PluginManifest, find_manifest_file, load_manifest
from plugins.migrator import run_pending_migrations

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class LoadedPlugin:
    """A successfully loaded plugin and the artifacts it contributed."""

    manifest: PluginManifest
    plugin_dir: Path
    package_name: str
    tools: list[tuple[dict, callable]] = dataclasses.field(default_factory=list)
    prompt_fragments: list[callable] = dataclasses.field(default_factory=list)
    event_handlers: dict[str, callable] = dataclasses.field(default_factory=dict)
    # Each entry is either ``fn`` or ``(fn, priority:int)``.
    filters: dict[str, object] = dataclasses.field(default_factory=dict)
    router: object | None = None
    settings_cls: type | None = None
    static_dir: Path | None = None

    @property
    def id(self) -> str:
        return self.manifest.id


@dataclasses.dataclass
class DiscoveredPlugin:
    """A plugin found on disk but not necessarily loaded successfully."""

    manifest: PluginManifest
    plugin_dir: Path
    enabled: bool
    error: str | None = None


class PluginRegistry:
    """In-memory registry populated by ``discover_and_load``."""

    def __init__(self) -> None:
        self.loaded: dict[str, LoadedPlugin] = {}
        self.discovered: dict[str, DiscoveredPlugin] = {}

    def by_id(self, plugin_id: str) -> LoadedPlugin | None:
        return self.loaded.get(plugin_id)


def bootstrap_initial_plugins(plugins_dir: Path, source_dir: Path) -> list[str]:
    """Copy bundled example plugins into ``plugins_dir`` on first run.

    Runs only when ``plugins_dir`` is empty (no subdirectories) so user
    deletions stick across restarts. Subsequent updates of ``source_dir`` from
    the core never overwrite a user's installed plugins.
    """
    plugins_dir.mkdir(parents=True, exist_ok=True)
    has_anything = any(c.is_dir() and not c.name.startswith(".") for c in plugins_dir.iterdir())
    if has_anything:
        return []
    if not source_dir.is_dir():
        return []
    copied: list[str] = []
    for child in source_dir.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        target = plugins_dir / child.name
        if target.exists():
            continue
        shutil.copytree(child, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        copied.append(child.name)
        logger.info("Bootstrapped initial plugin: %s -> %s", child.name, target)
    return copied


def discover_and_load(plugins_dir: Path) -> PluginRegistry:
    """Scan ``plugins_dir``, sync DB, run migrations and import enabled plugins."""
    registry = PluginRegistry()
    if not plugins_dir.is_dir():
        plugins_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Created plugins directory: %s", plugins_dir)

    for child in sorted(plugins_dir.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name == "__pycache__":
            continue
        if find_manifest_file(child) is None:
            continue
        _process_one(child, registry)

    return registry


def _process_one(plugin_dir: Path, registry: PluginRegistry) -> None:
    pid = plugin_dir.name
    try:
        manifest = load_manifest(plugin_dir)
    except Exception as e:
        logger.error("Plugin %s: manifest invalid: %s", pid, e)
        # We can't upsert without a valid id, but the folder name is likely
        # the id; record the error against it if a row already exists.
        existing = plugin_repo.get(pid)
        if existing:
            plugin_repo.set_load_error(pid, f"manifest invalid: {e}")
        return

    plugin_repo.upsert(manifest.id, manifest.version)
    db_row = plugin_repo.get(manifest.id) or {"enabled": 0}
    enabled = bool(db_row.get("enabled"))
    registry.discovered[manifest.id] = DiscoveredPlugin(
        manifest=manifest,
        plugin_dir=plugin_dir,
        enabled=enabled,
    )

    if not enabled:
        logger.info("Plugin %s: disabled, skipping load", manifest.id)
        return

    try:
        loaded = _load_plugin_module(manifest, plugin_dir)
        run_pending_migrations(manifest, plugin_dir)
        registry.loaded[manifest.id] = loaded
        plugin_repo.set_load_error(manifest.id, None)
        logger.info(
            "Plugin %s loaded (tools=%d prompts=%d events=%d filters=%d router=%s screens=%d)",
            manifest.id,
            len(loaded.tools),
            len(loaded.prompt_fragments),
            len(loaded.event_handlers),
            len(loaded.filters),
            "yes" if loaded.router else "no",
            len(manifest.screens),
        )
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        logger.error("Plugin %s failed to load:\n%s", manifest.id, traceback.format_exc())
        plugin_repo.set_load_error(manifest.id, err)
        registry.discovered[manifest.id].error = err


def _load_plugin_module(
    manifest: PluginManifest, plugin_dir: Path
) -> LoadedPlugin:
    package_name = f"whatsbot_plugins.{manifest.id}"
    _ensure_parent_package()
    module = _import_package(package_name, plugin_dir)
    loaded = LoadedPlugin(
        manifest=manifest, plugin_dir=plugin_dir, package_name=package_name
    )

    # tools entry: module exporting CORE_TOOLS=[(schema, executor), ...]
    tools_modname = manifest.entry.get("tools")
    if tools_modname:
        tools_mod = _import_submodule(package_name, tools_modname, plugin_dir)
        tools_attr = getattr(tools_mod, "CORE_TOOLS", None) or getattr(tools_mod, "TOOLS", None)
        if tools_attr:
            for entry in tools_attr:
                if not isinstance(entry, tuple) or len(entry) != 2:
                    logger.warning(
                        "Plugin %s: tools entry must be (schema, executor) tuples, got %r",
                        manifest.id, entry,
                    )
                    continue
                loaded.tools.append(entry)

    # prompts entry: module exporting PROMPT_FRAGMENTS=[callable, ...]
    prompts_modname = manifest.entry.get("prompts")
    if prompts_modname:
        prompts_mod = _import_submodule(package_name, prompts_modname, plugin_dir)
        frags = getattr(prompts_mod, "PROMPT_FRAGMENTS", None) or []
        for fn in frags:
            if callable(fn):
                loaded.prompt_fragments.append(fn)

    # events entry: module exporting EVENT_HANDLERS={"name": callable, ...}
    events_modname = manifest.entry.get("events")
    if events_modname:
        events_mod = _import_submodule(package_name, events_modname, plugin_dir)
        raw_events = getattr(events_mod, "EVENT_HANDLERS", None) or {}
        if isinstance(raw_events, dict):
            for name, fn in raw_events.items():
                if callable(fn):
                    loaded.event_handlers[str(name)] = fn
                else:
                    logger.warning(
                        "Plugin %s: EVENT_HANDLERS[%r] is not callable, skipped",
                        manifest.id, name,
                    )
        else:
            logger.warning(
                "Plugin %s: EVENT_HANDLERS must be a dict, got %s",
                manifest.id, type(raw_events).__name__,
            )

    # filters entry: module exporting FILTERS={"filter.name": fn | (fn, priority)}
    filters_modname = manifest.entry.get("filters")
    if filters_modname:
        filters_mod = _import_submodule(package_name, filters_modname, plugin_dir)
        raw_filters = getattr(filters_mod, "FILTERS", None) or {}
        if isinstance(raw_filters, dict):
            for name, entry in raw_filters.items():
                if isinstance(entry, tuple) and len(entry) == 2 and callable(entry[0]):
                    loaded.filters[str(name)] = entry
                elif callable(entry):
                    loaded.filters[str(name)] = entry
                else:
                    logger.warning(
                        "Plugin %s: FILTERS[%r] must be callable or (callable, int), skipped",
                        manifest.id, name,
                    )
        else:
            logger.warning(
                "Plugin %s: FILTERS must be a dict, got %s",
                manifest.id, type(raw_filters).__name__,
            )

    # routes entry: module exporting router=APIRouter()
    routes_modname = manifest.entry.get("routes")
    if routes_modname:
        routes_mod = _import_submodule(package_name, routes_modname, plugin_dir)
        loaded.router = getattr(routes_mod, "router", None)

    # settings entry: module exporting Settings (Pydantic BaseModel) — phase 6
    settings_modname = manifest.entry.get("settings")
    if settings_modname:
        settings_mod = _import_submodule(package_name, settings_modname, plugin_dir)
        loaded.settings_cls = getattr(settings_mod, "Settings", None)

    static_dir = plugin_dir / "static"
    if static_dir.is_dir():
        loaded.static_dir = static_dir

    # Keep module reference so it's not garbage-collected.
    _ = module
    return loaded


def _ensure_parent_package() -> None:
    """Make ``whatsbot_plugins`` a synthetic namespace package."""
    if "whatsbot_plugins" in sys.modules:
        return
    spec = importlib.util.spec_from_loader("whatsbot_plugins", loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__path__ = []  # type: ignore[attr-defined]
    sys.modules["whatsbot_plugins"] = module


def _import_package(package_name: str, plugin_dir: Path) -> ModuleType:
    init_file = plugin_dir / "__init__.py"
    if not init_file.exists():
        # auto-create empty __init__.py for plugin authors who forget it
        init_file.write_text("", encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        package_name,
        init_file,
        submodule_search_locations=[str(plugin_dir)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build spec for {package_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    spec.loader.exec_module(module)
    return module


def _import_submodule(
    package_name: str, modname: str, plugin_dir: Path
) -> ModuleType:
    full = f"{package_name}.{modname}"
    if full in sys.modules:
        return sys.modules[full]
    file_path = plugin_dir / f"{modname}.py"
    if not file_path.exists():
        raise ImportError(f"{full}: file not found at {file_path}")
    spec = importlib.util.spec_from_file_location(full, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build spec for {full}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module
