"""Comprehensive endpoint tests for WhatsBot API.

Uses FastAPI TestClient with a real temporary SQLite database.
No external services needed (GOWA/OpenRouter are mocked).
"""

import io
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Initialize the engine in a temp directory before importing anything else.
# Default: SQLite. Override with ``WHATSBOT_TEST_DB_URL`` to run the same
# assertions against Postgres (e.g. via testcontainers).
_tmpdir = tempfile.mkdtemp(prefix="whatsbot_test_")
_db_path = Path(_tmpdir) / "whatsbot.db"

from db import init_db, init_engine

_test_url = os.environ.get("WHATSBOT_TEST_DB_URL", "").strip()
if _test_url:
    init_engine(_test_url)
    from db.connection import _run_alembic_upgrade  # noqa: E402
    _run_alembic_upgrade()
else:
    init_db(_db_path)

# Seed some test data
from db.repositories import contact_repo, message_repo, usage_repo, tag_repo, config_repo

def _seed_data():
    """Insert test contacts, messages, tags, usage into the test DB."""
    now = time.time()

    # Create contacts
    c1 = contact_repo.get_or_create("5511999990001")
    contact_repo.update(c1["id"], name="Alice Test", email="alice@test.com",
                        profession="Engineer", company="TestCo")

    c2 = contact_repo.get_or_create("5511999990002")
    contact_repo.update(c2["id"], name="Bob Test", is_archived=True)

    # Add messages
    message_repo.add(c1["id"], "user", "Olá, tudo bem?", ts=now - 100)
    message_repo.add(c1["id"], "assistant", "Tudo sim! Como posso ajudar?", ts=now - 90)
    message_repo.add(c1["id"], "user", "Qual o horário de funcionamento?", ts=now - 50)
    message_repo.add(c1["id"], "assistant", "Nosso horário é de 9h às 18h.", ts=now - 40)

    message_repo.add(c2["id"], "user", "Oi", ts=now - 200)

    # Add observations
    contact_repo.add_observation(c1["id"], "Cliente VIP")

    # Add tags
    tag_repo.create("vip", "#ff0000")
    tag_repo.create("lead", "#00ff00")
    tag_repo.add_contact_tag(c1["id"], "vip")

    # Add usage
    usage_repo.add(c1["id"], "text", "openai/gpt-4o-mini", 100, 50, 150, 0.001)
    usage_repo.add(c1["id"], "text", "openai/gpt-4o-mini", 200, 80, 280, 0.002)

    # Increment unread for c2
    contact_repo.increment_unread(c2["id"], "msg_001")

_seed_data()

# Now import app components
from config.settings import Settings
from agent.handler import AgentHandler
from server.app import create_app

# Create mocks for GOWA
mock_gowa_manager = MagicMock()
mock_gowa_client = MagicMock()
mock_gowa_client.send_message = MagicMock(return_value=None)
mock_gowa_client.send_image = MagicMock(return_value=None)
mock_gowa_client.send_audio = MagicMock(return_value=None)
mock_gowa_client.send_file = MagicMock(return_value=None)
mock_gowa_client.send_chat_presence = MagicMock(return_value=None)
mock_gowa_client.mark_as_read = MagicMock(return_value=None)
mock_gowa_client.revoke_message = MagicMock(return_value=None)
mock_gowa_client.delete_message = MagicMock(return_value=None)
mock_gowa_client.react_to_message = MagicMock(return_value=None)
mock_gowa_client.reconnect = MagicMock(return_value=None)
mock_gowa_client.logout = MagicMock(return_value=None)
mock_gowa_client.get_own_number = MagicMock(return_value="5511999990001")

# Create real Settings and AgentHandler (backed by test DB)
settings = Settings()
agent_handler = AgentHandler(
    api_key="test-key-fake",
    system_prompt="Você é um assistente de teste.",
    max_context_messages=10,
    model="openai/gpt-4o-mini",
)

# Create the app (skip lifespan to avoid background tasks)
app = create_app(
    settings=settings,
    gowa_manager=mock_gowa_manager,
    gowa_client=mock_gowa_client,
    agent_handler=agent_handler,
)

# Patch lifespan to be a no-op for testing
from contextlib import asynccontextmanager

@asynccontextmanager
async def _noop_lifespan(app):
    yield

app.router.lifespan_context = _noop_lifespan

from starlette.testclient import TestClient
client = TestClient(app, raise_server_exceptions=False)


# ═══════════════════════════════════════════════════════════════════
#  Test runner
# ═══════════════════════════════════════════════════════════════════

passed = 0
failed = 0
errors = []


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        msg = f"  FAIL {name}" + (f" -- {detail}" if detail else "")
        print(msg)
        errors.append(msg)


def section(title: str):
    print(f"\n{'-'*60}")
    print(f"  {title}")
    print(f"{'-'*60}")


# ═══════════════════════════════════════════════════════════════════
#  1. Health
# ═══════════════════════════════════════════════════════════════════
section("Health")
r = client.get("/health")
check("GET /health -> 200", r.status_code == 200)
check("GET /health -> ok=true", r.json().get("ok") is True)

# ═══════════════════════════════════════════════════════════════════
#  2. Auth
# ═══════════════════════════════════════════════════════════════════
section("Auth")

r = client.get("/api/auth/check")
check("GET /api/auth/check (no password) -> authenticated", r.json()["data"]["authenticated"] is True)
check("GET /api/auth/check -> has_password=false", r.json()["data"]["has_password"] is False)

r = client.post("/api/auth/login", json={"password": "test"})
check("POST /api/auth/login (no pw set) -> 400", r.status_code == 400)

# ═══════════════════════════════════════════════════════════════════
#  3. Config
# ═══════════════════════════════════════════════════════════════════
section("Config")

r = client.get("/api/config")
check("GET /api/config -> 200", r.status_code == 200)
data = r.json()["data"]
check("GET /api/config -> has model field", "model" in data)
check("GET /api/config -> has API key field", "openrouter_api_key" in data)
check("GET /api/config -> has system_prompt", "system_prompt" in data)
check("GET /api/config -> has split_messages", "split_messages" in data)
check("GET /api/config -> has has_password", "has_password" in data)
check("GET /api/config -> has setup_completed", "setup_completed" in data)

r = client.put("/api/config", json={"auto_reply": False})
check("PUT /api/config -> 200", r.status_code == 200)
check("PUT /api/config -> saved", r.json()["data"]["message"] == "Configurações salvas!")

r = client.get("/api/config")
check("PUT /api/config -> auto_reply persisted", r.json()["data"]["auto_reply"] is False)

# Restore
client.put("/api/config", json={"auto_reply": True})

