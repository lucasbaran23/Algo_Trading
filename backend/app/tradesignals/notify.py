"""Outbound notifications for trade signals (all optional, configured by the user in .env).

  DISCORD_WEBHOOK_URL     Discord channel webhook
  TELEGRAM_BOT_TOKEN      Telegram bot token  +  TELEGRAM_CHAT_ID
  SIGNAL_WEBHOOK_URL      any URL: receives the signal as JSON (Zapier, n8n, your own server...)
  NOTIFY_EVENTS           which events to push (default: activated,tp1,closed)

Messages are sent from a background thread so a slow endpoint never stalls the price feed.
"""
from __future__ import annotations

import logging
import os
import threading

import httpx

log = logging.getLogger(__name__)


def channels() -> dict:
    return {
        "discord": bool(os.getenv("DISCORD_WEBHOOK_URL")),
        "telegram": bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")),
        "webhook": bool(os.getenv("SIGNAL_WEBHOOK_URL")),
        "events": [e.strip() for e in os.getenv("NOTIFY_EVENTS", "activated,tp1,closed").split(",") if e.strip()],
    }


def _send(text: str, payload: dict) -> list[str]:
    errors = []
    with httpx.Client(timeout=8) as c:
        if url := os.getenv("DISCORD_WEBHOOK_URL"):
            try:
                c.post(url, json={"content": text[:1900]}).raise_for_status()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"discord: {exc}")
        tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
        if tok and chat:
            try:
                c.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": chat, "text": text}).raise_for_status()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"telegram: {exc}")
        if url := os.getenv("SIGNAL_WEBHOOK_URL"):
            try:
                c.post(url, json=payload).raise_for_status()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"webhook: {exc}")
    for e in errors:
        log.warning("Signal notification failed: %s", e)
    return errors


def push(event: str, text: str, payload: dict, force: bool = False) -> bool:
    ch = channels()
    if not (ch["discord"] or ch["telegram"] or ch["webhook"]):
        return False
    if not force and event not in ch["events"]:
        return False
    threading.Thread(target=_send, args=(text, payload), daemon=True).start()
    return True
