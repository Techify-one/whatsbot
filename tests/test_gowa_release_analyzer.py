"""Offline tests for the GOWA release compatibility classifier."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gowa import release_analyzer  # noqa: E402

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


def assess(notes: str, settings=None, releases=None):
    settings = settings if settings is not None else {}
    releases = releases or [{
        "version": "9.2.2", "published_at": "2026-08-23", "notes": notes,
    }]
    return release_analyzer.assess_update(
        settings, installed="9.2.1", latest="9.2.2",
        releases=releases, commits=[],
    )


print("\nGOWA release analyzer")

# UI/auth/API work must stay available in the manual updater without opening a
# proactive modal.
result = assess(
    "Restores Basic Auth for the dashboard and scopes OAuth middleware to MCP."
)
check("UI/auth release is not recommended", not result["whatsapp_update_recommended"])
check("no-key fallback is identified", result["assessment_source"] == "unavailable")

# A generic dependency bump is deliberately not enough: GOWA does this in most
# releases, which was the source of noisy alerts.
result = assess("Dependency Updates: go.mau.fi/whatsmeow updated to latest.")
check("bare whatsmeow bump is not a hard signal", not result["whatsapp_update_recommended"])

result = release_analyzer.assess_update(
    {}, installed="9.1.0", latest="9.2.2",
    releases=[{"version": "9.2.2", "notes": "Dashboard authentication fix."}],
    commits=["[whatsmeow] proto: update to v1045649367"],
)
check("upstream whatsmeow proto update is recommended",
      result["whatsapp_update_recommended"])

# Explicit protocol compatibility remains protected even without an API key.
result = assess(
    "WhatsApp Protocol Update: latest WhatsApp Web protocol support and compatibility."
)
check("explicit protocol update is recommended", result["whatsapp_update_recommended"])
check("protocol update has evidence", bool(result["update_evidence"]))
check("protocol update is high risk", result["update_risk_level"] == "high")

# A critical intermediate release must not disappear just because the latest
# patch itself is only an unrelated UI fix.
result = assess("", releases=[
    {"version": "9.2.1", "notes": "Fix privacy token handling for WhatsApp error 463."},
    {"version": "9.2.2", "notes": "Restore Basic Auth prompt in the dashboard."},
])
check("critical intermediate release is recommended", result["whatsapp_update_recommended"])
check("all interval versions are recorded", result["assessed_versions"] == ["9.2.1", "9.2.2"])

# LLM can recognize a relevant case not covered by the hard rules. Its response
# is cached, so a second daily check does not call it again.
original_call = release_analyzer._call_llm
calls = []


def fake_llm(*_args, **_kwargs):
    calls.append(1)
    return {
        "recommended": True,
        "risk": "medium",
        "reason": "Mudança no estado da sessão usado pelo WhatsApp.",
        "evidence": ["session state compatibility"],
    }


release_analyzer._call_llm = fake_llm
settings = {"openrouter_api_key": "test", "model": "test/model"}
first = assess("Adjust device state synchronization.", settings=settings)
second = assess("Adjust device state synchronization.", settings=settings)
check("LLM recommendation is honored", first["whatsapp_update_recommended"])
check("assessment is cached", len(calls) == 1 and second.get("assessment_cached") is True)

# A broken LLM must not suppress a deterministic compatibility signal.
release_analyzer._call_llm = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("offline"))
result = assess(
    "Decrypts SecretEncryptedMessage envelopes from newer WhatsApp clients.",
    settings={"openrouter_api_key": "test", "model": "test/model"},
)
check("rules survive LLM failure", result["whatsapp_update_recommended"])
check("LLM failure is reported", bool(result["assessment_error"]))

release_analyzer._call_llm = original_call

print(f"\nRESULTS: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