# group_reply_mode is exposed and round-trips (controls AI replies in groups)
check("GET /api/config -> has group_reply_mode", "group_reply_mode" in data)
r = client.put("/api/config", json={"group_reply_mode": "always"})
check("PUT /api/config (group_reply_mode) -> 200", r.status_code == 200)
r = client.get("/api/config")
check("PUT /api/config -> group_reply_mode persisted",
      r.json()["data"]["group_reply_mode"] == "always")
client.put("/api/config", json={"group_reply_mode": "mention_only"})

# setup_completed flag round-trips through PUT
r = client.put("/api/config", json={"setup_completed": True})
check("PUT /api/config (setup_completed) -> 200", r.status_code == 200)
r = client.get("/api/config")
check("PUT /api/config -> setup_completed persisted", r.json()["data"]["setup_completed"] is True)
client.put("/api/config", json={"setup_completed": False})  # restore

# Test key (will fail since no real API)
r = client.post("/api/config/test-key", json={"api_key": ""})
check("POST /api/config/test-key (empty) -> error", r.json()["ok"] is False)

# ═══════════════════════════════════════════════════════════════════
#  4. Status
# ═══════════════════════════════════════════════════════════════════
section("Status")

r = client.get("/api/status")
check("GET /api/status -> 200", r.status_code == 200)
data = r.json()["data"]
check("GET /api/status -> has connected", "connected" in data)
check("GET /api/status -> has msg_count", "msg_count" in data)
check("GET /api/status -> has auto_reply_running", "auto_reply_running" in data)

# ═══════════════════════════════════════════════════════════════════
#  5. Contacts list
# ═══════════════════════════════════════════════════════════════════
section("Contacts — List")

r = client.get("/api/contacts")
check("GET /api/contacts -> 200", r.status_code == 200)
contacts_data = r.json()["data"]
check("GET /api/contacts -> is list", isinstance(contacts_data, list))
non_archived = [c for c in contacts_data if not c.get("is_archived")]
check("GET /api/contacts -> has non-archived contacts", len(non_archived) >= 1)
check("GET /api/contacts -> exposes avatar_v (cache-busting)", all("avatar_v" in c for c in contacts_data))

# Search
r = client.get("/api/contacts?q=Alice")
check("GET /api/contacts?q=Alice -> finds Alice", len(r.json()["data"]) >= 1)

r = client.get("/api/contacts?q=xyznotexist")
check("GET /api/contacts?q=xyz -> empty", len(r.json()["data"]) == 0)

# Accent-insensitive search: an unaccented query matches accented names (both ways)
_acc = contact_repo.get_or_create("5511999990009")
contact_repo.update(_acc["id"], name="Ótavio Açaí")
r = client.get("/api/contacts?q=otavio")
check("GET /api/contacts?q=otavio -> matches 'Ótavio' (no accent)",
      any(c["phone"] == "5511999990009" for c in r.json()["data"]))
r = client.get("/api/contacts?q=AÇAÍ")
check("GET /api/contacts?q=AÇAÍ -> matches 'Açaí' (accented, any case)",
      any(c["phone"] == "5511999990009" for c in r.json()["data"]))

# Search by message content (normal, private note, transcription)
_msc = contact_repo.get_or_create("5511999990010")
contact_repo.update(_msc["id"], name="Sem Termo No Nome")
_orc = message_repo.add(_msc["id"], "user", "preciso do orçamento atualizado")
message_repo.add(_msc["id"], "private_note", "lembrete: cobrar pagamento amanhã")
message_repo.add(_msc["id"], "transcription", "transcrição: reunião remarcada para quinta")
r = client.get("/api/contacts?q=orcamento")
_hit = next((c for c in r.json()["data"] if c["phone"] == "5511999990010"), None)
check("GET /api/contacts?q=orcamento -> matches by normal message (accent-insensitive)", _hit is not None)
check("GET /api/contacts?q=orcamento -> returns match snippet (original accents)",
      bool(_hit) and "orçamento" in _hit.get("match_snippet", ""))
check("GET /api/contacts?q=orcamento -> returns matched message id",
      bool(_hit) and _hit.get("match_msg_id") == _orc["id"])
r = client.get("/api/contacts?q=cobrar pagamento")
check("GET /api/contacts?q=cobrar pagamento -> matches by private note",
      any(c["phone"] == "5511999990010" for c in r.json()["data"]))
r = client.get("/api/contacts?q=remarcada")
check("GET /api/contacts?q=remarcada -> matches by transcription",
      any(c["phone"] == "5511999990010" for c in r.json()["data"]))
r = client.get("/api/contacts?q=conteudoinexistente123")
check("GET /api/contacts?q=conteudoinexistente123 -> empty",
      not any(c["phone"] == "5511999990010" for c in r.json()["data"]))

# Bot @mention flag: surfaces in the list and clears when the chat is read
_mc = contact_repo.get_or_create("5511999990011")
contact_repo.update(_mc["id"], is_group=1, group_name="Grupo Menção")
contact_repo.set_mention(_mc["id"])
r = client.get("/api/contacts")
_mhit = next((c for c in r.json()["data"] if c["phone"] == "5511999990011"), None)
check("GET /api/contacts -> has_unread_mention exposed", bool(_mhit) and _mhit.get("has_unread_mention") is True)
contact_repo.mark_as_read(_mc["id"])
r = client.get("/api/contacts")
_mhit2 = next((c for c in r.json()["data"] if c["phone"] == "5511999990011"), None)
check("mark_as_read -> mention flag cleared", bool(_mhit2) and _mhit2.get("has_unread_mention") is False)

# Unread-count endpoint (browser-tab badge). Static path must win over /{phone}.
r = client.get("/api/contacts/unread-count")
check("GET /api/contacts/unread-count -> 200", r.status_code == 200)
check("GET /api/contacts/unread-count -> int count", isinstance(r.json()["data"]["count"], int))
_uc = contact_repo.get_or_create("5511999990012")
contact_repo.mark_as_read(_uc["id"])
_before = client.get("/api/contacts/unread-count").json()["data"]["count"]
contact_repo.increment_unread(_uc["id"], "WAMID_UC_1")
_after = client.get("/api/contacts/unread-count").json()["data"]["count"]
check("unread-count -> increments with an unread conversation", _after == _before + 1)
contact_repo.mark_as_read(_uc["id"])
_cleared = client.get("/api/contacts/unread-count").json()["data"]["count"]
check("unread-count -> drops after read", _cleared == _before)

