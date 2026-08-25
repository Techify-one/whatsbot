"""GOWA updater tests.

Runs entirely offline: a fake release zip is fabricated on disk and served
through ``file://``, so download, checksum verification, binary swap, health
check and rollback are all exercised without touching the network.

    python tests/test_gowa_update.py
"""

import hashlib
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gowa import binary as gowa_binary  # noqa: E402
from gowa import updater as gowa_updater  # noqa: E402

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        print(f"  FAIL {name}" + (f" -> {detail}" if detail else ""))


def section(title: str) -> None:
    print("\n" + "-" * 60)
    print(f"  {title}")
    print("-" * 60)


# ── Fake project root + fake release ───────────────────────────────────

ROOT = Path(tempfile.mkdtemp(prefix="whatsbot-gowa-test-"))
RELEASE = Path(tempfile.mkdtemp(prefix="whatsbot-gowa-release-"))
BUNDLED_BYTES = b"#!/bin/sh\nexit 0\n"

(ROOT / "bin").mkdir()
(ROOT / "storages").mkdir()
(ROOT / "GOWA_VERSION").write_text("8.8.0\n")
(ROOT / "bin" / gowa_binary.binary_name()).write_bytes(BUNDLED_BYTES)
(ROOT / "bin" / gowa_binary.binary_name()).chmod(0o755)
(ROOT / "storages" / "whatsapp.db").write_bytes(b"session")

gowa_binary.repo_root = lambda: ROOT  # type: ignore[assignment]
# Keep the rollback path fast; the real 45s budget is a production concern.
gowa_updater._HEALTH_TIMEOUT_SEC = 3

_combo = gowa_updater.detect_platform()
if _combo is None:
    print("Plataforma sem binário GOWA publicado; nada a testar aqui.")
    sys.exit(0)
OS_, ARCH = _combo


def publish(version: str) -> str:
    """Write a fake release zip + checksums entry. Returns the zip sha256."""
    asset = gowa_updater.asset_name(version, OS_, ARCH)
    zpath = RELEASE / asset
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("readme.md", "docs")
        zf.writestr(gowa_updater.inner_binary_name(OS_, ARCH), f"#!/bin/sh\nexit 0\n# {version}\n")
    digest = hashlib.sha256(zpath.read_bytes()).hexdigest()
    _checksums[asset] = digest
    _write_checksums()
    return digest


_checksums: dict[str, str] = {}


def _write_checksums() -> None:
    lines = [f"{sha}  {name}" for name, sha in _checksums.items()]
    (RELEASE / gowa_updater.checksums_asset(OS_)).write_text("\n".join(lines) + "\n")


gowa_updater.GOWA_RELEASE_DOWNLOAD = "file://" + str(RELEASE) + "/{asset}"

manager = MagicMock()
client = MagicMock()
client.health_check.return_value = True
client.get_app_info.return_value = None


# ═══════════════════════════════════════════════════════════════════
section("Version parsing & supported range")

for value, expected in [
    ("9.1.0", (9, 1, 0)),
    ("v9.1.0", (9, 1, 0)),
    ("9.1.0-rc1", (9, 1, 0)),
    ("8.8", (8, 8, 0)),
    ("lixo", (0, 0, 0)),
    ("", (0, 0, 0)),
]:
    check(f"parse_version({value!r})", gowa_binary.parse_version(value) == expected,
          str(gowa_binary.parse_version(value)))

check("version_cmp 9.1.0 > 8.8.0", gowa_binary.version_cmp("9.1.0", "8.8.0") == 1)
check("version_cmp 8.8.0 == 8.8.0", gowa_binary.version_cmp("8.8.0", "8.8.0") == 0)
check("version_cmp 8.7.0 < 8.8.0", gowa_binary.version_cmp("8.7.0", "8.8.0") == -1)

for value, expected in [
    ("8.7.9", False), ("8.8.0", True), ("9.1.0", True),
    ("9.99.99", True), ("10.0.0", False), ("", False),
]:
    check(f"is_supported({value!r}) -> {expected}", gowa_updater.is_supported(value) is expected)


# ═══════════════════════════════════════════════════════════════════
section("Asset naming")

check("asset_name", gowa_updater.asset_name("9.1.0", "linux", "amd64") == "whatsapp_9.1.0_linux_amd64.zip")
check("inner name (linux)", gowa_updater.inner_binary_name("linux", "amd64") == "linux-amd64")
check("inner name (windows) tem .exe", gowa_updater.inner_binary_name("windows", "amd64") == "windows-amd64.exe")
check("checksums darwin é arquivo separado", gowa_updater.checksums_asset("darwin") == "checksums-macos.txt")
check("checksums linux", gowa_updater.checksums_asset("linux") == "checksums.txt")


# ═══════════════════════════════════════════════════════════════════
section("Release interval")

_saved_release_list_cache = dict(gowa_updater._release_list_cache)
gowa_updater._release_list_cache.update({
    "fetched_at": time.monotonic(),
    "etag": "test",
    "data": [
        {"tag_name": "v9.2.0", "body": "latest", "draft": False, "prerelease": False},
        {"tag_name": "v8.9.0", "body": "first", "draft": False, "prerelease": False},
        {"tag_name": "v9.0.0-rc1", "body": "rc", "draft": False, "prerelease": True},
        {"tag_name": "v10.0.0", "body": "future", "draft": False, "prerelease": False},
        {"tag_name": "v8.8.0", "body": "installed", "draft": False, "prerelease": False},
    ],
})
interval = gowa_updater.fetch_release_interval("8.8.0", "9.2.0")
check("interval includes all newer stable releases",
      [item["version"] for item in interval] == ["8.9.0", "9.2.0"])
