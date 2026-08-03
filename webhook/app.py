"""Sync Flask webhook: the free (no credit card) alternative to bot/app.py's
long-lived polling process. Deploy this on PythonAnywhere's free Web App
tier — see README "Deploying for free (no credit card)".

Why this exists: PythonAnywhere's free tier can't run a long-lived polling
process (that needs a paid "always-on task"), but it CAN host a normal web
app for free — reachable 24/7, just per-request rather than continuously
running. Telegram webhooks fit that perfectly: Telegram calls *us* only
when something happens (a button tap, a command), instead of us
continuously asking Telegram "anything new?".

Split responsibilities:
  - The actual scraping pipeline (Reddit/Devpost/GitHub/HN/Gemini) needs
    outbound internet access PythonAnywhere's free tier doesn't fully
    allow (Devpost isn't on its allowlist) — that stays on GitHub Actions
    (.github/workflows/opportunity_hunter.yml + morning_briefing.yml),
    which has unrestricted internet and is free/unlimited for a public repo.
  - This webhook ONLY handles inbound Telegram updates: button taps
    (-> Notion status update) and read-only commands (-> Notion query).
    Both api.notion.com and api.telegram.org ARE on PythonAnywhere's free
    allowlist, so this works within the free tier's restrictions.
  - /tara can't run the pipeline itself (same allowlist problem) — it
    triggers the Opportunity Hunter GitHub Actions workflow on demand via
    the GitHub API (api.github.com is also allowlisted) and confirms the
    trigger; the actual "found N opportunities" / "nothing new" message
    arrives a little later, sent by that workflow run itself (see
    scripts/run_opportunity_hunter.py's MANUAL_TRIGGER handling).

Set up the webhook once (from your own machine, not PythonAnywhere):
    python -m webhook.register YOUR_PYTHONANYWHERE_URL/webhook
"""
from __future__ import annotations

import html
import logging
import os

import requests
from dotenv import load_dotenv
from flask import Flask, abort, request

load_dotenv()

from integrations import (
    get_status_counts,
    query_by_status,
    query_pending_opportunities,
    send_telegram_text,
    update_notion_status,
)

from .telegram_api import answer_callback_query, edit_message_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("webhook.app")

app = Flask(__name__)

_STATUS_BY_ACTION = {"applied": "Applied", "accepted": "Accepted", "rejected": "Rejected"}
_CONFIRMATION_BY_STATUS = {
    "Applied": "✅ Başvurdum olarak işaretlendi.",
    "Accepted": "🎉 Kabul Edildim olarak işaretlendi.",
    "Rejected": "❌ Reddedildim olarak işaretlendi.",
}

_HELP_TEXT = (
    "🤖 <b>Kariyer Asistanına Hoş Geldin!</b>\n"
    "İşte sana yardımcı olabileceğim komutlar:\n\n"
    "📌 /basvurularim - Aktif olarak sonucunu beklediğin başvuruları listeler.\n"
    "📥 /bekleyenler - Henüz başvurmadığın ama süresi geçmemiş fırsatları gösterir.\n"
    "📊 /rapor - Başvuru ve kabul/ret istatistiklerini özetler.\n"
    "🔍 /tara - Zamanlayıcıyı beklemeden interneti anında manuel tarar.\n"
    "❓ /yardim - Bu komut menüsünü gösterir."
)


def _is_owner_chat(chat_id) -> bool:
    expected = os.environ.get("TELEGRAM_CHAT_ID")
    return bool(expected) and str(chat_id) == str(expected)


def _trigger_opportunity_hunter_workflow() -> bool:
    """Fires the Opportunity Hunter GitHub Actions workflow via
    workflow_dispatch — this webhook can't run the scrape pipeline itself
    (PythonAnywhere's free tier doesn't allow-list every source we hit)."""
    token = os.environ.get("GH_DISPATCH_TOKEN")
    repo = os.environ.get("GH_REPO")
    if not token or not repo:
        log.warning("GH_DISPATCH_TOKEN/GH_REPO not set — can't trigger the Opportunity Hunter workflow")
        return False

    url = f"https://api.github.com/repos/{repo}/actions/workflows/opportunity_hunter.yml/dispatches"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        resp = requests.post(url, headers=headers, json={"ref": "main"}, timeout=15)
        return resp.status_code == 204
    except requests.RequestException as exc:
        log.warning("Failed to trigger Opportunity Hunter workflow: %s", exc)
        return False