# Archived
r = client.get("/api/contacts?archived=true")
check("GET /api/contacts?archived=true -> has archived", len(r.json()["data"]) >= 1)
archived_names = [c.get("name", "") for c in r.json()["data"]]
check("GET /api/contacts?archived=true -> Bob is archived", any("Bob" in n for n in archived_names))

# Pin / unpin: pinned conversations sort to the top of the list
r = client.post("/api/contacts/5511999990001/pin", json={"pinned": True})
check("POST /pin -> 200", r.status_code == 200)
check("POST /pin -> pinned true", r.json()["data"]["pinned"] is True)
r = client.get("/api/contacts")
_list = r.json()["data"]
check("GET /api/contacts -> pinned contact is first", _list and _list[0]["phone"] == "5511999990001")
check("GET /api/contacts -> exposes is_pinned", _list[0].get("is_pinned") is True)
r = client.post("/api/contacts/5511999990001/pin", json={"pinned": False})
check("POST /pin (unpin) -> 200", r.status_code == 200 and r.json()["data"]["pinned"] is False)
r = client.post("/api/contacts/5511999990001/pin", json={})
check("POST /pin (no field) -> 400", r.status_code == 400)
r = client.post("/api/contacts/5511999999999/pin", json={"pinned": True})
check("POST /pin (unknown contact) -> 404", r.status_code == 404)

# ═══════════════════════════════════════════════════════════════════
#  6. Contact detail
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Detail")

r = client.get("/api/contacts/5511999990001")
check("GET /api/contacts/{phone} -> 200", r.status_code == 200)
data = r.json()["data"]
check("GET /api/contacts/{phone} -> has phone", data.get("phone") == "5511999990001")
check("GET /api/contacts/{phone} -> has name", data.get("name") == "Alice Test")
check("GET /api/contacts/{phone} -> has email", data.get("email") == "alice@test.com")
check("GET /api/contacts/{phone} -> has messages", isinstance(data.get("messages"), list))
check("GET /api/contacts/{phone} -> messages count", len(data["messages"]) == 4)
check("GET /api/contacts/{phone} -> has tags", isinstance(data.get("tags"), list))
check("GET /api/contacts/{phone} -> has observations", isinstance(data.get("info", {}).get("observations"), list))

# Non-existent contact — auto-creates on GET
r = client.get("/api/contacts/0000000000")
check("GET /api/contacts/0000 -> auto-create 200", r.status_code == 200 and r.json()["ok"])

# ═══════════════════════════════════════════════════════════════════
#  7. Contact send message
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Send Message")

r = client.post("/api/contacts/5511999990001/send", json={"message": "Teste manual"})
check("POST /send -> 200", r.status_code == 200)
check("POST /send -> message sent", "enviada" in r.json()["data"]["message"].lower())
check("POST /send -> gowa called", mock_gowa_client.send_message.called)

# Empty message
r = client.post("/api/contacts/5511999990001/send", json={"message": ""})
check("POST /send (empty) -> 400", r.status_code == 400)

# Reply (quote an existing message): reply_to is forwarded to GOWA and persisted
mock_gowa_client.send_message.reset_mock()
r = client.post("/api/contacts/5511999990001/send",
                json={"message": "Respondendo", "reply_to": "WAMID_QUOTE_1"})
check("POST /send (reply_to) -> 200", r.status_code == 200)
_args, _kwargs = mock_gowa_client.send_message.call_args
check("POST /send (reply_to) -> gowa got reply id",
      "WAMID_QUOTE_1" in (list(_args) + list(_kwargs.values())))
_reply_cid = contact_repo.get_full_contact("5511999990001")["id"]
_last = message_repo.get_last(_reply_cid)
check("POST /send (reply_to) -> persisted reply_to_msg_id",
      _last.get("reply_to_msg_id") == "WAMID_QUOTE_1")

# ═══════════════════════════════════════════════════════════════════
#  7a. Send wa.me link to the connected account's own chat
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Send Self Link")

mock_gowa_client.send_message.reset_mock()
r = client.post("/api/contacts/send-self-link", json={"phone": "85973605591"})
check("POST /send-self-link -> 200", r.status_code == 200)
_sl = r.json().get("data") or {}
check("POST /send-self-link -> wa.me link",
      _sl.get("link") == "https://wa.me/5585973605591", _sl.get("link"))
check("POST /send-self-link -> own phone", _sl.get("own_phone") == "5511999990001")
_args, _kwargs = mock_gowa_client.send_message.call_args
check("POST /send-self-link -> sent to own number",
      "5511999990001" in (list(_args) + list(_kwargs.values())))
check("POST /send-self-link -> link in message text",
      any("https://wa.me/5585973605591" in str(a) for a in (list(_args) + list(_kwargs.values()))))
_self_contact = contact_repo.get_full_contact("5511999990001")
check("POST /send-self-link -> saved on own chat",
      "https://wa.me/5585973605591" in (message_repo.get_last(_self_contact["id"]) or {}).get("content", ""))

r = client.post("/api/contacts/send-self-link", json={"phone": "123"})
check("POST /send-self-link (invalid) -> 400", r.status_code == 400)

# ═══════════════════════════════════════════════════════════════════
#  8. Contact retry send
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Retry Send")

r = client.post("/api/contacts/5511999990001/retry-send", json={"message": "Retry msg"})
check("POST /retry-send -> 200", r.status_code == 200)

r = client.post("/api/contacts/5511999990001/retry-send", json={"message": ""})
check("POST /retry-send (empty) -> 400", r.status_code == 400)

# ═══════════════════════════════════════════════════════════════════
#  8a. Delete message (scope me / all)
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Delete Message")

_del_cid = contact_repo.get_full_contact("5511999990001")["id"]

# scope=all on own (outgoing) message -> revoke for everyone
message_repo.add(_del_cid, "assistant", "Msg para apagar", msg_id="WAMID_DEL_1", status="operator")
mock_gowa_client.revoke_message.reset_mock()
r = client.post("/api/contacts/5511999990001/messages/delete",
                json={"msg_id": "WAMID_DEL_1", "scope": "all"})
check("POST /messages/delete (all) -> 200", r.status_code == 200)
check("POST /messages/delete (all) -> gowa revoke called", mock_gowa_client.revoke_message.called)
_m = message_repo.get_by_msg_id("WAMID_DEL_1")
check("POST /messages/delete (all) -> flagged revoked", bool(_m) and bool(_m.get("revoked")))
check("POST /messages/delete (all) -> content preserved", bool(_m) and bool(_m.get("content")))
check("POST /messages/delete (all) -> scope all", bool(_m) and _m.get("revoke_scope") == "all")