check("interval keeps release notes", interval[0]["notes"] == "first")
check("extract whatsmeow pseudo-version revision",
      gowa_updater._extract_whatsmeow_revision(
          "require (\n  go.mau.fi/whatsmeow v0.0.0-20260816113502-fb386f152837\n)"
      ) == "fb386f152837")
gowa_updater._release_list_cache.clear()
gowa_updater._release_list_cache.update(_saved_release_list_cache)


# ═══════════════════════════════════════════════════════════════════
section("Binary resolution")

check("sem override -> bundled", gowa_binary.resolve_binary()[1] == "bundled")
check("versão bundled vem do GOWA_VERSION", gowa_binary.read_bundled_version() == "8.8.0")


# ═══════════════════════════════════════════════════════════════════
section("Install — happy path")

publish("9.1.0")
result = gowa_updater.install("9.1.0", manager=manager, client=client)
check("ok", result["ok"], result.get("message", ""))
check("binário gravado em storages/bin", gowa_binary.managed_binary_path().exists())
check("bin/ intocado", (ROOT / "bin" / gowa_binary.binary_name()).read_bytes() == BUNDLED_BYTES)
check("meta.version", gowa_binary.read_meta().get("version") == "9.1.0")
check("checksum verificado", gowa_binary.read_meta().get("checksum") == "verified")
check("resolve -> managed", gowa_binary.resolve_binary()[1] == "managed")
check("previous_source registrado", gowa_binary.read_meta().get("previous_source") == "bundled")
check("snapshot da sessão em salto de major", bool(gowa_binary.read_meta().get("session_backup")))
check("staging limpo", not gowa_binary.tmp_dir().exists())
check("GOWA parado e religado", manager.stop.called and manager.start.called)


# ═══════════════════════════════════════════════════════════════════
section("Install — guards")

result = gowa_updater.install("10.5.0", manager=manager, client=client)
check("fora da faixa pede confirmação",
      result.get("requires_confirmation") is True and not result["ok"])

result = gowa_updater.install("../../etc/passwd", manager=manager, client=client)
check("versão malformada rejeitada", not result["ok"] and "inválida" in result["message"])

result = gowa_updater.install("9.1.0.0", manager=manager, client=client)
check("versão de 4 partes rejeitada", not result["ok"])

before = gowa_binary.managed_binary_path().read_bytes()
publish("9.1.1")
_checksums[gowa_updater.asset_name("9.1.1", OS_, ARCH)] = "0" * 64
_write_checksums()
result = gowa_updater.install("9.1.1", manager=manager, client=client)
check("checksum divergente aborta", not result["ok"], result["message"])
check("binário intacto após checksum ruim", gowa_binary.managed_binary_path().read_bytes() == before)
check("versão instalada continua 9.1.0", gowa_binary.read_meta().get("version") == "9.1.0")

gowa_updater._UPDATE_LOCK.acquire()
try:
    gowa_updater.install("9.1.0", manager=manager, client=client)
    check("update concorrente é recusado", False, "não levantou GowaUpdateBusy")
except gowa_updater.GowaUpdateBusy:
    check("update concorrente é recusado", True)
finally:
    gowa_updater._UPDATE_LOCK.release()


# ═══════════════════════════════════════════════════════════════════
section("Install — health check falha e reverte sozinho")

publish("9.2.0")
client.health_check.return_value = False
result = gowa_updater.install("9.2.0", manager=manager, client=client)
client.health_check.return_value = True
check("install falha", not result["ok"], result["message"])
check("rolled_back", result["rolled_back"] is True)
check("meta voltou para 9.1.0", gowa_binary.read_meta().get("version") == "9.1.0")
check("ainda usa o managed", gowa_binary.resolve_binary()[1] == "managed")


# ═══════════════════════════════════════════════════════════════════
section("Rollback manual")

result = gowa_updater.rollback(manager=manager, client=client)
check("ok", result["ok"], result.get("message", ""))
check("volta para o bundled", gowa_binary.resolve_binary()[1] == "bundled")
check("versão volta a 8.8.0", gowa_binary.installed_version() == "8.8.0")

result = gowa_updater.rollback(manager=manager, client=client)
check("rollback sem backup falha limpo", not result["ok"] and "anterior" in result["message"])


# ═══════════════════════════════════════════════════════════════════
section("Managed mais antigo que o bundled perde")

gowa_binary.managed_dir(create=True)
gowa_binary.managed_binary_path().write_bytes(b"velho")
gowa_binary.write_meta({"version": "8.0.0"})
check("bundled 8.8.0 ganha de managed 8.0.0", gowa_binary.resolve_binary()[1] == "bundled")

gowa_binary.write_meta({"version": "9.9.9"})
check("managed 9.9.9 ganha de bundled 8.8.0", gowa_binary.resolve_binary()[1] == "managed")

(ROOT / "bin" / ".gowa_stamp").write_text("10.0.0\n")
check("stamp mais novo tem precedência sobre GOWA_VERSION",
      gowa_binary.read_bundled_version() == "10.0.0")
check("bundled 10.0.0 ganha de managed 9.9.9", gowa_binary.resolve_binary()[1] == "bundled")


# ═══════════════════════════════════════════════════════════════════
shutil.rmtree(ROOT, ignore_errors=True)
shutil.rmtree(RELEASE, ignore_errors=True)

print("\n" + "=" * 60)
print(f"  RESULTS: {passed} passed, {failed} failed")
print("=" * 60)
sys.exit(1 if failed else 0)
