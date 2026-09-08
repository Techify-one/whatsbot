"""WhatsBot — self-update endpoint (uses GitHub Releases API for versioning)."""

import asyncio
import json
import logging
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from server.helpers import _ok, _err

logger = logging.getLogger(__name__)

GITHUB_REPO = "Techify-one/whatsbot"
GITHUB_RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_TAG_ZIP_URL_TEMPLATE = f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{{tag}}.zip"

VERSION_FILENAME = "WHATSBOT_VERSION"

PRESERVE_DIRS = {"storages", "statics", "logs", "venv", ".git", "bin"}
PRESERVE_FILES = {".env"}


def _get_project_root(settings) -> Path:
    return Path(settings.data_dir)


def _read_local_version(project_root: Path) -> dict:
    """Read the installed version + per-version changelog from WHATSBOT_VERSION.

    The file ships inside the repo and inside every release's tag zip (bumped
    by the /release-up flow before tagging, which PREPENDS a new entry to
    `changelog` instead of overwriting it), so history accumulates release
    over release. Reading from this tracked file — instead of `git describe`
    — also works whether the install is a git clone or a plain "Download ZIP":
    a ZIP install has no .git directory, so `git describe` silently fails and
    always reports "0.0.0".

    `changelog` is newest-first: `changelog[0]` is always the entry for the
    installed `version`. `popup_shown` tracks (per this installation, not per
    browser) whether the "what's new" popup was already dismissed for
    `version` — /release-up resets it to `false` on every bump; missing it
    entirely (file predates this field) is treated as `false` too, so an
    install updating for the first time since this field was introduced still
    gets shown the popup once instead of silently skipping it.
    """
    path = project_root / VERSION_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        changelog = [
            {"version": str(e.get("version") or ""), "description": str(e.get("description") or "")}
            for e in (data.get("changelog") or [])
            if isinstance(e, dict)
        ]
        version = str(data.get("version") or (changelog[0]["version"] if changelog else "0.0.0"))
        popup_shown = bool(data.get("popup_shown", False))
        return {"version": version, "changelog": changelog, "popup_shown": popup_shown}
    except Exception as exc:
        logger.debug("Failed to read %s: %s", VERSION_FILENAME, exc)
        return {"version": "0.0.0", "changelog": [], "popup_shown": True}


def _mark_popup_shown(project_root: Path) -> dict:
    """Flip `popup_shown` to True in WHATSBOT_VERSION, in place.

    Runs on this installation's own copy of the file (self-update already
    overwrites the whole file with the new release's, `popup_shown: false`
    included) — so this is per-installation state, not per-browser. Only
    `popup_shown` is touched; `version`/`changelog` are written back exactly
    as read.
    """
    path = project_root / VERSION_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read %s to mark popup as shown: %s", VERSION_FILENAME, exc)
        return _read_local_version(project_root)
    data["popup_shown"] = True
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to write %s to mark popup as shown: %s", VERSION_FILENAME, exc)
    return _read_local_version(project_root)