# scope=all on a contact (user) message -> rejected
message_repo.add(_del_cid, "user", "Msg do contato", msg_id="WAMID_USER_1")
r = client.post("/api/contacts/5511999990001/messages/delete",
                json={"msg_id": "WAMID_USER_1", "scope": "all"})
check("POST /messages/delete (all, user msg) -> 400", r.status_code == 400)

# scope=me by db_id (local message without msg_id) -> row kept, flagged revoked
_local = message_repo.add(_del_cid, "assistant", "Local sem msg_id")
r = client.post("/api/contacts/5511999990001/messages/delete",
                json={"db_id": _local["id"], "scope": "me"})
check("POST /messages/delete (me, db_id) -> 200", r.status_code == 200)
_kept = [m for m in message_repo.get_all(_del_cid) if m.get("_id") == _local["id"]]
check("POST /messages/delete (me) -> row kept", len(_kept) == 1)
check("POST /messages/delete (me) -> flagged revoked", bool(_kept and _kept[0].get("revoked")))
check("POST /messages/delete (me) -> content preserved", bool(_kept and _kept[0].get("content")))
check("POST /messages/delete (me) -> scope me", bool(_kept) and _kept[0].get("revoke_scope") == "me")

# missing identifiers -> 400
r = client.post("/api/contacts/5511999990001/messages/delete", json={"scope": "me"})
check("POST /messages/delete (no id) -> 400", r.status_code == 400)

# ═══════════════════════════════════════════════════════════════════
#  8b. React to message
# ═══════════════════════════════════════════════════════════════════
section("Contacts — React Message")

message_repo.add(_del_cid, "user", "Msg para reagir", msg_id="WAMID_REACT_1")

# Add a reaction
mock_gowa_client.react_to_message.reset_mock()
r = client.post("/api/contacts/5511999990001/messages/react",
                json={"msg_id": "WAMID_REACT_1", "emoji": "👍"})
check("POST /messages/react -> 200", r.status_code == 200)
check("POST /messages/react -> gowa called", mock_gowa_client.react_to_message.called)
check("POST /messages/react -> reactions in response", r.json()["data"]["reactions"].get("👍") == ["me"])
_m = message_repo.get_by_msg_id("WAMID_REACT_1")
check("POST /messages/react -> persisted", _m.get("reactions", {}).get("👍") == ["me"])

# Change reaction (replaces, one per reactor)
r = client.post("/api/contacts/5511999990001/messages/react",
                json={"msg_id": "WAMID_REACT_1", "emoji": "❤️"})
_m = message_repo.get_by_msg_id("WAMID_REACT_1")
check("POST /messages/react (change) -> replaced", "👍" not in _m.get("reactions", {}) and _m["reactions"].get("❤️") == ["me"])

# Remove reaction (empty emoji)
r = client.post("/api/contacts/5511999990001/messages/react",
                json={"msg_id": "WAMID_REACT_1", "emoji": ""})
_m = message_repo.get_by_msg_id("WAMID_REACT_1")
check("POST /messages/react (remove) -> cleared", not _m.get("reactions"))

# Missing msg_id -> 400
r = client.post("/api/contacts/5511999990001/messages/react", json={"emoji": "👍"})
check("POST /messages/react (no msg_id) -> 400", r.status_code == 400)

# ═══════════════════════════════════════════════════════════════════
#  8b. Private message (panel-only) — no AI trigger
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Private Message")

_gowa_calls_before = mock_gowa_client.send_message.call_count
r = client.post(
    "/api/contacts/5511999990001/private-message",
    json={"text": "Cliente VIP, atender com prioridade"},
)
check("POST /private-message -> 200", r.status_code == 200)
_pn = r.json()["data"]
check("POST /private-message -> saved note returned",
      _pn.get("role") == "private_note" and "prioridade" in (_pn.get("content") or ""))
check("POST /private-message -> returns DB id (for delete)", bool(_pn.get("_id")))
check(
    "POST /private-message -> no GOWA send",
    mock_gowa_client.send_message.call_count == _gowa_calls_before,
)

# Confirm the message is in the contact's history with role=private_note
r = client.get("/api/contacts/5511999990001")
msgs = r.json()["data"]["messages"]
private_notes = [m for m in msgs if m.get("role") == "private_note"]
check("GET /api/contacts -> private_note present", len(private_notes) >= 1)
check(
    "GET /api/contacts -> private_note status is null",
    private_notes and private_notes[-1].get("status") in (None, ""),
)

# Private notes are deletable by DB id (scope=me) without a msg_id -> kept, flagged revoked
r = client.post("/api/contacts/5511999990001/messages/delete",
                json={"db_id": _pn["_id"], "scope": "me"})
check("POST /messages/delete (private note, db_id) -> 200", r.status_code == 200)
r = client.get("/api/contacts/5511999990001")
_remaining = [m for m in r.json()["data"]["messages"] if m.get("_id") == _pn["_id"]]
check("POST /messages/delete (private note) -> row kept", len(_remaining) == 1)
check("POST /messages/delete (private note) -> flagged revoked", bool(_remaining and _remaining[0].get("revoked")))
check(
    "GET /api/contacts -> private_note content matches",
    private_notes and "VIP" in private_notes[-1]["content"],
)

# Empty payload
r = client.post("/api/contacts/5511999990001/private-message", json={"text": ""})
check("POST /private-message (empty) -> 400", r.status_code == 400)

# ═══════════════════════════════════════════════════════════════════
#  9. Contact send image
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Send Image")

# Create a fake PNG (1x1 pixel)
fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
r = client.post(
    "/api/contacts/5511999990001/send-image",
    files={"image": ("test.png", io.BytesIO(fake_png), "image/png")},
    data={"caption": "Test caption"},
)
check("POST /send-image -> 200", r.status_code == 200)
check("POST /send-image -> gowa called", mock_gowa_client.send_image.called)

# ═══════════════════════════════════════════════════════════════════
#  10. Contact send audio
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Send Audio")

fake_ogg = b"OggS" + b"\x00" * 100
r = client.post(
    "/api/contacts/5511999990001/send-audio",
    files={"audio": ("voice.ogg", io.BytesIO(fake_ogg), "audio/ogg")},
)
check("POST /send-audio -> 200", r.status_code == 200)
check("POST /send-audio -> gowa called", mock_gowa_client.send_audio.called)

# ═══════════════════════════════════════════════════════════════════
#  10b. Contact send document
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Send Document")

fake_pdf = b"%PDF-1.4\n" + b"\x00" * 100
r = client.post(
    "/api/contacts/5511999990001/send-document",
    files={"document": ("relatorio.pdf", io.BytesIO(fake_pdf), "application/pdf")},
    data={"caption": "Doc caption"},
)
check("POST /send-document -> 200", r.status_code == 200)
check("POST /send-document -> gowa called", mock_gowa_client.send_file.called)

