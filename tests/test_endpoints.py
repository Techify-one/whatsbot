"""Comprehensive endpoint tests for WhatsBot API.

Uses FastAPI TestClient with a real temporary SQLite database.
No external services needed (GOWA/OpenRouter are mocked).
"""

import io
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

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
mock_gowa_client.send_chat_presence = MagicMock(return_value=None)
mock_gowa_client.mark_as_read = MagicMock(return_value=None)
mock_gowa_client.reconnect = MagicMock(return_value=None)
mock_gowa_client.logout = MagicMock(return_value=None)

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

r = client.put("/api/config", json={"auto_reply": False})
check("PUT /api/config -> 200", r.status_code == 200)
check("PUT /api/config -> saved", r.json()["data"]["message"] == "Configurações salvas!")

r = client.get("/api/config")
check("PUT /api/config -> auto_reply persisted", r.json()["data"]["auto_reply"] is False)

# Restore
client.put("/api/config", json={"auto_reply": True})

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

# Search
r = client.get("/api/contacts?q=Alice")
check("GET /api/contacts?q=Alice -> finds Alice", len(r.json()["data"]) >= 1)

r = client.get("/api/contacts?q=xyznotexist")
check("GET /api/contacts?q=xyz -> empty", len(r.json()["data"]) == 0)

# Archived
r = client.get("/api/contacts?archived=true")
check("GET /api/contacts?archived=true -> has archived", len(r.json()["data"]) >= 1)
archived_names = [c.get("name", "") for c in r.json()["data"]]
check("GET /api/contacts?archived=true -> Bob is archived", any("Bob" in n for n in archived_names))

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

# ═══════════════════════════════════════════════════════════════════
#  8. Contact retry send
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Retry Send")

r = client.post("/api/contacts/5511999990001/retry-send", json={"message": "Retry msg"})
check("POST /retry-send -> 200", r.status_code == 200)

r = client.post("/api/contacts/5511999990001/retry-send", json={"message": ""})
check("POST /retry-send (empty) -> 400", r.status_code == 400)

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
check("POST /private-message -> saved", "salva" in r.json()["data"]["message"].lower())
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

for path in ["/", "/dashboard", "/sandbox", "/costs"]:
    r = client.get(path)
    check(f"GET {path} -> 200", r.status_code == 200)

# ═══════════════════════════════════════════════════════════════════
#  23. Auth with password
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