def _fetch_latest_release() -> dict:
    """Fetch the latest release's tag + release notes from the GitHub API."""
    try:
        req = urllib.request.Request(
            GITHUB_RELEASES_API,
            headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "WhatsBot"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            tag = data.get("tag_name", "")
            return {
                "tag": tag,
                "version": tag.lstrip("v"),
                "description": data.get("body") or "",
                "url": data.get("html_url", ""),
            }
    except Exception as exc:
        logger.warning("Failed to fetch latest release: %s", exc)
        return {"tag": "", "version": "", "description": "", "url": ""}


def _should_preserve(rel_path: str) -> bool:
    """Return True if *rel_path* must NOT be overwritten during update."""
    parts = Path(rel_path).parts
    if not parts:
        return True
    if parts[0] in PRESERVE_DIRS:
        return True
    if rel_path in PRESERVE_FILES:
        return True
    if "__pycache__" in parts:
        return True
    return False


def _perform_update(project_root: Path, tag: str) -> dict:
    """Download the given release tag's ZIP from GitHub and overwrite code files.

    Downloads the tag's snapshot (not `main` HEAD) so a self-update always
    lands on the stable, released state instead of whatever unreleased work
    happens to be on the branch.
    """
    zip_url = GITHUB_TAG_ZIP_URL_TEMPLATE.format(tag=tag)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "update.zip"

        # ── Download ──────────────────────────────────────────────
        logger.info("Downloading update (%s) from %s", tag, zip_url)
        try:
            urllib.request.urlretrieve(zip_url, str(zip_path))
        except Exception as exc:
            raise RuntimeError(f"Erro ao baixar a release {tag}: {exc}") from exc

        # ── Extract ───────────────────────────────────────────────
        try:
            zf = zipfile.ZipFile(zip_path)
        except zipfile.BadZipFile as exc:
            raise RuntimeError("Arquivo de atualização inválido.") from exc

        with zf:
            names = zf.namelist()
            if not names:
                raise RuntimeError("ZIP vazio.")

            # GitHub tag ZIPs have a top-level folder like "whatsbot-0.1.1/"
            top_folder = names[0].split("/")[0] + "/"

            extract_dir = tmp_path / "extracted"
            zf.extractall(extract_dir)

        source_root = extract_dir / top_folder.rstrip("/")
        if not source_root.is_dir():
            raise RuntimeError(f"Estrutura inesperada no ZIP (pasta {top_folder} não encontrada).")

        # ── Copy new files ────────────────────────────────────────
        copied = 0
        for src_file in source_root.rglob("*"):
            if src_file.is_dir():
                continue

            rel = src_file.relative_to(source_root).as_posix()

            # Security: reject path traversal
            if ".." in rel:
                logger.warning("Skipping suspicious path: %s", rel)
                continue

            if _should_preserve(rel):
                continue

            dest = project_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dest)
            copied += 1

        # The tag's own WHATSBOT_VERSION file was just copied in above, so this
        # already reflects the new version + its changelog history.
        info = _read_local_version(project_root)
        logger.info("Update applied: %d files updated. New version: %s", copied, info["version"])
        return {
            "version": info["version"],
            "changelog": info["changelog"],
            "files_updated": copied,
            "message": f"Atualizado para v{info['version']} — {copied} arquivos atualizados. Reinicie o servidor para aplicar.",
        }


def register_routes(app, deps):
    settings = deps.settings

    @app.get("/api/update/check")
    async def check_update():
        project_root = _get_project_root(settings)
        current = await asyncio.to_thread(_read_local_version, project_root)
        latest = await asyncio.to_thread(_fetch_latest_release)
        has_update = bool(latest["version"] and latest["version"] != current["version"])
        return _ok({
            "current_version": current["version"],
            "latest_version": latest["version"],
            "latest_description": latest["description"],
            "release_url": latest["url"],
            "update_available": has_update,
        })

    @app.get("/api/update/local-version")
    async def local_version():
        # Local-only read (no GitHub call) — safe to call on every app boot to
        # drive the "what's new" popup without hitting the GitHub API rate limit.
        # Returns the full changelog history (newest-first) so the frontend can
        # show everything a user missed if they skipped several releases.
        project_root = _get_project_root(settings)
        info = await asyncio.to_thread(_read_local_version, project_root)
        return _ok(info)

    @app.post("/api/update/popup-seen")
    async def mark_popup_seen():
        # Flips popup_shown to true in WHATSBOT_VERSION so the "what's new"
        # popup is shown once per installation (not per browser/device).
        project_root = _get_project_root(settings)
        info = await asyncio.to_thread(_mark_popup_shown, project_root)
        return _ok(info)

    @app.post("/api/update")
    async def apply_update():
        project_root = _get_project_root(settings)
        latest = await asyncio.to_thread(_fetch_latest_release)
        if not latest["tag"]:
            return _err("Não foi possível encontrar uma release publicada no GitHub.", 502)
        try:
            result = await asyncio.to_thread(_perform_update, project_root, latest["tag"])
        except RuntimeError as exc:
            return _err(str(exc), 500)
        except Exception as exc:
            logger.exception("Unexpected error during update")
            return _err(f"Erro inesperado: {exc}", 500)
        return _ok(result)