# ═══════════════════════════════════════════════════════════════════
#  11. Contact presence
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Presence")

r = client.post("/api/contacts/5511999990001/presence", json={"action": "start"})
check("POST /presence -> 200", r.status_code == 200)
check("POST /presence -> gowa called", mock_gowa_client.send_chat_presence.called)

# ═══════════════════════════════════════════════════════════════════
#  12. Contact mark read
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Mark Read")

r = client.post("/api/contacts/5511999990002/read")
check("POST /read -> 200", r.status_code == 200)

# ═══════════════════════════════════════════════════════════════════
#  13. Contact toggle AI
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Toggle AI")

r = client.post("/api/contacts/5511999990001/toggle-ai", json={"enabled": False})
check("POST /toggle-ai -> 200", r.status_code == 200)
check("POST /toggle-ai -> disabled", r.json()["data"]["ai_enabled"] is False)

r = client.post("/api/contacts/5511999990001/toggle-ai", json={"enabled": True})
check("POST /toggle-ai -> re-enabled", r.json()["data"]["ai_enabled"] is True)

r = client.post("/api/contacts/5511999990001/toggle-ai", json={})
check("POST /toggle-ai (no field) -> 400", r.status_code == 400)

# ═══════════════════════════════════════════════════════════════════
#  14. Contact update info
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Update Info")

r = client.put("/api/contacts/5511999990001/info", json={
    "name": "Alice Updated",
    "email": "alice_new@test.com",
    "profession": "Senior Engineer",
    "company": "NewCo",
    "observations": ["VIP client", "Prefers morning calls"],
})
check("PUT /info -> 200", r.status_code == 200)
info = r.json()["data"]
check("PUT /info -> name updated", info.get("name") == "Alice Updated")
check("PUT /info -> email updated", info.get("email") == "alice_new@test.com")

# Verify persistence
r = client.get("/api/contacts/5511999990001")
data = r.json()["data"]
check("PUT /info -> persisted name", data["name"] == "Alice Updated")
check("PUT /info -> persisted observations", len(data.get("info", {}).get("observations", [])) == 2)

# ═══════════════════════════════════════════════════════════════════
#  15. Tags
# ═══════════════════════════════════════════════════════════════════
section("Tags")

r = client.get("/api/tags")
check("GET /api/tags -> 200", r.status_code == 200)
tags = r.json()["data"]
check("GET /api/tags -> is dict", isinstance(tags, dict))
check("GET /api/tags -> has vip", "vip" in tags)
check("GET /api/tags -> has lead", "lead" in tags)

# Create tag
r = client.post("/api/tags", json={"name": "hot", "color": "#ff6600"})
check("POST /api/tags -> 200", r.status_code == 200)
check("POST /api/tags -> created", r.json()["data"]["name"] == "hot")

# Duplicate tag
r = client.post("/api/tags", json={"name": "hot", "color": "#ff6600"})
check("POST /api/tags (dup) -> 400", r.status_code == 400)

# Update tag
r = client.put("/api/tags/hot", json={"name": "super_hot", "color": "#ff0066"})
check("PUT /api/tags/{name} -> 200", r.status_code == 200)
check("PUT /api/tags -> renamed", r.json()["data"]["name"] == "super_hot")

# Update non-existent
r = client.put("/api/tags/nonexist", json={"color": "#000"})
check("PUT /api/tags (404) -> 404", r.status_code == 404)

# Delete tag
r = client.delete("/api/tags/super_hot")
check("DELETE /api/tags -> 200", r.status_code == 200)

r = client.delete("/api/tags/super_hot")
check("DELETE /api/tags (404) -> 404", r.status_code == 404)

# Set contact tags
r = client.put("/api/contacts/5511999990001/tags", json={"tags": ["vip", "lead"]})
check("PUT /contacts/{phone}/tags -> 200", r.status_code == 200)
check("PUT /contacts/{phone}/tags -> set", set(r.json()["data"]["tags"]) == {"vip", "lead"})

# Non-existent contact (use different number not auto-created elsewhere)
r = client.put("/api/contacts/9999999999/tags", json={"tags": ["vip"]})
check("PUT /contacts/9999/tags -> 404", r.status_code == 404)

# ═══════════════════════════════════════════════════════════════════
#  16. Usage
# ═══════════════════════════════════════════════════════════════════
section("Usage")

r = client.get("/api/usage/summary")
check("GET /usage/summary -> 200", r.status_code == 200)
data = r.json()["data"]
check("GET /usage/summary -> has total_tokens", "total_tokens" in data)
check("GET /usage/summary -> tokens > 0", data.get("total_tokens", 0) > 0)

r = client.get("/api/usage/summary?period=24h")
check("GET /usage/summary?period=24h -> 200", r.status_code == 200)
check("GET /usage/summary -> has period_start", r.json()["data"].get("period_start") is not None)

r = client.get("/api/usage/by-contact")
check("GET /usage/by-contact -> 200", r.status_code == 200)
by_contact = r.json()["data"]
check("GET /usage/by-contact -> is list", isinstance(by_contact, list))
check("GET /usage/by-contact -> has entries", len(by_contact) >= 1)

r = client.get("/api/usage/contact/5511999990001")
check("GET /usage/contact/{phone} -> 200", r.status_code == 200)
detail = r.json()["data"]
check("GET /usage/contact -> is list", isinstance(detail, list))
check("GET /usage/contact -> has records", len(detail) >= 2)

r = client.get("/api/usage/contact/0000000000")
check("GET /usage/contact/0000 -> empty", r.json()["data"] == [])

# ═══════════════════════════════════════════════════════════════════
#  17. Logs
# ═══════════════════════════════════════════════════════════════════
section("Logs")

r = client.get("/api/logs")
check("GET /api/logs -> 200", r.status_code == 200)
check("GET /api/logs -> is list", isinstance(r.json()["data"], list))

r = client.get("/api/logs?limit=5")
check("GET /api/logs?limit=5 -> 200", r.status_code == 200)

r = client.delete("/api/logs")
check("DELETE /api/logs -> 200", r.status_code == 200)

# ═══════════════════════════════════════════════════════════════════
#  18. Webhook Payloads
# ═══════════════════════════════════════════════════════════════════
section("Webhook Payloads")

r = client.get("/api/webhook-payloads")
check("GET /api/webhook-payloads -> 200", r.status_code == 200)
check("GET /api/webhook-payloads -> is list", isinstance(r.json()["data"], list))