def _handle_callback_query(cq: dict) -> None:
    answer_callback_query(cq["id"])  # ack immediately, regardless of ownership

    message = cq.get("message") or {}
    chat = message.get("chat") or {}
    if not _is_owner_chat(chat.get("id")):
        log.warning("Ignoring status-button callback from unexpected chat %s", chat.get("id"))
        return

    try:
        action, page_id = (cq.get("data") or "").split(":", 1)
    except ValueError:
        return
    status = _STATUS_BY_ACTION.get(action)
    if not status:
        return

    ok = update_notion_status(page_id, status)
    confirmation = _CONFIRMATION_BY_STATUS[status] if ok else "⚠️ Notion güncellenemedi, lütfen tekrar dene."

    # Plain `text` (entities stripped) rather than reconstructed HTML — a
    # minor cosmetic simplification vs bot/handlers.py's text_html trick,
    # traded for not needing to hand-parse Telegram's `entities` array here.
    original = html.escape(message.get("text", ""))
    edit_message_text(chat["id"], message["message_id"], f"{original}\n\n{confirmation}", reply_markup=None)


def _reply_basvurularim() -> None:
    items = query_by_status("Applied")
    if not items:
        send_telegram_text("Başvurduğun bir şey görünmüyor.")
        return
    lines = [f"<b>Başvurduğun {len(items)} fırsat:</b>"]
    for item in items:
        title = html.escape(item["title"])
        lines.append(f'• <a href="{item["url"]}">{title}</a>' if item["url"] else f"• {title}")
    send_telegram_text("\n".join(lines))


def _reply_bekleyenler() -> None:
    items = query_pending_opportunities()
    if not items:
        send_telegram_text("Bekleyen fırsat yok — güncelsin! 🎉")
        return
    lines = [f"<b>📋 {len(items)} bekleyen fırsat:</b>"]
    for i, item in enumerate(items, start=1):
        title = html.escape(item["title"])
        entry = f'{i}. <a href="{item["url"]}">{title}</a>' if item["url"] else f"{i}. {title}"
        lines.append(f"{entry} — {item['deadline_display']}")
    send_telegram_text("\n".join(lines))


def _reply_rapor() -> None:
    counts = get_status_counts()
    applied, accepted, rejected = counts.get("Applied", 0), counts.get("Accepted", 0), counts.get("Rejected", 0)
    text = (
        "📊 <b>Kariyer İstatistiğin:</b>\n\n"
        f"🚀 Toplam Başvurulan: {applied + accepted + rejected}\n"
        f"⏳ Sonuç Beklenen: {applied}\n"
        f"🎉 Kabul: {accepted}\n"
        f"❌ Ret: {rejected}"
    )
    send_telegram_text(text)


def _reply_tara() -> None:
    send_telegram_text("🔍 İnternet taranıyor, lütfen bekle...")
    if not _trigger_opportunity_hunter_workflow():
        send_telegram_text("⚠️ Tarama tetiklenemedi, lütfen tekrar dene.")
    # The "N new opportunities" / "nothing new" follow-up is sent by the
    # GitHub Actions run itself once it finishes — this handler can't wait
    # for it (the scan takes longer than a webhook response should).


def _handle_message(message: dict) -> None:
    chat = message.get("chat") or {}
    if not _is_owner_chat(chat.get("id")):
        return

    text = (message.get("text") or "").strip()
    if not text.startswith("/"):
        return
    command = text.split()[0].split("@")[0]  # strip args and an @BotName suffix

    if command == "/basvurularim":
        _reply_basvurularim()
    elif command == "/bekleyenler":
        _reply_bekleyenler()
    elif command == "/rapor":
        _reply_rapor()
    elif command == "/tara":
        _reply_tara()
    elif command in ("/yardim", "/start"):
        send_telegram_text(_HELP_TEXT)


@app.route("/webhook", methods=["POST"])
def webhook():
    # Telegram sends this header back only when the same secret was set via
    # setWebhook's secret_token — confirms the request genuinely came from
    # Telegram rather than a stranger POSTing to our public URL.
    expected_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if not expected_secret or request.headers.get("X-Telegram-Bot-Api-Secret-Token") != expected_secret:
        abort(403)

    update = request.get_json(force=True, silent=True) or {}
    try:
        if "callback_query" in update:
            _handle_callback_query(update["callback_query"])
        elif "message" in update:
            _handle_message(update["message"])
    except Exception:  # noqa: BLE001 - a bad update must never take the webhook down
        log.exception("Failed to handle update: %r", update)

    return "ok"


@app.route("/health", methods=["GET"])
def health():
    return "ok"
