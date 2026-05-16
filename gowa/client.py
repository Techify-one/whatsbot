import logging
import mimetypes
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Default device ID used across GOWA sessions (persisted per-instance)
_DEFAULT_DEVICE_NAME = "whatsbot"


def extract_msg_id(response: dict | None) -> str | None:
    """Extract the message ID from a GOWA send response.

    Tries multiple known field paths since the GOWA API is not 100% consistent.
    Returns None if not found (graceful fallback).
    """
    if not response or not isinstance(response, dict):
        return None
    results = response.get("results", {})
    if isinstance(results, dict):
        for key in ("message_id", "id"):
            val = results.get(key)
            if val:
                return str(val)
    for key in ("message_id", "id"):
        val = response.get(key)
        if val:
            return str(val)
    logger.warning("extract_msg_id: could not find msg_id in GOWA response. keys=%s",
                   list(response.keys()))
    return None


class GOWASendError(Exception):
    """Raised when sending a message via GOWA fails."""

    def __init__(self, message: str, error_type: str = "unknown"):
        super().__init__(message)
        self.error_type = error_type  # network, api, unknown


class GOWAClient:
    """HTTP client for the GOWA REST API (go-whatsapp-web-multidevice v8.5.0)."""

    def __init__(self, port: int = 3000, timeout: float = 15.0):
        self.base_url = f"http://127.0.0.1:{port}"
        self.timeout = timeout
        self.device_id: str = _DEFAULT_DEVICE_NAME
        self._device_ready = False

    @property
    def _headers(self) -> dict:
        return {"X-Device-Id": self.device_id}

    def _request(self, method: str, path: str, raw: bool = False,
                 skip_device_header: bool = False,
                 raise_on_error: bool = False, **kwargs) -> dict | bytes | None:
        url = f"{self.base_url}{path}"
        if skip_device_header:
            headers = kwargs.pop("headers", {})
        else:
            headers = {**self._headers, **kwargs.pop("headers", {})}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.request(method, url, headers=headers, **kwargs)
                resp.raise_for_status()
                ct = resp.headers.get("content-type", "")
                if raw or ct.startswith("image/"):
                    return resp.content
                return resp.json() if resp.text else {}
        except httpx.ConnectError:
            logger.debug("GOWA not reachable at %s", url)
            if raise_on_error:
                raise GOWASendError(
                    "WhatsApp não está acessível. Verifique se o serviço está rodando.",
                    error_type="network",
                )
            return None
        except httpx.HTTPStatusError as e:
            logger.error("GOWA HTTP %s %s -> %s", method, path, e.response.status_code)
            if raise_on_error:
                status = e.response.status_code
                # Try to extract error message from GOWA response
                detail = ""
                try:
                    body = e.response.json()
                    detail = body.get("message", body.get("error", ""))
                except Exception:
                    pass
                msg = f"Erro da API do WhatsApp (HTTP {status})"
                if detail:
                    msg += f": {detail}"
                raise GOWASendError(msg, error_type="api")
            return None
        except GOWASendError:
            raise
        except Exception as e:
            logger.error("GOWA request error: %s", e)
            if raise_on_error:
                raise GOWASendError(
                    f"Erro ao enviar mensagem: {e}",
                    error_type="unknown",
                )
            return None

    # ── Device Management ────────────────────────────────────────────

    def ensure_device(self) -> bool:
        """Ensure a device exists in GOWA, creating one if needed."""
        if self._device_ready:
            return True

        # Check if device already exists
        devices = self.list_devices()
        if devices:
            for d in devices:
                did = d.get("id", d.get("device", ""))
                if did == self.device_id:
                    self._device_ready = True
                    logger.info("GOWA device '%s' already exists.", self.device_id)
                    return True
            # Use the first existing device
            first = devices[0]
            self.device_id = first.get("id", first.get("device", self.device_id))
            self._device_ready = True
            logger.info("Using existing GOWA device '%s'.", self.device_id)
            return True

        # Create a new device
        result = self.create_device(self.device_id)
        if result:
            self._device_ready = True
            logger.info("Created GOWA device '%s'.", self.device_id)
            return True

        logger.error("Failed to create GOWA device.")
        return False

    def list_devices(self) -> list[dict]:
        """List all registered devices."""
        result = self._request("GET", "/devices", skip_device_header=True)
        if result and isinstance(result, dict):
            return result.get("results", None) or []
        return []

    def create_device(self, device_id: str | None = None) -> dict | None:
        """Create a new device in GOWA."""
        payload = {"device_id": device_id} if device_id else {}
        return self._request("POST", "/devices", json=payload, skip_device_header=True)

    # ── Health / Status ──────────────────────────────────────────────

    def health_check(self) -> bool:
        """Check if GOWA is running and reachable."""
        result = self._request("GET", "/devices", skip_device_header=True)
        return result is not None

    def get_status(self) -> dict | None:
        """Get WhatsApp connection status."""
        if not self._device_ready:
            self.ensure_device()
        return self._request("GET", "/app/status")

    def is_connected(self) -> bool:
        """Check if WhatsApp is connected."""
        status = self.get_status()
        if not status:
            return False
        results = status.get("results", status.get("data", status))
        if isinstance(results, dict):
            return results.get("is_logged_in", results.get("is_connected", False))
        return False

    # ── QR Code / Login ──────────────────────────────────────────────

    def get_qr_code(self) -> bytes | None:
        """Get QR code image for WhatsApp login.

        GOWA v8.5.0 returns JSON with a qr_link URL pointing to a PNG image.
        Returns None if already logged in or on error.
        """
        if not self._device_ready:
            self.ensure_device()
        # Skip if already connected
        if self.is_connected():
            logger.debug("get_qr_code: already connected, skipping.")
            return None
        result = self._request("GET", "/app/login")
        if not result or not isinstance(result, dict):
            logger.warning("get_qr_code: /app/login returned no data.")
            return None

        # Extract QR image URL from response
        results = result.get("results", result)
        qr_link = results.get("qr_link", "")
        if not qr_link:
            logger.warning("get_qr_code: no qr_link in response. keys=%s", list(results.keys()))
            return None

        # Download the QR image (use urlparse to extract path safely)
        qr_path = urlparse(qr_link).path
        logger.info("get_qr_code: fetching QR image from %s", qr_path)
        image_data = self._request("GET", qr_path, raw=True)
        if image_data and isinstance(image_data, bytes) and len(image_data) > 100:
            return image_data
        logger.warning("get_qr_code: QR image download failed or too small (%s bytes).",
                       len(image_data) if image_data else 0)
        return None

    # ── Messages ─────────────────────────────────────────────────────

    def _clean_phone(self, phone: str) -> str:
        return phone.strip().replace("+", "").replace(" ", "").replace("-", "")

    @staticmethod
    def _is_group_jid(phone: str) -> bool:
        """Check if a phone/JID string refers to a group."""
        return "@g.us" in phone

    def _format_target(self, phone: str) -> str:
        """Format phone for GOWA API. Groups keep full JID, individuals get cleaned."""
        if self._is_group_jid(phone):
            return phone
        return self._clean_phone(phone)

    def send_message(self, phone: str, text: str) -> dict:
        """Send a text message to a phone number or group. Raises GOWASendError on failure."""
        payload = {
            "phone": self._format_target(phone),
            "message": text,
        }
        return self._request("POST", "/send/message", raise_on_error=True, json=payload)

    def send_image(self, phone: str, image_path: str, caption: str = "") -> dict:
        """Send an image to a phone number or group via multipart/form-data. Raises GOWASendError on failure."""
        phone = self._format_target(phone)
        url = f"{self.base_url}/send/image"
        mime = mimetypes.guess_type(image_path)[0] or "image/png"
        try:
            with httpx.Client(timeout=30.0) as client:
                with open(image_path, "rb") as f:
                    files = {"image": (Path(image_path).name, f, mime)}
                    data = {"phone": phone}
                    if caption:
                        data["caption"] = caption
                    resp = client.post(url, headers=self._headers, data=data, files=files)
                    resp.raise_for_status()
                    return resp.json() if resp.text else {}
        except httpx.ConnectError:
            raise GOWASendError(
                "WhatsApp não está acessível. Verifique se o serviço está rodando.",
                error_type="network",
            )
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                body = e.response.json()
                detail = body.get("message", body.get("error", ""))
            except Exception:
                pass
            msg = f"Erro da API do WhatsApp (HTTP {e.response.status_code})"
            if detail:
                msg += f": {detail}"
            raise GOWASendError(msg, error_type="api")
        except GOWASendError:
            raise
        except Exception as e:
            raise GOWASendError(f"Erro ao enviar imagem: {e}", error_type="unknown")

    def send_audio(self, phone: str, audio_path: str) -> dict:
        """Send an audio file to a phone number or group via multipart/form-data. Raises GOWASendError on failure."""
        phone = self._format_target(phone)
        url = f"{self.base_url}/send/audio"
        mime = mimetypes.guess_type(audio_path)[0] or "audio/ogg"
        try:
            with httpx.Client(timeout=30.0) as client:
                with open(audio_path, "rb") as f:
                    files = {"audio": (Path(audio_path).name, f, mime)}
                    data = {"phone": phone, "ptt": "true"}
                    resp = client.post(url, headers=self._headers, data=data, files=files)
                    resp.raise_for_status()
                    return resp.json() if resp.text else {}
        except httpx.ConnectError:
            raise GOWASendError(
                "WhatsApp não está acessível. Verifique se o serviço está rodando.",
                error_type="network",
            )
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                body = e.response.json()
                detail = body.get("message", body.get("error", ""))
            except Exception:
                pass
            msg = f"Erro da API do WhatsApp (HTTP {e.response.status_code})"
            if detail:
                msg += f": {detail}"
            raise GOWASendError(msg, error_type="api")
        except GOWASendError:
            raise
        except Exception as e:
            raise GOWASendError(f"Erro ao enviar áudio: {e}", error_type="unknown")

    # ── Read Receipts ──────────────────────────────────────────────

    def mark_as_read(self, message_id: str, phone: str) -> dict | None:
        """Send a read receipt for a message (best-effort, never raises)."""
        if self._is_group_jid(phone):
            jid = phone
        else:
            jid = f"{self._clean_phone(phone)}@s.whatsapp.net"
        payload = {"phone": jid}
        try:
            return self._request("POST", f"/message/{message_id}/read", json=payload)
        except Exception as e:
            logger.warning("mark_as_read failed for %s: %s", message_id, e)
            return None

    # ── Presence ───────────────────────────────────────────────────

    def send_chat_presence(self, phone: str, action: str = "start") -> dict | None:
        """Send typing indicator. action: 'start' or 'stop'."""
        payload = {"phone": self._format_target(phone), "action": action}
        return self._request("POST", "/send/chat-presence", json=payload)

    def stop_chat_presence(self, phone: str) -> dict | None:
        """Stop typing indicator."""
        return self.send_chat_presence(phone, "stop")

    # ── Chats ─────────────────────────────────────────────────────

    def get_chats(self, limit: int = 20) -> list[dict]:
        """Get list of chats."""
        result = self._request("GET", f"/chats?limit={limit}")
        if result and isinstance(result, dict):
            # v8.5.0 nests list under results.data
            results = result.get("results", {})
            if isinstance(results, dict):
                return results.get("data", []) or []
            if isinstance(results, list):
                return results
        return []

    def get_group_info(self, group_jid: str) -> dict | None:
        """Get full group metadata including announce mode and participants."""
        try:
            result = self._request("GET", f"/group/info?group_id={group_jid}")
            if result and isinstance(result, dict):
                return result.get("results", result)
        except Exception as e:
            logger.warning("[GOWA] get_group_info failed for %s: %s", group_jid, e)
        return None

    def get_group_name(self, group_jid: str) -> str:
        """Get a group's name/subject via GOWA group info endpoint."""
        info = self.get_group_info(group_jid)
        if info and isinstance(info, dict):
            name = (info.get("Name", "")
                    or info.get("name", "")
                    or info.get("subject", "")
                    or info.get("Topic", ""))
            if name:
                return name
        return ""

    def can_bot_send_in_group(self, group_jid: str, bot_phone: str) -> bool:
        """Check if bot can send messages in a group.

        Returns False only if the group is in announce mode and bot is not admin.
        Defaults to True on any API failure to avoid false lockouts.
        """
        info = self.get_group_info(group_jid)
        if not info:
            return True

        if not info.get("IsAnnounce", False):
            return True

        # Announce mode: check if bot is admin
        participants = info.get("Participants", [])
        if not participants:
            return True

        for p in participants:
            phone_number = p.get("PhoneNumber", "")
            if bot_phone in phone_number:
                return bool(p.get("IsAdmin", False) or p.get("IsSuperAdmin", False))

        return False

    def is_chat_archived(self, jid: str) -> bool:
        """Check if a specific chat is archived in WhatsApp."""
        chats = self.get_chats(limit=100)
        logger.info("[GOWA] is_chat_archived(%s): got %d chats", jid, len(chats))
        for chat in chats:
            if chat.get("jid") == jid:
                logger.info("[GOWA] Raw chat data for %s: %s", jid, chat)
                result = bool(chat.get("archived") or chat.get("Archived"))
                logger.info("[GOWA] is_chat_archived(%s): found, archived=%s", jid, result)
                return result
        logger.info("[GOWA] is_chat_archived(%s): JID not found in chat list", jid)
        return False

    def get_chat_messages(self, chat_jid: str, limit: int = 20) -> list[dict]:
        """Get messages from a specific chat."""
        result = self._request("GET", f"/chat/{chat_jid}/messages?limit={limit}")
        if result and isinstance(result, dict):
            results = result.get("results", {})
            if isinstance(results, dict):
                return results.get("data", []) or []
            if isinstance(results, list):
                return results
        return []

    def get_message_filename(self, chat_jid: str, msg_id: str) -> str:
        """Resolve a media message's original filename from GOWA chat storage.

        GOWA's webhook omits the document filename when auto-download is
        enabled (``buildAutoDownloadPayload`` sends only ``path``/``caption``
        and the on-disk path is UUID-based, so the name can't be recovered
        from it). But GOWA persists ``filename`` in its chat storage *before*
        forwarding the webhook and exposes it via ``GET /chat/{jid}/messages``
        — so we look the message up by id.
        """
        if not chat_jid or not msg_id:
            return ""
        try:
            messages = self.get_chat_messages(chat_jid, limit=25)
        except Exception:
            return ""
        for m in messages:
            if isinstance(m, dict) and str(m.get("id", "")) == str(msg_id):
                return (m.get("filename") or "").strip()
        return ""

    # ── Phone Check ────────────────────────────────────────────────

    def check_phone(self, phone: str) -> dict:
        """Check if a phone number is registered on WhatsApp via GOWA GET /user/check.

        Returns dict with keys: registered, jid, name, canonical_phone.
        canonical_phone is the phone number WhatsApp uses internally (from /user/info devices).
        """
        clean = self._clean_phone(phone)
        jid = f"{clean}@s.whatsapp.net"
        result = self._request("GET", f"/user/check?phone={jid}", raise_on_error=True)
        if not result or not isinstance(result, dict):
            return {"registered": False}
        results = result.get("results", result)
        if isinstance(results, dict):
            registered = bool(results.get("is_on_whatsapp", results.get("IsOnWhatsApp", False)))
            data = {"registered": registered, "jid": jid, "name": "", "canonical_phone": clean}
            if registered:
                info = self._get_user_info(jid)
                # BR numbers: if no canonical_phone from devices, try 12-digit variant
                if not info.get("canonical_phone") and clean.startswith("55") and len(clean) == 13:
                    alt = clean[:4] + clean[5:]  # remove 9 after DDD
                    alt_jid = f"{alt}@s.whatsapp.net"
                    alt_info = self._get_user_info(alt_jid)
                    if alt_info.get("canonical_phone"):
                        info = alt_info
                    elif alt_info.get("name") and not info.get("name"):
                        info = alt_info
                if info.get("name"):
                    data["name"] = info["name"]
                if info.get("canonical_phone"):
                    data["canonical_phone"] = info["canonical_phone"]
            return data
        return {"registered": False}

    def _get_user_info(self, jid: str) -> dict:
        """Get WhatsApp push name and canonical phone for a JID via GET /user/info.

        Returns dict with optional keys: name, canonical_phone.
        """
        try:
            result = self._request("GET", f"/user/info?phone={jid}")
            if not result or not isinstance(result, dict):
                return {}
            results = result.get("results", {})
            data = results.get("data", [])
            if isinstance(data, list) and len(data) > 0:
                item = data[0]
                info = {}
                name = item.get("name", "") or ""
                if name:
                    info["name"] = name
                # Extract canonical phone from devices
                devices = item.get("devices", [])
                if devices and isinstance(devices, list):
                    user = str(devices[0].get("User", "") or "")
                    if user:
                        info["canonical_phone"] = user
                return info
            return {}
        except Exception as e:
            logger.warning("_get_user_info failed for %s: %s", jid, e)
            return {}

    # ── Avatar ───────────────────────────────────────────────────────

    def get_avatar(self, phone: str, is_preview: bool = True) -> bytes | None:
        """Fetch a contact's WhatsApp profile picture. Returns image bytes or None."""
        if self._is_group_jid(phone):
            jid = phone
        else:
            jid = f"{self._clean_phone(phone)}@s.whatsapp.net"
        preview = "true" if is_preview else "false"
        try:
            data = self._request("GET", f"/user/avatar?phone={jid}&is_preview={preview}")
            if data and isinstance(data, bytes) and len(data) > 100:
                return data
            # GOWA returns JSON with a URL pointing to the image on WhatsApp CDN
            if data and isinstance(data, dict):
                results = data.get("results", data)
                url = results.get("url", results.get("profile_picture", ""))
                if url and url.startswith("http"):
                    # Download directly from external URL (not via GOWA)
                    with httpx.Client(timeout=15.0) as client:
                        resp = client.get(url)
                        resp.raise_for_status()
                        if resp.content and len(resp.content) > 100:
                            return resp.content
            return None
        except Exception as e:
            logger.debug("get_avatar failed for %s: %s", phone, e)
            return None

    # ── Session ──────────────────────────────────────────────────────

    def reset(self):
        """Reset client state (call after GOWA restarts, logout, etc.)."""
        self._device_ready = False

    def logout(self) -> dict | None:
        """Disconnect from WhatsApp."""
        result = self._request("GET", "/app/logout")
        self.reset()
        return result

    def reconnect(self) -> dict | None:
        """Reconnect to WhatsApp."""
        result = self._request("GET", "/app/reconnect")
        self.reset()
        return result