# ═══════════════════════════════════════════════════════════════════
#  19. Webhook (incoming message simulation)
# ═══════════════════════════════════════════════════════════════════
section("Webhook")

# Presence event
r = client.post("/api/webhook", json={
    "type": "chat_presence",
    "data": [{"from": "5511999990001@s.whatsapp.net", "state": "composing"}],
})
check("POST /webhook (presence) -> 200", r.status_code == 200)

# is_from_me echo (should be ignored)
r = client.post("/api/webhook", json={
    "body": "echo test",
    "from": "5511999990001@s.whatsapp.net",
    "id": "echo_001",
    "is_from_me": True,
})
check("POST /webhook (echo) -> 200", r.status_code == 200)

# message.ack event
r = client.post("/api/webhook", json={
    "type": "message.ack",
    "data": [{"id": "msg_001", "chat_jid": "5511999990002@s.whatsapp.net", "ack": 3}],
})
check("POST /webhook (ack) -> 200", r.status_code == 200)

# Reply-quote extraction from inbound payloads (GOWA nests this inconsistently).
from server.routes.webhook import _extract_reply_to as _ext_reply
check("reply extract: flat replied_id", _ext_reply({"replied_id": "Q1"}) == "Q1")
check("reply extract: nested message.replied_id",
      _ext_reply({"message": {"text": "oi", "id": "SELF", "replied_id": "Q2"}}) == "Q2")
check("reply extract: context_info.stanza_id",
      _ext_reply({"context_info": {"stanza_id": "Q3"}}) == "Q3")
check("reply extract: quoted_message object id",
      _ext_reply({"quoted_message": {"id": "Q4"}}) == "Q4")
check("reply extract: own id is NOT a reply", _ext_reply({"id": "SELF", "body": "oi"}) is None)
check("reply extract: quoted text is NOT a reply id",
      _ext_reply({"quoted_message": "texto", "body": "oi"}) is None)

# ═══════════════════════════════════════════════════════════════════
#  20. QR / WhatsApp
# ═══════════════════════════════════════════════════════════════════
section("WhatsApp / QR")

r = client.get("/api/qr")
check("GET /api/qr -> 204 (no qr)", r.status_code == 204)

r = client.post("/api/qr/refresh")
check("POST /api/qr/refresh -> 200", r.status_code == 200)

r = client.post("/api/whatsapp/reconnect")
check("POST /whatsapp/reconnect -> 200", r.status_code == 200)
check("POST /whatsapp/reconnect -> gowa called", mock_gowa_client.reconnect.called)

r = client.post("/api/whatsapp/logout")
check("POST /whatsapp/logout -> 200", r.status_code == 200)
check("POST /whatsapp/logout -> gowa called", mock_gowa_client.logout.called)

# ═══════════════════════════════════════════════════════════════════
#  20b. Setup Wizard
# ═══════════════════════════════════════════════════════════════════
section("Setup Wizard")


