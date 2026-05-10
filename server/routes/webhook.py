"""Webhook endpoint — receives real-time messages from GOWA."""

import asyncio
import json
import logging
import random
import re
import time
import uuid

from gowa.client import GOWASendError, extract_msg_id

from db.repositories import contact_repo, message_repo
from server.execution import astart_execution, aend_execution, atrack_step, prune_executions
from server.helpers import _ok, parse_split_reply

logger = logging.getLogger(__name__)


def register_routes(app, deps):
    agent_handler = deps.agent_handler
    gowa_client = deps.gowa_client
    ws_manager = deps.ws_manager
    state = deps.state
    settings = deps.settings

    # ── Group Mention Helpers ──────────────────────────────────────

    def _is_bot_mentioned(text: str, data: dict) -> bool:
        """Check if the bot is mentioned in a group message."""
        if not text:
            return False
        text_lower = text.lower()
        # Check @phone mention
        bot_phone = state.bot_phone
        if bot_phone and f"@{bot_phone}" in text:
            return True
        # Check @name mention (case-insensitive)
        bot_name = state.bot_name
        if bot_name and f"@{bot_name.lower()}" in text_lower:
            return True
        # Check mentioned_jids from GOWA payload (if present)
        mentioned = data.get("mentioned_jids", data.get("mentioned", []))
        if mentioned and bot_phone:
            for jid in mentioned:
                if bot_phone in str(jid):
                    return True
        return False

    def _strip_bot_mention(text: str) -> str:
        """Remove bot @mention from message text."""
        bot_phone = state.bot_phone
        bot_name = state.bot_name
        if bot_phone:
            text = text.replace(f"@{bot_phone}", "").strip()
        if bot_name:
            text = re.sub(rf"@{re.escape(bot_name)}", "", text, flags=re.IGNORECASE).strip()
        return text

    # ── Reply Splitting & Sending ─────────────────────────────────

    async def _send_reply(phone: str, reply: str):
        """Send reply (possibly split into multiple parts) and broadcast."""
        split_enabled = settings.get("split_messages", True)

        if split_enabled:
            parts = parse_split_reply(reply)
        else:
            parts = [reply]

        # Initial response delay (simulates typing)
        delay_min = settings.get("response_delay_min", 1.0)
        delay_max = settings.get("response_delay_max", 3.0)
        await asyncio.sleep(random.uniform(delay_min, delay_max))

        sent_parts = []  # collect (part_text, msg_id) for saving after send
        for i, part in enumerate(parts):
            if i > 0:
                # Inter-message delay with ±0.5s variation
                base_delay = settings.get("split_message_delay", 2.0)
                if base_delay > 0:
                    await asyncio.sleep(base_delay + random.uniform(-0.5, 0.5))
                # Re-send typing indicator between parts
                try:
                    await asyncio.to_thread(gowa_client.send_chat_presence, phone)
                except Exception:
                    pass

            # Track for echo-back filtering
            sent_key = f"{phone}:{part[:120]}"
            state.recently_sent[sent_key] = time.time()

            send_result = None
            try:
                send_result = await asyncio.to_thread(gowa_client.send_message, phone, part)
                await atrack_step("gowa_send", {"phone": phone, "part": i + 1, "total_parts": len(parts)})
            except GOWASendError as e:
                logger.error("[Batch] Send failed for %s (part %d/%d): %s", phone, i + 1, len(parts), e)
                await atrack_step("gowa_send", {
                    "phone": phone, "part": i + 1, "error": str(e),
                }, status="error")
                await asyncio.to_thread(gowa_client.stop_chat_presence, phone)
                await ws_manager.broadcast("new_message", {
                    "phone": phone,
                    "message": {"role": "error", "content": f"Falha ao enviar: {e}", "ts": time.time()},
                })
                return

            part_msg_id = extract_msg_id(send_result)
            sent_parts.append((part, part_msg_id))

            # Broadcast each part to frontend individually
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "message": {"role": "assistant", "content": part, "ts": time.time(),
                            "status": "sent", "msg_id": part_msg_id},
            })

        # Save each part as a separate message to preserve split across page refresh
        for part, part_msg_id in sent_parts:
            try:
                await asyncio.to_thread(agent_handler.save_assistant_message, phone, part,
                                        msg_id=part_msg_id, status="sent")
                # Increment unread AI count (operator hasn't seen this reply yet)
                contact = agent_handler._contacts.get(phone)
                if contact:
                    await asyncio.to_thread(contact.increment_unread_ai)
            except Exception as e:
                logger.error("[Batch] Failed to save reply for %s: %s", phone, e)

        await asyncio.to_thread(gowa_client.stop_chat_presence, phone)
        state.msg_count += 1
        full_reply = "\n".join(parts)
        await atrack_step("response_sent", {
            "phone": phone,
            "parts": len(parts),
            "reply_preview": full_reply[:200],
        })
        logger.info("[Batch] Replied to %s (%d parts): %s", phone, len(parts), full_reply[:80])

        await ws_manager.broadcast("status", {
            "connected": state.connected,
            "msg_count": state.msg_count,
            "auto_reply_running": state.auto_reply_running,
            "bot_phone": state.bot_phone,
            "bot_name": state.bot_name,
        })

    async def _broadcast_tool_calls(phone: str, tool_calls: list[dict],
                                    contact_info: dict | None = None):
        """Broadcast private messages for each tool call executed by the LLM."""
        contact = agent_handler._get_contact(phone)
        for tc in tool_calls:
            tool_name = tc.get("tool", "unknown")
            args = tc.get("args", {})
            # Format: tool name + each arg on its own line
            lines = [f"\U0001f527 {tool_name}"]
            for key, value in args.items():
                lines.append(f"{key}: {value}")
            content = "\n".join(lines)

            contact.add_message("tool_call", content)
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "message": {
                    "role": "tool_call",
                    "content": content,
                    "ts": time.time(),
                },
            })

        # Broadcast updated contact info so the frontend refreshes name/details
        if contact_info:
            logger.info("[ToolCall] Broadcasting contact_info_updated for %s: %s", phone, contact_info)
            await ws_manager.broadcast("contact_info_updated", {
                "phone": phone,
                "info": contact_info,
            })

        # If transfer_to_human was called, broadcast alert + state updates
        if any(tc.get("tool") == "transfer_to_human" for tc in tool_calls):
            await ws_manager.broadcast("human_transfer_alert", {"phone": phone})
            await ws_manager.broadcast("contact_ai_toggled", {
                "phone": phone,
                "ai_enabled": False,
            })
            await ws_manager.broadcast("tags_changed", agent_handler.tag_registry.all())
            await ws_manager.broadcast("contact_tags_updated", {
                "phone": phone,
                "tags": list(contact.tags),
            })

    # Expose broadcast_tool_calls for sandbox route
    deps.broadcast_tool_calls = _broadcast_tool_calls

    # ── Batch Processing ──────────────────────────────────────────

    async def _process_batch(phone: str, delay: float):
        """Wait for batch delay, then process all accumulated messages."""
        await asyncio.sleep(delay)

        items = state.pending_messages.pop(phone, [])
        state.batch_tasks.pop(phone, None)

        if not items:
            return

        # Create execution tracking
        exec_id = await astart_execution(phone, "webhook")

        try:
            # Record webhook payload
            await atrack_step("webhook_received", {
                "phone": phone,
                "items": [
                    {k: v for k, v in it.items() if k != "audio_path" or v}
                    for it in items
                ],
            })

            contact = agent_handler._get_contact(phone)

            # Separate plain text items from media items
            text_parts: list[str] = []
            text_msg_ids: list[str] = []
            media_items: list[dict] = []
            for item in items:
                if item.get("image_path") or item.get("audio_path"):
                    media_items.append(item)
                else:
                    text_parts.append(item.get("text", ""))
                    if item.get("msg_id"):
                        text_msg_ids.append(item["msg_id"])

            await atrack_step("batch_accumulated", {
                "text_count": len(text_parts),
                "media_count": len(media_items),
                "combined_preview": "\n".join(t for t in text_parts if t)[:200],
            })

            # AI is active — mark user messages as read before processing
            # (preserves unread_ai_count so operator sees AI replied)
            if contact.ai_enabled and settings.get("auto_reply", True):
                msg_ids = await asyncio.to_thread(contact.mark_user_messages_as_read)
                if msg_ids:
                    for mid in msg_ids:
                        try:
                            await asyncio.to_thread(gowa_client.mark_as_read, mid, phone)
                        except Exception:
                            pass
                    await ws_manager.broadcast("messages_read", {"phone": phone, "only_user": True})

            # Process combined text messages first
            if text_parts:
                combined = "\n".join(t for t in text_parts if t)
                if combined:
                    logger.info("[Batch] Processing %d text messages from %s: %s",
                                len(text_parts), phone, combined[:80])
                    last_msg_id = text_msg_ids[-1] if text_msg_ids else None
                    contact.add_message("user", combined, msg_id=last_msg_id)
                    if contact.ai_enabled and settings.get("auto_reply", True):
                        if not agent_handler.api_key:
                            notice = "[WhatsBot] API key não configurada."
                            contact.add_message("system_notice", notice)
                            await ws_manager.broadcast("new_message", {
                                "phone": phone,
                                "message": {"role": "system_notice", "content": notice, "ts": time.time()},
                            })
                        else:
                            try:
                                await asyncio.to_thread(gowa_client.send_chat_presence, phone)
                                result = await asyncio.to_thread(
                                    agent_handler.process_message, phone, combined,
                                    save_user_message=False, save_response=False)
                                if result.tool_calls:
                                    await _broadcast_tool_calls(phone, result.tool_calls, result.contact_info)
                                if result.reply:
                                    if result.reply.startswith("[WhatsBot]"):
                                        contact.add_message("system_notice", result.reply)
                                        await ws_manager.broadcast("new_message", {
                                            "phone": phone,
                                            "message": {"role": "system_notice", "content": result.reply, "ts": time.time()},
                                        })
                                    else:
                                        await _send_reply(phone, result.reply)
                            except Exception as e:
                                logger.error("[Batch] Agent error for %s: %s", phone, e)
                                await atrack_step("error", {"error": str(e), "phase": "text_processing"}, status="error")

            # Process each media item individually
            for item in media_items:
                text = item.get("text", "")
                image_path = item.get("image_path")
                audio_path = item.get("audio_path")

                media_label = "image" if image_path else "audio"
                logger.info("[Batch] Processing %s from %s", media_label, phone)

                # Save message to contact memory
                contact.add_message(
                    "user", text or ("[Áudio recebido]" if audio_path else ""),
                    media_type="image" if image_path else "audio",
                    media_path=image_path or audio_path,
                    msg_id=item.get("msg_id"),
                )

                # Transcribe audio / describe image
                transcription = ""
                try:
                    if audio_path and settings.get("audio_transcription_enabled", True):
                        transcription = await asyncio.to_thread(
                            agent_handler.transcribe_audio, audio_path, phone)
                    elif image_path and settings.get("image_transcription_enabled", True):
                        transcription = await asyncio.to_thread(
                            agent_handler.describe_image, image_path, phone)
                except Exception as e:
                    logger.error("[Batch] Transcription error for %s: %s", phone, e)

                # Save transcription as private message and broadcast.
                # Also update the original user message so the LLM sees the
                # transcription instead of the placeholder "[Áudio recebido]".
                if transcription:
                    contact.add_message("transcription", transcription)
                    # Update the last user message content with the transcription
                    if audio_path:
                        new_content = f"[Transcrição do áudio]: {transcription}"
                    elif image_path:
                        prefix = f"[Descrição da imagem]: {transcription}"
                        new_content = f"{prefix}\n{text}" if text else prefix
                    else:
                        new_content = None
                    if new_content:
                        await asyncio.to_thread(
                            agent_handler.update_last_user_message_content, phone, new_content
                        )
                    await ws_manager.broadcast("new_message", {
                        "phone": phone,
                        "message": {
                            "role": "transcription",
                            "content": transcription,
                            "ts": time.time(),
                        },
                    })

                if not contact.ai_enabled or not settings.get("auto_reply", True):
                    continue

                if not agent_handler.api_key:
                    notice = "[WhatsBot] API key não configurada."
                    contact.add_message("system_notice", notice)
                    await ws_manager.broadcast("new_message", {
                        "phone": phone,
                        "message": {"role": "system_notice", "content": notice, "ts": time.time()},
                    })
                    continue

                # Build text for LLM: use transcription if available
                llm_text = text or ""
                if audio_path:
                    if transcription:
                        llm_text = f"[Transcrição do áudio]: {transcription}"
                    else:
                        llm_text = llm_text or "[Áudio recebido]"
                elif image_path and transcription:
                    prefix = f"[Descrição da imagem]: {transcription}"
                    llm_text = f"{prefix}\n{text}" if text else prefix

                try:
                    await asyncio.to_thread(gowa_client.send_chat_presence, phone)
                    result = await asyncio.to_thread(
                        agent_handler.process_message, phone,
                        llm_text,
                        save_user_message=False, save_response=False,
                        image_path=image_path if not transcription else None,
                    )
                    if result.tool_calls:
                        await _broadcast_tool_calls(phone, result.tool_calls, result.contact_info)
                    if result.reply:
                        if result.reply.startswith("[WhatsBot]"):
                            contact.add_message("system_notice", result.reply)
                            await ws_manager.broadcast("new_message", {
                                "phone": phone,
                                "message": {"role": "system_notice", "content": result.reply, "ts": time.time()},
                            })
                        else:
                            await _send_reply(phone, result.reply)
                except Exception as e:
                    logger.error("[Batch] Agent error for %s (%s): %s", phone, media_label, e)
                    await atrack_step("error", {"error": str(e), "phase": f"{media_label}_processing"}, status="error")

            # Finalize execution as completed
            await aend_execution(exec_id)
        except Exception as exc:
            await aend_execution(exec_id, error=str(exc))

        # Prune old executions if beyond limit
        max_exec = settings.get("max_executions", 200)
        try:
            await asyncio.to_thread(prune_executions, max_exec)
        except Exception:
            pass

    # ── Webhook Endpoint ──────────────────────────────────────────

    @app.post("/api/webhook")
    async def webhook(body: dict):
        """Receive real-time message events from GOWA webhook."""
        event = body.get("event", "")
        # GOWA wraps message data inside "payload"
        data = body.get("payload", body.get("data", body))

        # Store raw payload for debugging (last 50, in-memory fallback)
        state.webhook_payloads.append({
            "ts": time.time(),
            "event": event,
            "payload": data,
        })

        # Handle chat presence events (typing/recording indicators)
        if event == "chat_presence":
            from_jid = data.get("from", "")
            phone = from_jid.split("@")[0] if "@" in from_jid else from_jid
            presence_state = data.get("state", "")
            media = data.get("media", "")
            if phone and presence_state:
                logger.info("[Webhook] chat_presence %s from %s (media=%s)",
                            presence_state, phone, media or "text")
                await ws_manager.broadcast("chat_presence", {
                    "phone": phone,
                    "state": presence_state,
                    "media": media,
                })
            return _ok({"status": "presence"})

        # Handle message.ack events (delivery + read receipts from WhatsApp)
        if event == "message.ack":
            receipt_type = data.get("receipt_type", "")
            msg_ids = data.get("ids", [])

            # Extract phone from ack payload (try multiple fields, GOWA is inconsistent)
            ack_phone = ""
            for field in ("chat_id", "from", "jid", "phone"):
                val = data.get(field, "")
                if val and "@" in val:
                    ack_phone = val.split("@")[0]
                    break
                elif val and not ack_phone:
                    ack_phone = val

            # Fallback: look up phone from the message in DB
            if not ack_phone and msg_ids:
                cid = await asyncio.to_thread(message_repo.get_contact_id_by_msg_id, msg_ids[0])
                if cid:
                    for phone_key, contact in agent_handler._contacts.items():
                        if contact.id == cid:
                            ack_phone = phone_key
                            break

            if receipt_type == "delivered" and msg_ids:
                # Update outgoing message status to "delivered" (with cascade to prior msgs)
                all_updated = []
                for mid in msg_ids:
                    updated = await asyncio.to_thread(message_repo.update_status_by_msg_id, mid, "delivered")
                    all_updated.extend(updated)
                # Deduplicate
                all_updated = list(dict.fromkeys(all_updated))
                logger.info("[Webhook] message.ack delivered for %s (ids=%s, cascaded=%d)",
                            ack_phone, msg_ids, len(all_updated))
                if ack_phone and all_updated:
                    await ws_manager.broadcast("message_status", {
                        "phone": ack_phone,
                        "msg_ids": all_updated,
                        "status": "delivered",
                    })

            elif receipt_type in ("read", "read-self") and msg_ids:
                # Update outgoing message status to "read" (with cascade to prior msgs)
                all_updated = []
                for mid in msg_ids:
                    updated = await asyncio.to_thread(message_repo.update_status_by_msg_id, mid, "read")
                    all_updated.extend(updated)
                all_updated = list(dict.fromkeys(all_updated))
                logger.info("[Webhook] message.ack read for %s (ids=%s, cascaded=%d)",
                            ack_phone, msg_ids, len(all_updated))
                if ack_phone and all_updated:
                    await ws_manager.broadcast("message_status", {
                        "phone": ack_phone,
                        "msg_ids": all_updated,
                        "status": "read",
                    })

                # Existing unread tracking logic (for incoming messages read by us)
                for phone_key, contact in agent_handler._contacts.items():
                    unread_ids = contact.get_unread_msg_ids()
                    matched = [mid for mid in msg_ids if mid in unread_ids]
                    if matched:
                        logger.info("[Webhook] message.ack unread cleared for %s (ids=%s)", phone_key, matched)
                        contact.mark_as_read()
                        await ws_manager.broadcast("messages_read", {"phone": phone_key})

            return _ok({"status": "ack"})

        # Only process incoming messages
        if event and event not in ("message", "message:received", ""):
            return _ok({"status": "ignored"})

        if not isinstance(data, dict):
            return _ok({"status": "ignored"})

        # Extract message fields (GOWA field names vary)
        is_from_me = data.get("is_from_me", data.get("from_me", data.get("FromMe", False)))

        # Capture bot's own phone from outgoing messages (for @mention detection)
        if is_from_me:
            own_jid = (data.get("sender_jid", "") or data.get("from", "")
                       or data.get("sender", ""))
            if own_jid and "@s.whatsapp.net" in own_jid:
                state.bot_phone = own_jid.split("@")[0].split(":")[0]
                logger.info("[Webhook] Bot phone captured from own message: %s", state.bot_phone)

        msg_id = data.get("id", data.get("Id", data.get("message_id", ""))
                         ) or str(uuid.uuid4())
        if msg_id in state.processed_messages:
            return _ok({"status": "duplicate"})

        # Extract body — try multiple known field names
        text = (data.get("content", "")
                or data.get("body", "")
                or data.get("Body", "")
                or data.get("message", "")
                or data.get("text", "")).strip()

        # Extract media paths from GOWA payload
        image_path: str | None = None
        audio_path: str | None = None
        document_path: str | None = None
        document_name: str | None = None

        raw_image = data.get("image")
        if raw_image:
            if isinstance(raw_image, str):
                image_path = raw_image
            elif isinstance(raw_image, dict):
                image_path = raw_image.get("path", "")
                if not text:
                    text = (raw_image.get("caption", "") or "").strip()

        raw_audio = data.get("audio")
        if raw_audio:
            if isinstance(raw_audio, str):
                audio_path = raw_audio
            elif isinstance(raw_audio, dict):
                audio_path = raw_audio.get("path", "")

        # Video notes (voice messages) are treated as audio
        raw_vn = data.get("video_note")
        if raw_vn and not audio_path:
            if isinstance(raw_vn, str):
                audio_path = raw_vn
            elif isinstance(raw_vn, dict):
                audio_path = raw_vn.get("path", "")

        raw_doc = data.get("document")
        if raw_doc:
            if isinstance(raw_doc, str):
                document_path = raw_doc
            elif isinstance(raw_doc, dict):
                document_path = raw_doc.get("path", "")
                document_name = raw_doc.get("file_name") or raw_doc.get("filename") or ""
                if not text:
                    text = (raw_doc.get("caption", "") or "").strip()

        # Contact / vCard messages (GOWA v8.5.0+)
        if not text:
            shared_contacts: list[tuple[str, str]] = []
            single = data.get("contact")
            if isinstance(single, dict):
                name = (single.get("displayName") or single.get("display_name")
                        or single.get("name") or "").strip()
                ph = (single.get("phone_number") or single.get("phoneNumber") or "").strip()
                shared_contacts.append((name, ph))
            arr = data.get("contacts_array") or data.get("contactsArray")
            if isinstance(arr, list):
                for c in arr:
                    if not isinstance(c, dict):
                        continue
                    name = (c.get("displayName") or c.get("display_name")
                            or c.get("name") or "").strip()
                    ph = (c.get("phone_number") or c.get("phoneNumber") or "").strip()
                    shared_contacts.append((name, ph))
            if shared_contacts:
                if len(shared_contacts) == 1:
                    name, ph = shared_contacts[0]
                    label = name or ph or "sem nome"
                    suffix = f" ({ph})" if ph and name else ""
                    text = f"[Contato compartilhado: {label}{suffix}]"
                else:
                    names = ", ".join(n or p or "?" for n, p in shared_contacts)
                    text = f"[Contatos compartilhados ({len(shared_contacts)}): {names}]"

        # For audio without text, set a placeholder
        if audio_path and not text:
            text = "[Áudio recebido]" if not is_from_me else "[Áudio enviado]"

        # For image without text, set a placeholder for outgoing
        if image_path and not text and is_from_me:
            text = "[Imagem enviada]"

        # For document without text, set a placeholder
        if document_path and not text:
            label = document_name or "documento"
            text = f"[Documento recebido: {label}]" if not is_from_me else f"[Documento enviado: {label}]"

        # Extract chat and sender separately for group support
        chat_jid = (data.get("chat_jid", "") or data.get("chat_id", "")
                    or data.get("from", "") or data.get("jid", ""))
        sender_jid = data.get("sender_jid", "") or data.get("sender", "")

        is_group = "@g.us" in chat_jid

        if is_group:
            # For groups: route replies to the group, track individual sender
            phone = chat_jid  # keep full JID (e.g. 120363xxx@g.us)
            individual_phone = sender_jid.split("@")[0] if "@" in sender_jid else sender_jid
            from_name = data.get("from_name", "") or data.get("pushName", "") or data.get("notify", "")
        else:
            # For private chats: use sender as before
            sender = sender_jid or chat_jid
            phone = sender.split("@")[0] if "@" in sender else sender
            individual_phone = phone
            from_name = data.get("from_name", "") or data.get("pushName", "") or data.get("notify", "")

        if not phone or (not text and not image_path and not audio_path and not document_path):
            media_kind = ("image" if image_path
                          else "audio" if audio_path
                          else "document" if document_path
                          else "none")
            logger.info("[Webhook] Skipping: text=%r phone=%r media=%s keys=%s payload=%s",
                        text[:50] if text else "", phone, media_kind,
                        list(data.keys()), str(data)[:1000])
            return _ok({"status": "ignored"})

        state.processed_messages.add(msg_id)

        # Filter GOWA echo-backs: ignore messages we recently sent
        if text:
            sent_key = f"{phone}:{text[:120]}"
            sent_at = state.recently_sent.pop(sent_key, None)
            if sent_at and (time.time() - sent_at) < 30:
                logger.info("[Webhook] Ignoring echo-back for %s", phone)
                return _ok({"status": "echo"})

        # Sync outgoing messages sent from phone (not via our app)
        if is_from_me:
            # Determine media metadata
            media_type: str | None = None
            media_path: str | None = None
            if image_path:
                media_type = "image"
                media_path = image_path
            elif audio_path:
                media_type = "audio"
                media_path = audio_path
            elif document_path:
                media_type = "document"
                media_path = document_path

            logger.info("[Webhook] Syncing outgoing %s to %s: %s",
                        media_type or "message", phone,
                        text[:80] if text else f"[{media_type}]")

            # Save as "assistant" in contact memory (status="operator" to distinguish from AI)
            contact = agent_handler._get_contact(phone)
            await asyncio.to_thread(
                contact.add_message, "assistant", text,
                media_type=media_type, media_path=media_path, msg_id=msg_id,
                status="operator")

            # Broadcast to frontend
            broadcast_msg: dict = {"role": "assistant", "content": text,
                                   "ts": time.time(), "msg_id": msg_id,
                                   "status": "operator"}
            if media_type:
                broadcast_msg["media_type"] = media_type
                broadcast_msg["media_path"] = media_path
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "message": broadcast_msg,
            })

            return _ok({"status": "synced"})

        # Determine media metadata for broadcast
        media_type: str | None = None
        media_path: str | None = None
        if image_path:
            media_type = "image"
            media_path = image_path
        elif audio_path:
            media_type = "audio"
            media_path = audio_path
        elif document_path:
            media_type = "document"
            media_path = document_path

        # For groups: prefix text with sender name and check @mention
        display_text = text
        skip_ai = False
        if is_group:
            # Log full group payload for debugging field names
            logger.info("[Webhook] Group payload: %s", json.dumps(data, default=str, ensure_ascii=False)[:2000])

            # Ensure group metadata is stored
            contact = agent_handler._get_contact(phone)
            if not contact.is_group or not contact.group_name:
                contact.is_group = True
                # Try to get group name from payload fields (NOT from_name, that's the sender)
                group_name = (data.get("subject", "")
                              or data.get("group_name", "")
                              or data.get("group_subject", "")
                              or data.get("chat_name", ""))
                # Fallback: fetch group name from GOWA API
                if not group_name:
                    try:
                        group_name = await asyncio.to_thread(
                            gowa_client.get_group_name, phone)
                    except Exception as e:
                        logger.warning("[Webhook] Failed to fetch group name: %s", e)
                if group_name:
                    contact.group_name = group_name
                    logger.info("[Webhook] Group name resolved: %s -> %s", phone, group_name)
                else:
                    logger.warning("[Webhook] Could not resolve group name for %s", phone)
                contact.save()

            # Check if bot can send in this group
            if state.bot_phone:
                try:
                    can_send = await asyncio.to_thread(
                        gowa_client.can_bot_send_in_group, phone, state.bot_phone)
                    if contact.can_send != can_send:
                        contact.can_send = can_send
                        contact.save()
                        logger.info("[Webhook] Group %s can_send updated: %s", phone, can_send)
                except Exception as e:
                    logger.warning("[Webhook] Failed to check group send permission: %s", e)

            # Prefix message with sender name for group context
            sender_label = from_name or individual_phone
            if text:
                display_text = f"[{sender_label}]: {text}"

            # Check if bot is mentioned
            group_mode = settings.get("group_reply_mode", "mention_only")
            bot_mentioned = _is_bot_mentioned(text, data)

            if group_mode == "never" or (group_mode == "mention_only" and not bot_mentioned):
                skip_ai = True
                logger.info("[Webhook] Group message (no mention) from %s in %s: %s",
                            sender_label, phone, text[:80] if text else "[media]")
            else:
                # Bot was mentioned — strip mention from text for LLM
                cleaned = _strip_bot_mention(text)
                display_text = f"[{sender_label}]: {cleaned}" if cleaned else display_text
                logger.info("[Webhook] Group message (@mention) from %s in %s: %s",
                            sender_label, phone, text[:80] if text else "[media]")
        else:
            logger.info("[Webhook] %s from %s: %s",
                        media_type.capitalize() if media_type else "Message",
                        phone, text[:80] if text else f"[{media_type}]")

        # Check/update archive status from GOWA (skip if archived by app)
        try:
            contact = agent_handler._get_contact(phone)
            if not contact.archived_by_app:
                archived = await asyncio.to_thread(gowa_client.is_chat_archived, chat_jid)
                logger.info("[Webhook] Archive check: %s (jid=%s) -> archived=%s", phone, chat_jid, archived)
                if contact.is_archived != archived:
                    contact.is_archived = archived
                    contact.save()
                    logger.info("[Webhook] Archive status updated: %s -> %s", phone, archived)
            else:
                logger.info("[Webhook] Skipping archive check for %s (archived by app)", phone)
        except Exception as e:
            logger.warning("[Webhook] Failed to check archive status for %s: %s", phone, e)

        # Auto-fill contact name from WhatsApp pushName (private chats only)
        if from_name and not is_group:
            await asyncio.to_thread(agent_handler._get_contact(phone).set_wa_name, from_name)

        # Increment unread count for incoming user messages
        await asyncio.to_thread(lambda: agent_handler._get_contact(phone).increment_unread(msg_id))

        # Broadcast incoming message to frontend in real-time
        broadcast_msg: dict = {"role": "user", "content": display_text, "ts": time.time(), "msg_id": msg_id}
        if media_type:
            broadcast_msg["media_type"] = media_type
            broadcast_msg["media_path"] = media_path
        await ws_manager.broadcast("new_message", {
            "phone": phone,
            "message": broadcast_msg,
        })

        # For group messages without mention: save to history but don't trigger AI
        if skip_ai:
            await asyncio.to_thread(
                agent_handler._get_contact(phone).add_message,
                "user", display_text, msg_id=msg_id)
            return _ok({"status": "group_no_mention"})

        # Batch messages — accumulate and wait before responding
        if phone not in state.pending_messages:
            state.pending_messages[phone] = []
        state.pending_messages[phone].append({
            "text": display_text,
            "image_path": image_path,
            "audio_path": audio_path,
            "msg_id": msg_id,
        })

        # Cancel existing batch timer for this contact
        if phone in state.batch_tasks:
            state.batch_tasks[phone].cancel()

        # Schedule batch processing after delay
        batch_delay = settings.get("message_batch_delay", 3.0)
        state.batch_tasks[phone] = asyncio.create_task(
            _process_batch(phone, batch_delay)
        )

        # Prune processed set to avoid unbounded growth
        if len(state.processed_messages) > 5000:
            oldest = list(state.processed_messages)[:2500]
            for item in oldest:
                state.processed_messages.discard(item)

        # Prune stale recently_sent entries (older than 60s)
        now = time.time()
        stale = [k for k, v in state.recently_sent.items() if now - v > 60]
        for k in stale:
            del state.recently_sent[k]

        return _ok({"status": "batched"})
