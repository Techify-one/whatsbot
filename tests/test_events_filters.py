"""Tests for the extended plugin event/filter surface.

Covers:
- Filter chaining + priority + ``None`` short-circuit
- ``emit_with_filter`` honoring ``filter.event.before_emit`` (block, rewrite, lifecycle bypass)
- ``_extract_media`` parsing for every new media type
- Filter helpers used by the transcription pipeline

Run standalone: ``python tests/test_events_filters.py``
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


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


# ─── 1. Bus internals — emit_with_filter / apply_filter ─────────────

from plugins import events as bus  # noqa: E402


def _make_loop_in_thread() -> asyncio.AbstractEventLoop:
    """Spin a real asyncio loop in a background thread (mimics server)."""
    loop = asyncio.new_event_loop()
    started = threading.Event()

    def _run():
        asyncio.set_event_loop(loop)
        started.set()
        loop.run_forever()

    threading.Thread(target=_run, daemon=True).start()
    started.wait()
    return loop


def _drain(loop: asyncio.AbstractEventLoop, delay: float = 0.05) -> None:
    """Wait until the event loop has flushed scheduled tasks."""
    fut = asyncio.run_coroutine_threadsafe(asyncio.sleep(delay), loop)
    fut.result(timeout=2.0)


def _await(coro, loop):
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=5.0)


_loop = _make_loop_in_thread()
bus.set_runtime(_loop, agent_handler=None)


print("\n── Bus internals ──")


# 1.1 filter chain + priority order
bus.reset()
order: list[str] = []


def f_low(ctx, value):
    order.append(f"low:{value}")
    return value + "+L"


def f_high(ctx, value):
    order.append(f"high:{value}")
    return value + "+H"


bus.register_filter("plg_a", "filter.test.chain", f_high, priority=200)
bus.register_filter("plg_b", "filter.test.chain", f_low, priority=10)

result = _await(bus.apply_filter("filter.test.chain", "x"), _loop)
check("priority asc executes low before high",
      order == ["low:x", "high:x+L"], detail=str(order))
check("filter chain composes values", result == "x+L+H", detail=result)


# 1.2 None aborts
bus.reset()
calls: list[str] = []


def f_abort(ctx, value):
    calls.append("abort")
    return None


def f_after(ctx, value):
    calls.append("after")  # should not run
    return value


bus.register_filter("p1", "filter.test.abort", f_abort, priority=10)
bus.register_filter("p2", "filter.test.abort", f_after, priority=20)
out = _await(bus.apply_filter("filter.test.abort", "v"), _loop)
check("None short-circuits chain", out is None)
check("downstream filter NOT invoked after None",
      calls == ["abort"], detail=str(calls))


# 1.3 emit_with_filter — block + rewrite + lifecycle bypass
bus.reset()
seen: list[dict] = []


def handler(ctx, payload):
    seen.append(payload)


bus.register("p1", "message.received", handler)

# rewrite payload
def rewrite_filter(ctx, payload):
    payload = dict(payload)
    payload["rewritten"] = True
    return payload


bus.register_filter("p2", "filter.event.before_emit", rewrite_filter)

_await(bus.emit_with_filter("message.received", {"phone": "1"}), _loop)
_drain(_loop)
check("emit_with_filter passes payload through filter",
      len(seen) == 1 and seen[0].get("rewritten") is True,
      detail=str(seen))


# block emit
bus.reset()
seen.clear()
bus.register("p1", "message.reaction", handler)


def block_filter(ctx, payload):
    return None


bus.register_filter("p2", "filter.event.before_emit", block_filter)
_await(bus.emit_with_filter("message.reaction", {"phone": "1"}), _loop)
_drain(_loop)
check("emit_with_filter respects None (no dispatch)", seen == [])


# lifecycle bypass
bus.reset()
seen.clear()
bus.register("p1", "plugin.enabled", handler)


def hostile_filter(ctx, payload):
    seen.append({"blocked_lifecycle_attempt": True})
    return None


bus.register_filter("p2", "filter.event.before_emit", hostile_filter)
_await(bus.emit_with_filter("plugin.enabled", {"plugin_id": "x"}), _loop)
_drain(_loop)
check("lifecycle events bypass filter.event.before_emit",
      len(seen) == 1 and seen[0].get("plugin_id") == "x",
      detail=str(seen))


# ─── 2. _extract_media — every new media type ────────────────────────

print("\n── _extract_media ──")
from server.routes.webhook import _extract_media  # noqa: E402

# 2.1 video w/ caption
r = _extract_media({"video": {"path": "v.mp4", "caption": "olha isso",
                              "duration": 1500}},
                   is_from_me=False, existing_text="")
check("video: media_type", r["media_type"] == "video")
check("video: media_path", r["media_path"] == "v.mp4")
check("video: caption becomes text", r["text"] == "olha isso")
check("video: duration in extras",
      r["media_extras"] and r["media_extras"].get("duration_ms") == 1500,
      detail=str(r["media_extras"]))

# 2.2 sticker
r = _extract_media({"sticker": {"path": "s.webp", "is_animated": True}},
                   is_from_me=False, existing_text="")
check("sticker: media_type", r["media_type"] == "sticker")
check("sticker: placeholder text", r["text"] == "[Sticker]")
check("sticker: is_animated in extras",
      r["media_extras"] and r["media_extras"].get("is_animated") is True)

# 2.3 video_note treated as audio
r = _extract_media({"video_note": "voice.ogg"},
                   is_from_me=False, existing_text="")
check("video_note normalises to audio", r["media_type"] == "audio")
check("video_note exposes audio_path",
      r["audio_path"] == "voice.ogg")

# 2.4 location
r = _extract_media({"location": {"latitude": -23.5, "longitude": -46.6,
                                  "name": "Av Paulista"}},
                   is_from_me=False, existing_text="")
check("location: media_type", r["media_type"] == "location")
check("location: media_path geo:", r["media_path"] == "geo:-23.5,-46.6")
check("location: name in text", "Av Paulista" in r["text"])
check("location: lat/lng in extras",
      r["media_extras"].get("lat") == -23.5)

# 2.5 live_location
r = _extract_media({"live_location": {"latitude": 1.0, "longitude": 2.0}},
                   is_from_me=False, existing_text="")
check("live_location: media_type", r["media_type"] == "live_location")
check("live_location: placeholder", r["text"] == "[Localização ao vivo]")

# 2.6 poll
r = _extract_media({"poll": {"name": "Pizza?", "options": [
    {"name": "Sim"}, {"name": "Não"}]}},
    is_from_me=False, existing_text="")
check("poll: media_type", r["media_type"] == "poll")
check("poll: name in text", "Pizza?" in r["text"])
check("poll: options array",
      r["media_extras"] and r["media_extras"].get("options") == ["Sim", "Não"])

# 2.7 buttons_response → interactive
r = _extract_media({"buttons_response": {"title": "Aceitar",
                                          "button_id": "btn_ok"}},
                   is_from_me=False, existing_text="")
check("buttons_response: media_type", r["media_type"] == "interactive")
check("buttons_response: button_id in extras",
      r["media_extras"].get("button_id") == "btn_ok")

# 2.8 order
r = _extract_media({"order": {"item_count": 3, "total": 99}},
                   is_from_me=False, existing_text="")
check("order: media_type", r["media_type"] == "order")
check("order: item count in text", "3 item" in r["text"])

# 2.9 product
r = _extract_media({"product": {"product_id": "p1", "title": "Tênis"}},
                   is_from_me=False, existing_text="")
check("product: media_type", r["media_type"] == "product")

# 2.10 single contact (vCard)
r = _extract_media({"contact": {"displayName": "João",
                                 "phone_number": "5511999999"}},
                   is_from_me=False, existing_text="")
check("contact: media_type", r["media_type"] == "contact")
check("contact: vCard text composed",
      "João" in r["text"] and "5511999999" in r["text"])

# 2.11 contacts array
r = _extract_media({"contacts_array": [
    {"displayName": "A"}, {"displayName": "B"}]},
    is_from_me=False, existing_text="")
check("contacts_array: media_type", r["media_type"] == "contacts")
check("contacts_array: extras carries list",
      len(r["media_extras"]["contacts"]) == 2)

# 2.12 audio with no text → placeholder
r = _extract_media({"audio": "x.ogg"}, is_from_me=False, existing_text="")
check("audio incoming placeholder", r["text"] == "[Áudio recebido]")
r = _extract_media({"audio": "x.ogg"}, is_from_me=True, existing_text="")
check("audio outgoing placeholder", r["text"] == "[Áudio enviado]")

# 2.13 empty payload
r = _extract_media({}, is_from_me=False, existing_text="")
check("empty payload returns media_type None",
      r["media_type"] is None)


# ─── 3. KNOWN_EVENTS includes the new names ──────────────────────────
print("\n── KNOWN_EVENTS ──")
for ev in ("message.saved", "contact.untagged",
           "execution.started", "execution.ended"):
    check(f"KNOWN_EVENTS contains {ev}", ev in bus.KNOWN_EVENTS)


# ─── 4. emit_with_filter_sync from a worker thread ──────────────────
print("\n── sync helper ──")
bus.reset()
got: list[dict] = []


def hh(ctx, payload):
    got.append(payload)


bus.register("p1", "tool.before", hh)


def worker():
    bus.emit_with_filter_sync("tool.before", {"phone": "X"})


threading.Thread(target=worker).start()
_drain(_loop, 0.1)
check("emit_with_filter_sync dispatches from worker thread",
      len(got) == 1 and got[0].get("phone") == "X", detail=str(got))


# ─── Wrap up ─────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"  RESULTS: {passed} passed, {failed} failed")
print("=" * 60)

# Stop the loop cleanly
_loop.call_soon_threadsafe(_loop.stop)

if failed:
    sys.exit(1)