class _FakeTechifyResp:
    """Stands in for an httpx Response from the Techify provisioning route."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_async_client(resp):
    """Build a patch target mimicking httpx.AsyncClient as an async CM.

    Answers both verbs with the same body: ``GET /service_number`` (the
    provisioning target) and ``POST /request-apikey`` (the key polling).
    """
    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=resp)
    fake_client.get = AsyncMock(return_value=resp)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=fake_client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


# The provisioning target (number + phrase) comes from Techify's
# /service_number. Pinning it keeps this block off the network — otherwise the
# assertions below would depend on the endpoint being reachable from wherever
# the suite runs.
_service_number = _fake_async_client(_FakeTechifyResp(200, {
    "ok": True, "phone": "5599000000001", "message": "Frase publicada no endpoint"}))

# request-key: sends the provisioning WhatsApp message and arms polling
_send_calls_before = mock_gowa_client.send_message.call_count
with patch("server.routes.setup.httpx.AsyncClient", _service_number):
    r = client.post("/api/setup/request-key")
check("POST /api/setup/request-key -> 200", r.status_code == 200)
check("POST /api/setup/request-key -> returns number",
      r.json()["data"].get("number") == "5511999990001")
check("POST /api/setup/request-key -> WhatsApp message sent",
      mock_gowa_client.send_message.call_count == _send_calls_before + 1)
check("POST /api/setup/request-key -> sends the endpoint's pair (number + phrase)",
      mock_gowa_client.send_message.call_args[0][:2]
      == ("5599000000001", "Frase publicada no endpoint"),
      detail=str(mock_gowa_client.send_message.call_args))

# Both provisioning seams abort the send: an empty destination would fire the
# phrase at a number nobody chose, and an empty phrase would burn the single
# conversation opening WhatsApp grants with a brand-new contact.
from plugins import events as _bus  # noqa: E402

for _seam, _label in (("filter.provisioning.number", "sem destino"),
                      ("filter.provisioning.message", "sem frase")):
    _bus.register_filter("test_setup_abort", _seam, lambda ctx, value: None)
    _send_calls_before = mock_gowa_client.send_message.call_count
    with patch("server.routes.setup.httpx.AsyncClient", _service_number):
        r = client.post("/api/setup/request-key")
    _bus._filters.pop(_seam, None)
    check(f"POST /api/setup/request-key ({_label}) -> erro acionável",
          r.status_code == 400 and not r.json()["ok"] and r.json().get("error"),
          detail=f"{r.status_code} {r.json()}")
    check(f"POST /api/setup/request-key ({_label}) -> nada é enviado",
          mock_gowa_client.send_message.call_count == _send_calls_before)

# Re-arm the polling the abort cases refused to arm, so key-status can run.
with patch("server.routes.setup.httpx.AsyncClient", _service_number):
    client.post("/api/setup/request-key")

# key-status: account not ready yet
with patch("server.routes.setup.httpx.AsyncClient",
           _fake_async_client(_FakeTechifyResp(200, {"status": "pending"}))):
    r = client.get("/api/setup/key-status")
check("GET /api/setup/key-status (pending) -> 200", r.status_code == 200)
check("GET /api/setup/key-status -> pending", r.json()["data"]["status"] == "pending")

# key-status: key ready -> persisted to config
_provisioned_key = "sk-techify-provisioned-abcdef123456"
with patch("server.routes.setup.httpx.AsyncClient",
           _fake_async_client(_FakeTechifyResp(200, {
               "status": "ready", "api_key": _provisioned_key}))):
    r = client.get("/api/setup/key-status")
check("GET /api/setup/key-status (ready) -> 200", r.status_code == 200)
check("GET /api/setup/key-status -> ready", r.json()["data"]["status"] == "ready")
check("GET /api/setup/key-status -> key saved to config",
      config_repo.get("openrouter_api_key") == _provisioned_key)

# ═══════════════════════════════════════════════════════════════════
#  21. Sandbox
# ═══════════════════════════════════════════════════════════════════
section("Sandbox")

# sandbox/send requires a working LLM — mock it
with patch.object(agent_handler, "process_message") as mock_process:
    from agent.handler import ProcessResult
    mock_process.return_value = ProcessResult(reply="Resposta de teste", tool_calls=[])

    r = client.post("/api/sandbox/send", json={"phone": "sandbox_test", "message": "Oi"})
    check("POST /sandbox/send -> 200", r.status_code == 200)
    check("POST /sandbox/send -> has reply", "reply" in r.json().get("data", {}))
    check("POST /sandbox/send -> reply text", r.json()["data"]["reply"] == "Resposta de teste")

r = client.post("/api/sandbox/send", json={"phone": "", "message": "Oi"})
check("POST /sandbox/send (no phone) -> 400", r.status_code == 400)

r = client.post("/api/sandbox/send", json={"phone": "test", "message": ""})
check("POST /sandbox/send (no msg) -> 400", r.status_code == 400)

r = client.post("/api/sandbox/clear", json={"phone": "sandbox_test"})
check("POST /sandbox/clear -> 200", r.status_code == 200)

r = client.post("/api/sandbox/clear", json={})
check("POST /sandbox/clear (all) -> 200", r.status_code == 200)

# ═══════════════════════════════════════════════════════════════════
#  22. Frontend routes (SPA)
# ═══════════════════════════════════════════════════════════════════
section("Frontend SPA Routes")

for path in ["/", "/painel", "/sandbox", "/costs"]:
    r = client.get(path)
    check(f"GET {path} -> 200", r.status_code == 200)

# ═══════════════════════════════════════════════════════════════════
#  23. GOWA version / update
# ═══════════════════════════════════════════════════════════════════
section("GOWA — Version & Update")

from gowa import binary as _gowa_binary  # noqa: E402
from gowa import updater as _gowa_updater  # noqa: E402

# Sandbox the binary resolution: without this, POST /gowa/rollback below would
# uninstall the GOWA the developer actually has in storages/bin.
_gowa_fake_root = Path(tempfile.mkdtemp(prefix="whatsbot_test_gowa_"))
(_gowa_fake_root / "bin").mkdir()
(_gowa_fake_root / "storages").mkdir()
(_gowa_fake_root / "GOWA_VERSION").write_text("8.8.0\n")
_fake_binary = _gowa_fake_root / "bin" / _gowa_binary.binary_name()
_fake_binary.write_bytes(b"#!/bin/sh\nexit 0\n")
_fake_binary.chmod(0o755)
_orig_repo_root = _gowa_binary.repo_root
_gowa_binary.repo_root = lambda: _gowa_fake_root

# Never hit GitHub from the tests: stub the release lookup.
_FAKE_RELEASE = {
    "version": "9.1.0",
    "published_at": "2026-08-15T19:35:00Z",
    "notes": "fake",
    "url": "https://example.invalid/release",
    "cached": False,
    "rate_limited": False,
}
_orig_fetch = _gowa_updater.fetch_latest_release
_gowa_updater.fetch_latest_release = lambda force=False: dict(_FAKE_RELEASE)

r = client.get("/api/gowa/version")
check("GET /gowa/version -> 200", r.status_code == 200)
_gv = r.json().get("data", {})
check("GET /gowa/version -> installed_version", bool(_gv.get("installed_version")))
check("GET /gowa/version -> source", _gv.get("source") in ("bundled", "managed", "env"))
check("GET /gowa/version -> supported_range", _gv.get("supported_range") == _gowa_updater.GOWA_SUPPORTED_RANGE)

r = client.get("/api/gowa/update/check")
check("GET /gowa/update/check -> 200", r.status_code == 200)
_gc = r.json().get("data", {})
check("GET /gowa/update/check -> latest_version", _gc.get("latest_version") == "9.1.0")
check("GET /gowa/update/check -> update_available", _gc.get("update_available") is True)
check("GET /gowa/update/check -> latest_supported", _gc.get("latest_supported") is True)

# Out-of-range version must ask for confirmation instead of installing.
r = client.post("/api/gowa/update", json={"version": "10.5.0"})
check("POST /gowa/update (unsupported) -> 200", r.status_code == 200)
_gu = r.json().get("data", {})
check("POST /gowa/update (unsupported) -> requires_confirmation",
      _gu.get("requires_confirmation") is True)

# A malformed version must never reach the download URL builder.
r = client.post("/api/gowa/update", json={"version": "../../etc/passwd"})
check("POST /gowa/update (versão inválida) -> 400", r.status_code == 400)

# Rollback with nothing to restore.
r = client.post("/api/gowa/rollback")
check("POST /gowa/rollback (sem backup) -> 400", r.status_code == 400)

r = client.post("/api/gowa/skip-version", json={"version": "9.1.0"})
check("POST /gowa/skip-version -> 200", r.status_code == 200)
check("POST /gowa/skip-version -> persisted",
      client.get("/api/config").json()["data"].get("gowa_skipped_version") == "9.1.0")

r = client.post("/api/gowa/skip-version", json={"version": ""})
check("POST /gowa/skip-version (vazio) -> 400", r.status_code == 400)

# The auto-check toggle must survive PUT /api/config (allowlist regression).
client.put("/api/config", json={"gowa_auto_check_enabled": False})
check("PUT /config gowa_auto_check_enabled -> persisted",
      client.get("/api/config").json()["data"].get("gowa_auto_check_enabled") is False)
client.put("/api/config", json={"gowa_auto_check_enabled": True})

_gowa_updater.fetch_latest_release = _orig_fetch


# ═══════════════════════════════════════════════════════════════════
#  24. GOWA proxy
# ═══════════════════════════════════════════════════════════════════
section("GOWA — Proxy")

import socket as _socket  # noqa: E402
import threading as _threading  # noqa: E402

r = client.get("/api/gowa/proxy")
check("GET /gowa/proxy -> 200", r.status_code == 200)
_pp = r.json().get("data", {})
check("GET /gowa/proxy -> desativado por padrão", _pp.get("enabled") is False)
check("GET /gowa/proxy -> min_version", _pp.get("min_version") == "8.11.0")
check("GET /gowa/proxy -> schemes", "socks5" in (_pp.get("schemes") or []))
check("GET /gowa/proxy -> GOWA 8.8.0 não suporta proxy", _pp.get("supported") is False)

# Config inválida não é salva.
r = client.put("/api/gowa/proxy", json={"enabled": True, "mode": "fields", "host": ""})
check("PUT /gowa/proxy (host vazio) -> 400", r.status_code == 400)
r = client.put("/api/gowa/proxy", json={"enabled": True, "mode": "url", "url": "1.2.3.4:1080"})
check("PUT /gowa/proxy (URL sem esquema) -> 400", r.status_code == 400)

mock_gowa_manager.restart.reset_mock()
r = client.put("/api/gowa/proxy", json={
    "enabled": True, "mode": "fields", "scheme": "socks5",
    "host": "10.0.0.9", "port": 1080, "username": "user", "password": "segredo",
})
check("PUT /gowa/proxy -> 200", r.status_code == 200)
_pp = r.json().get("data", {})
check("PUT /gowa/proxy -> salvo", _pp.get("enabled") is True and _pp.get("host") == "10.0.0.9")
check("PUT /gowa/proxy -> senha nunca volta em claro", _pp.get("password") == "***")
check("PUT /gowa/proxy -> has_password", _pp.get("has_password") is True)
check("PUT /gowa/proxy -> URL efetiva mascarada",
      _pp.get("effective_url") == "socks5://user:***@10.0.0.9:1080", str(_pp.get("effective_url")))
check("PUT /gowa/proxy -> reiniciou o GOWA", mock_gowa_manager.restart.called)

from gowa import proxy as _gowa_proxy  # noqa: E402

check("proxy aplicado ao subprocesso",
      _gowa_proxy.current_url() == "socks5://user:segredo@10.0.0.9:1080", _gowa_proxy.current_url())

# Reenviar a senha mascarada preserva a senha salva e não reinicia à toa.
mock_gowa_manager.restart.reset_mock()
r = client.put("/api/gowa/proxy", json={
    "enabled": True, "mode": "fields", "scheme": "socks5",
    "host": "10.0.0.9", "port": 1080, "username": "user", "password": "***",
})
check("PUT /gowa/proxy (senha mascarada) -> senha preservada",
      _gowa_proxy.current_url() == "socks5://user:segredo@10.0.0.9:1080", _gowa_proxy.current_url())
check("PUT /gowa/proxy sem mudança -> não reinicia", not mock_gowa_manager.restart.called)

# Teste de conectividade contra um SOCKS5 falso local.
def _fake_socks5():
    srv = _socket.socket()
    srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(2)

    def serve():
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        try:
            conn.settimeout(5)
            _, count = conn.recv(2)
            conn.recv(count)
            conn.sendall(b"\x05\x00")
            head = conn.recv(4)
            if head and head[3] == 0x03:
                length = conn.recv(1)[0]
                conn.recv(length + 2)
            conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        except Exception:
            pass
        finally:
            conn.close()

    _threading.Thread(target=serve, daemon=True).start()
    return srv, srv.getsockname()[1]


_srv, _port = _fake_socks5()
r = client.post("/api/gowa/proxy/test", json={
    "enabled": True, "mode": "fields", "scheme": "socks5",
    "host": "127.0.0.1", "port": _port, "username": "", "password": "",
})
check("POST /gowa/proxy/test -> 200", r.status_code == 200)
_pt = r.json().get("data", {})
check("POST /gowa/proxy/test -> ok", _pt.get("ok") is True, str(_pt.get("message")))
_srv.close()

r = client.post("/api/gowa/proxy/test", json={"enabled": True, "mode": "url", "url": "ftp://x:1"})
check("POST /gowa/proxy/test (URL inválida) -> 400", r.status_code == 400)

# Desativar limpa o proxy do subprocesso.
mock_gowa_manager.restart.reset_mock()
r = client.put("/api/gowa/proxy", json={"enabled": False})
check("PUT /gowa/proxy (desativar) -> 200", r.status_code == 200)
check("desativado -> sem proxy no subprocesso", _gowa_proxy.current_url() == "",
      _gowa_proxy.current_url())
check("desativar reinicia o GOWA", mock_gowa_manager.restart.called)

# As chaves do proxy não são graváveis pelo PUT /api/config genérico.
client.put("/api/config", json={"gowa_proxy_enabled": True, "gowa_proxy_host": "6.6.6.6"})
check("PUT /config não escreve config de proxy",
      client.get("/api/gowa/proxy").json()["data"].get("enabled") is False)

_gowa_binary.repo_root = _orig_repo_root
shutil.rmtree(_gowa_fake_root, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
#  25. Auth with password
# ═══════════════════════════════════════════════════════════════════
section("Auth — With Password")

# Set a password
r = client.put("/api/config", json={"web_password": "mysecret123"})
check("SET password -> 200", r.status_code == 200)

# Now auth should be required
r = client.get("/api/auth/check")
check("GET /auth/check (no token) -> 401", r.status_code == 401)

# Login
r = client.post("/api/auth/login", json={"password": "mysecret123"})
check("POST /auth/login -> 200", r.status_code == 200)
token = r.json()["data"]["token"]
check("POST /auth/login -> has token", len(token) > 0)

# Check with token
r = client.get("/api/auth/check", headers={"Authorization": f"Bearer {token}"})
check("GET /auth/check (valid token) -> 200", r.status_code == 200)
check("GET /auth/check -> authenticated", r.json()["data"]["authenticated"] is True)

# Wrong password
r = client.post("/api/auth/login", json={"password": "wrong"})
check("POST /auth/login (wrong) -> 401", r.status_code == 401)

# API endpoint without auth (should be blocked)
r = client.get("/api/config")
check("GET /api/config (no auth) -> 401", r.status_code == 401)

# API endpoint with auth
r = client.get("/api/config", headers={"Authorization": f"Bearer {token}"})
check("GET /api/config (with auth) -> 200", r.status_code == 200)

# Webhook should be exempt from auth
r = client.post("/api/webhook", json={"type": "unknown"})
check("POST /webhook (auth exempt) -> 200", r.status_code == 200)

# Health should be exempt
r = client.get("/health")
check("GET /health (auth exempt) -> 200", r.status_code == 200)

# Remove password to not affect other tests
r = client.put("/api/config", json={"web_password": ""}, headers={"Authorization": f"Bearer {token}"})
check("REMOVE password -> 200", r.status_code == 200)

# ═══════════════════════════════════════════════════════════════════
#  Summary
# ═══════════════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print(f"  RESULTS: {passed} passed, {failed} failed")
print(f"{'='*60}")

if errors:
    print("\nFailed tests:")
    for e in errors:
        print(e)

# Cleanup temp dir
import shutil
try:
    shutil.rmtree(_tmpdir, ignore_errors=True)
except Exception:
    pass

sys.exit(1 if failed else 0)
