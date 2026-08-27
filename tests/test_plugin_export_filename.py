"""O nome do zip de export carrega a versão do manifesto.

``GET /api/plugins/{id}/export`` devolvia sempre ``<id>-plugin.zip``, então dois
downloads do mesmo plugin em momentos diferentes eram indistinguíveis sem abrir o
arquivo. Agora o nome é ``<id>-<versão>-plugin.zip``.

O que está travado aqui é a DEGRADAÇÃO: o nome nunca pode derrubar o download.
Manifesto ausente, ``whatsbot_api_version`` incompatível com o core rodando (o
``load_manifest`` levanta ``ValueError`` nesse caso — e é justamente o plugin que
o operador quer exportar para consertar em outro lugar) e versão com caractere
que envenenaria o ``Content-Disposition`` caem todos no nome antigo.

Run standalone: ``python tests/test_plugin_export_filename.py``
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from plugins.manifest import WHATSBOT_API_VERSION  # noqa: E402
from server.routes.plugins import _export_filename  # noqa: E402

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        msg = f"  FAIL {name}"
        if detail:
            msg += f" -- {detail}"
        print(msg)


def make_plugin(root: Path, pid: str, *, version: str = "1.35.0",
                api_range: str = ">=1.0,<99.0", manifest: bool = True) -> Path:
    d = root / pid
    d.mkdir(parents=True)
    if manifest:
        (d / "plugin.yaml").write_text(
            f"id: {pid}\n"
            f"name: {pid}\n"
            f"version: {version}\n"
            f'whatsbot_api_version: "{api_range}"\n',
            encoding="utf-8",
        )
    return d


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)

    name = _export_filename("foo", make_plugin(root / "a", "foo"))
    check("versão do manifesto entra no nome", name == "foo-1.35.0-plugin.zip",
          detail=name)

    name = _export_filename("foo", make_plugin(root / "b", "foo", manifest=False))
    check("pasta sem manifesto cai no nome antigo", name == "foo-plugin.zip",
          detail=name)

    major = int(WHATSBOT_API_VERSION.split(".")[0])
    incompat = f">={major + 50}.0,<{major + 60}.0"
    name = _export_filename("foo", make_plugin(root / "c", "foo", api_range=incompat))
    check("API incompatível não levanta, degrada", name == "foo-plugin.zip",
          detail=name)

    name = _export_filename("foo", make_plugin(root / "d", "foo", version='1.0.0+a"b'))
    check("versão com caractere hostil é descartada", name == "foo-plugin.zip",
          detail=name)


print("\n" + "=" * 60)
print(f"  RESULTS: {passed} passed, {failed} failed")
print("=" * 60)

if failed:
    sys.exit(1)
