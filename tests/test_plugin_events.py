"""Unit tests for the plugin event bus and filter pipeline.

Tests are deliberately isolated from the full FastAPI app — they exercise
``plugins.events`` directly. The bigger integration story (webhook → emit →
plugin handler runs) lives in test_endpoints.py if needed.

Run with:
    python tests/test_plugin_events.py
"""

import asyncio
import logging
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from plugins.events import (
    set_runtime,
    reset,
    register,
    register_filter,
    register_plugin_events,
    register_plugin_filters,
    emit,
    apply_filter,
    apply_filter_sync,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("test_plugin_events")


passed = 0
failed = 0
errors: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        msg = f"  FAIL {name}" + (f" -- {detail}" if detail else "")
        print(msg)
        errors.append(msg)


def section(title: str) -> None:
    print(f"\n{'-' * 60}\n  {title}\n{'-' * 60}")


async def _wait_for(condition, timeout: float = 1.0, interval: float = 0.01) -> bool:
    """Poll a condition (sync callable returning bool) until it's true or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        await asyncio.sleep(interval)
    return False


# ─── Test 1: emit dispatches to a specific subscriber ────────────────────


async def test_emit_basic():
    reset()
    received: list[dict] = []

    def handler(ctx, payload):
        received.append({"event": ctx.event_name, "payload": dict(payload)})

    register("plugin_a", "message.received", handler)
    emit("message.received", {"phone": "5511", "text": "hi"})
    ok = await _wait_for(lambda: len(received) == 1)
    check("emit dispatches to specific subscriber", ok)
    check("ctx.event_name matches", received and received[0]["event"] == "message.received")
    check("payload is delivered intact",
          received and received[0]["payload"] == {"phone": "5511", "text": "hi"})


# ─── Test 2: wildcard subscriber receives every event ───────────────────


async def test_wildcard():
    reset()
    received: list[str] = []

    def all_events(ctx, payload):
        received.append(ctx.event_name)

    register("logger", "*", all_events)
    emit("message.received", {"phone": "1"})
    emit("presence.changed", {"phone": "1", "state": "composing"})
    emit("llm.after", {"phone": "1", "reply": "ok"})
    ok = await _wait_for(lambda: len(received) >= 3)
    check("wildcard receives every event", ok, f"got {received!r}")
    check("wildcard sees original event name",
          set(received) == {"message.received", "presence.changed", "llm.after"})


# ─── Test 3: message.any alias receives both directions with `direction` ─


async def test_message_any_alias():
    reset()
    received: list[dict] = []

    def on_any(ctx, payload):
        received.append({"event": ctx.event_name, "direction": payload.get("direction"),
                         "phone": payload.get("phone")})

    register("any_plugin", "message.any", on_any)
    emit("message.received", {"phone": "111"})
    emit("message.sent", {"phone": "222", "source": "ai"})
    emit("presence.changed", {"phone": "333"})  # should NOT trigger message.any
    ok = await _wait_for(lambda: len(received) == 2)
    check("message.any fires for received + sent only", ok, f"got {received!r}")
    if received:
        dirs = {r["direction"] for r in received}
        check("direction='in' added on message.received", "in" in dirs)
        check("direction='out' added on message.sent", "out" in dirs)


# ─── Test 4: emit() with dispatch-only key is a no-op ───────────────────


async def test_dispatch_only_emit_blocked():
    reset()
    received: list[str] = []
    register("p", "*", lambda ctx, p: received.append(ctx.event_name))
    emit("*", {"x": 1})
    emit("message.any", {"x": 2})
    await asyncio.sleep(0.05)
    check("emit('*') is ignored (dispatch-only)", received == [],
          f"got {received!r}")


# ─── Test 5: exception in one handler does not block others ─────────────


async def test_handler_exception_isolated():
    reset()
    received: list[str] = []

    def bad_handler(ctx, payload):
        raise RuntimeError("boom")

    def good_handler(ctx, payload):
        received.append("ok")

    register("bad", "message.received", bad_handler)
    register("good", "message.received", good_handler)
    emit("message.received", {"phone": "1"})
    ok = await _wait_for(lambda: received == ["ok"])
    check("good handler runs even if a sibling raises", ok)


# ─── Test 6: sync handler runs in a worker thread, not the loop ─────────


async def test_sync_runs_off_loop():
    reset()
    seen: dict = {}
    main_loop = asyncio.get_running_loop()

    def sync_handler(ctx, payload):
        try:
            in_loop = asyncio.get_running_loop()
        except RuntimeError:
            in_loop = None
        seen["thread"] = threading.get_ident()
        seen["has_loop"] = in_loop is not None
        seen["is_main_loop"] = in_loop is main_loop

    register("sync_p", "message.received", sync_handler)
    main_thread = threading.get_ident()
    emit("message.received", {"phone": "x"})
    ok = await _wait_for(lambda: "thread" in seen)
    check("sync handler executed", ok)
    if "thread" in seen:
        check("sync handler ran in a different thread than the loop",
              seen["thread"] != main_thread)


# ─── Test 7: async handler runs awaited on the loop ─────────────────────


async def test_async_handler_awaited():
    reset()
    seen: dict = {}
    main_loop = asyncio.get_running_loop()

    async def async_handler(ctx, payload):
        seen["loop_is_main"] = asyncio.get_running_loop() is main_loop
        seen["plugin_id"] = ctx.plugin_id

    register("async_p", "message.received", async_handler)
    emit("message.received", {"phone": "x"})
    ok = await _wait_for(lambda: "loop_is_main" in seen)
    check("async handler ran on the main loop", ok and seen.get("loop_is_main") is True)
    check("ctx.plugin_id propagated", seen.get("plugin_id") == "async_p")


# ─── Test 8: register_plugin_events bulk + invalid entries skipped ──────


async def test_bulk_register_skips_invalid():
    reset()
    received: list[str] = []

    def ok(ctx, p): received.append(ctx.event_name)

    register_plugin_events("p", {
        "message.received": ok,
        "presence.changed": 12345,  # invalid (not callable) — should warn + skip
        "llm.after": ok,
    })
    emit("message.received", {})
    emit("presence.changed", {})
    emit("llm.after", {})
    ok_evt = await _wait_for(lambda: sorted(received) == ["llm.after", "message.received"])
    check("bulk register accepts callables and skips non-callables", ok_evt,
          f"got {sorted(received)!r}")


# ─── Test 9: apply_filter chains values in priority order ───────────────


async def test_filter_chain_priority():
    reset()

    def add_a(ctx, value): return value + "A"
    def add_b(ctx, value): return value + "B"
    def add_c(ctx, value): return value + "C"

    # Priorities: lower runs earlier. Expect C → A → B → "_ABC" (start) wait —
    # actually: start "" → C runs (priority 1) → "C" → A runs (priority 50) → "CA"
    # → B runs (priority 100) → "CAB"
    register_filter("p1", "filter.reply.part", add_a, priority=50)
    register_filter("p2", "filter.reply.part", add_b, priority=100)
    register_filter("p3", "filter.reply.part", add_c, priority=1)
    out = await apply_filter("filter.reply.part", "")
    check("filter chain respects priority", out == "CAB", f"got {out!r}")


# ─── Test 10: apply_filter returning None aborts the chain ──────────────


async def test_filter_none_aborts():
    reset()
    later_ran = {"v": False}

    def veto(ctx, value): return None

    def later(ctx, value):
        later_ran["v"] = True
        return value + "!"

    register_filter("veto_p", "filter.reply.part", veto, priority=10)
    register_filter("later_p", "filter.reply.part", later, priority=100)
    out = await apply_filter("filter.reply.part", "hello")
    check("None from a filter returns None to caller", out is None)
    check("filters after the veto do not run", later_ran["v"] is False)


# ─── Test 11: filter exception is swallowed, value passes through ───────


async def test_filter_exception_passthrough():
    reset()

    def explode(ctx, value): raise RuntimeError("boom")

    def append(ctx, value): return value + "!"

    register_filter("boom_p", "filter.reply.part", explode, priority=10)
    register_filter("ok_p", "filter.reply.part", append, priority=100)
    out = await apply_filter("filter.reply.part", "hello")
    check("filter exception is isolated and chain continues", out == "hello!")


# ─── Test 12: register_plugin_filters with (fn, priority) tuples ────────


async def test_plugin_filter_priority_tuple():
    reset()

    def first(ctx, v): return v + "1"
    def second(ctx, v): return v + "2"

    register_plugin_filters("p", {
        "filter.reply.part": (second, 100),
    })
    register_plugin_filters("p2", {
        "filter.reply.part": (first, 50),
    })
    out = await apply_filter("filter.reply.part", "")
    check("FILTERS dict accepts (fn, priority) tuples", out == "12", f"got {out!r}")


# ─── Test 13: apply_filter_sync detects loop-thread and falls back ──────


async def test_apply_filter_sync_on_loop():
    reset()
    register_filter("p", "filter.x", lambda c, v: v + "X")
    # Calling apply_filter_sync from within the event loop should NOT deadlock
    # — it must return the value unchanged (with a debug log).
    out = apply_filter_sync("filter.x", "in", timeout=0.5)
    check("apply_filter_sync from loop thread returns value unchanged", out == "in")


# ─── Test 14: apply_filter_sync from a worker thread DOES apply ─────────


async def test_apply_filter_sync_worker():
    reset()
    register_filter("p", "filter.x", lambda c, v: v + "!")
    out = await asyncio.to_thread(apply_filter_sync, "filter.x", "hi", None, 1.0)
    check("apply_filter_sync from worker thread runs the filter", out == "hi!")


# ─── Test 15: KNOWN_EVENTS membership ───────────────────────────────────


async def test_known_events_coverage():
    from plugins.events import KNOWN_EVENTS
    must_have = {
        "message.received", "message.sent",
        "message.reaction", "message.edited", "message.revoked", "message.deleted",
        "presence.changed", "receipt.changed",
        "group.participants_changed", "group.joined",
        "call.received", "newsletter.event", "chat.archived",
        "connection.changed",
        "llm.before", "llm.after",
        "tool.before", "tool.after",
        "plugin.loaded", "plugin.enabled", "plugin.disabled",
        "plugin.settings.changed",
        "contact.updated", "contact.ai_toggled",
        "contact.tagged", "contact.untagged",
        "tag.created", "tag.updated", "tag.deleted",
        "config.changed", "tool_override.changed",
        "app.startup", "app.shutdown",
    }
    missing = must_have - KNOWN_EVENTS
    check("KNOWN_EVENTS covers every documented event", not missing,
          f"missing: {sorted(missing)!r}")


# ─── Runner ─────────────────────────────────────────────────────────────


async def main():
    # Wire the bus to the current loop. agent_handler is None for these unit tests.
    set_runtime(asyncio.get_running_loop(), None)

    section("emit / handler basics")
    await test_emit_basic()
    await test_wildcard()
    await test_message_any_alias()
    await test_dispatch_only_emit_blocked()

    section("handler isolation")
    await test_handler_exception_isolated()
    await test_sync_runs_off_loop()
    await test_async_handler_awaited()
    await test_bulk_register_skips_invalid()

    section("filter chain")
    await test_filter_chain_priority()
    await test_filter_none_aborts()
    await test_filter_exception_passthrough()
    await test_plugin_filter_priority_tuple()

    section("apply_filter_sync")
    await test_apply_filter_sync_on_loop()
    await test_apply_filter_sync_worker()

    section("taxonomy")
    await test_known_events_coverage()

    print("\n" + "=" * 60)
    print(f"  Results: {passed} passed, {failed} failed")
    print("=" * 60)
    if errors:
        for e in errors:
            print(e)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
