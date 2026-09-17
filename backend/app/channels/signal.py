"""Signal channel — self-hosted via signal-cli REST wrapper (no paid gateway).

Talks to a ``signal-cli`` REST API (bbernhard/signal-cli-rest-api shape):
``GET /v1/receive/{number}`` for inbound envelopes (polled),
``POST /v1/send`` for outbound replies. All HTTP uses the standard library
so the channel adds zero dependencies; without a configured wrapper the
channel refuses to start (fail-closed) instead of half-running.

Configuration keys (in ``config.yaml`` under ``channels.signal``):
    - ``base_url``: wrapper base URL (default ``http://127.0.0.1:8080``).
    - ``number``: our registered Signal number, e.g. ``+15551234567``.
    - ``allowed_users``: (optional) list of allowed sender numbers. Empty = allow all.
    - ``poll_interval_seconds``: receive poll cadence (default 5, min 1).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.channels.base import Channel
from app.channels.message_bus import (
    InboundMessage,
    InboundMessageType,
    MessageBus,
    OutboundMessage,
)

logger = logging.getLogger(__name__)

SIGNAL_DEFAULT_BASE_URL = "http://127.0.0.1:8080"
SIGNAL_DEFAULT_POLL_INTERVAL_SECONDS = 5.0
SIGNAL_MIN_POLL_INTERVAL_SECONDS = 1.0
SIGNAL_HTTP_TIMEOUT_SECONDS = 30.0
SIGNAL_MAX_MESSAGE_LENGTH = 4000


def _parse_envelopes(payload: Any) -> list[dict[str, Any]]:
    """Normalize a /v1/receive body into envelope dicts (tolerates shapes)."""
    if isinstance(payload, dict):
        payload = payload.get("messages", payload.get("envelopes", []))
    if not isinstance(payload, list):
        return []
    return [e for e in payload if isinstance(e, dict)]


def _envelope_to_chat(envelope: dict[str, Any]) -> tuple[str, str, str] | None:
    """Extract (sender, chat_id, text) from a dataMessage envelope."""
    source = envelope.get("source") or envelope.get("sender") or envelope.get("sourceNumber")
    data = envelope.get("dataMessage") or {}
    text = data.get("message") or envelope.get("message") or ""
    group = data.get("groupInfo") or {}
    group_id = group.get("groupId") or envelope.get("groupId")
    if not source or not str(text).strip():
        return None
    chat_id = f"group:{group_id}" if group_id else f"dm:{source}"
    return str(source), str(chat_id), str(text).strip()


def _chunk_text(text: str, limit: int = SIGNAL_MAX_MESSAGE_LENGTH) -> list[str]:
    if len(text) <= limit:
        return [text]
    return [text[i : i + limit] for i in range(0, len(text), limit)]


class SignalChannel(Channel):
    """Signal channel backed by a self-hosted signal-cli REST wrapper."""

    def __init__(self, bus: MessageBus, config: dict[str, Any]) -> None:
        super().__init__(name="signal", bus=bus, config=config)
        self._base_url = str(config.get("base_url", SIGNAL_DEFAULT_BASE_URL)).rstrip("/")
        self._number = str(config.get("number", "")).strip()
        try:
            self._poll_interval = max(
                SIGNAL_MIN_POLL_INTERVAL_SECONDS,
                float(config.get("poll_interval_seconds", SIGNAL_DEFAULT_POLL_INTERVAL_SECONDS)),
            )
        except (TypeError, ValueError):
            self._poll_interval = SIGNAL_DEFAULT_POLL_INTERVAL_SECONDS
        self._allowed_users = {str(u).strip() for u in config.get("allowed_users", []) if str(u).strip()}
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def _http_json(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        url = f"{self._base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=SIGNAL_HTTP_TIMEOUT_SECONDS) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Signal wrapper HTTP {exc.code} on {method} {path}") from exc
        except OSError as exc:
            raise RuntimeError(f"Signal wrapper unreachable at {self._base_url}: {exc}") from exc
        if not raw.strip():
            return []
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise RuntimeError(f"Signal wrapper returned non-JSON on {method} {path}") from exc

    def _poll_once(self) -> None:
        try:
            payload = self._http_json("GET", f"/v1/receive/{urllib.parse.quote(self._number, safe='+')}")
        except RuntimeError as exc:
            logger.warning("Signal receive poll failed: %s", exc)
            return
        for envelope in _parse_envelopes(payload):
            try:
                parsed = _envelope_to_chat(envelope)
            except Exception:
                logger.debug("Signal envelope parse failed", exc_info=True)
                continue
            if parsed is None:
                continue
            sender, chat_id, text = parsed
            if self._allowed_users and sender not in self._allowed_users:
                logger.debug("Signal message from non-allowlisted sender dropped")
                continue
            inbound = self._make_inbound(chat_id=chat_id, user_id=sender, text=text, msg_type=InboundMessageType.CHAT)
            reservation = self._reserve_inbound(inbound)
            if reservation is None:
                continue
            self._commit_reserved_inbound(reservation, inbound)

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            if self._running:
                self._poll_once()
            self._stop_event.wait(self._poll_interval)

    async def start(self) -> None:
        if self._running:
            return
        if not self._number:
            logger.error("Signal channel requires channels.signal.number")
            return
        try:
            self._http_json("GET", "/v1/about")
        except RuntimeError as exc:
            logger.error("Signal channel refusing to start: %s", exc)
            return
        self._stop_event.clear()
        self._open_threadsafe_future_intake()
        self._running = True
        self.bus.subscribe_outbound(self._on_outbound)
        self._thread = threading.Thread(target=self._poll_loop, name="signal-poll", daemon=True)
        self._thread.start()
        logger.info("Signal channel started for %s", self._number)

    async def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        await self._close_and_drain_threadsafe_futures()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            await asyncio.to_thread(thread.join, 5.0)

    async def send(self, msg: OutboundMessage) -> None:
        recipient = msg.chat_id
        if recipient.startswith("dm:"):
            recipients = [recipient[3:]]
        elif recipient.startswith("group:"):
            recipients = [recipient[6:]]
        else:
            recipients = [recipient]
        for chunk in _chunk_text(msg.text or ""):
            body = {"number": self._number, "recipients": recipients, "message": chunk}
            try:
                await asyncio.to_thread(self._http_json, "POST", "/v1/send", body)
            except RuntimeError as exc:
                logger.warning("Signal send failed: %s", exc)
                return

    def receive_poll_result_for_test(self, payload: Any) -> list[InboundMessage]:
        """Parse-only entry point used by unit tests (no network, no bus)."""
        out = []
        for envelope in _parse_envelopes(payload):
            parsed = _envelope_to_chat(envelope)
            if parsed is None:
                continue
            sender, chat_id, text = parsed
            out.append(self._make_inbound(chat_id=chat_id, user_id=sender, text=text))
        return out
