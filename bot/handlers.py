"""Telegram command + callback-query handlers.

Every handler checks the incoming chat id against TELEGRAM_CHAT_ID before
acting — this bot is built for one owner's personal chat, not a group, so
this is a cheap guard against a stray/forwarded callback from anywhere
else ever mutating Notion state.
"""
from __future__ import annotations

import html
import logging
import os

from telegram import Update
from telegram.ext import ContextTypes

from integrations import get_status_counts, query_by_status, query_pending_opportunities, update_notion_status

from .scheduler import hunt_and_broadcast

log = logging.getLogger(__name__)

_STATUS_BY_ACTION = {"applied": "Applied", "accepted": "Accepted", "rejected": "Rejected"}
_CONFIRMATION_BY_STATUS = {
    "Applied": "✅ Başvurdum olarak işaretlendi.",
    "Accepted": "🎉 Kabul Edildim olarak işaretlendi.",
    "Rejected": "❌ Reddedildim olarak işaretlendi.",
}


def _is_owner(update: Update) -> bool:
    expected = os.environ.get("TELEGRAM_CHAT_ID")
    chat = update.effective_chat
    return bool(expected) and chat is not None and str(chat.id) == str(expected)


async def handle_status_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # ack immediately — Telegram shows a loading spinner on the button until this fires

    if not _is_owner(update):
        log.warning("Ignoring status-button callback from unexpected chat %s", update.effective_chat)
        return

    try:
        action, page_id = (query.data or "").split(":", 1)
    except ValueError:
        return
    status = _STATUS_BY_ACTION.get(action)
    if not status:
        return

    ok = update_notion_status(page_id, status)
    confirmation = _CONFIRMATION_BY_STATUS[status] if ok else "⚠️ Notion güncellenemedi, lütfen tekrar dene."

    # text_html reconstructs the original HTML-formatted text from the
    # message's entities, so the confirmation is appended below the
    # existing content rather than replacing it. reply_markup=None removes
    # the buttons so the same opportunity can't be double-clicked.
    original = query.message.text_html or query.message.text or ""
    await query.edit_message_text(f"{original}\n\n{confirmation}", parse_mode="HTML", reply_markup=None)


async def handle_basvurularim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return

    items = query_by_status("Applied")
    if not items:
        await update.message.reply_text("Başvurduğun bir şey görünmüyor.")
        return

    lines = [f"<b>Başvurduğun {len(items)} fırsat:</b>"]
    for item in items:
        title = html.escape(item["title"])
        lines.append(f'• <a href="{item["url"]}">{title}</a>' if item["url"] else f"• {title}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


async def handle_bekleyenler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/bekleyenler — everything still Status="New" with a deadline that
    hasn't passed yet, i.e. what you haven't applied to but still could."""
    if not _is_owner(update):
        return

    items = query_pending_opportunities()
    if not items:
        await update.message.reply_text("Bekleyen fırsat yok — güncelsin! 🎉")
        return

    lines = [f"<b>📋 {len(items)} bekleyen fırsat:</b>"]
    for i, item in enumerate(items, start=1):
        title = html.escape(item["title"])
        entry = f'{i}. <a href="{item["url"]}">{title}</a>' if item["url"] else f"{i}. {title}"
        lines.append(f"{entry} — {item['deadline_display']}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


async def handle_rapor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/rapor — a gamified stats dashboard from the Notion Status counts."""
    if not _is_owner(update):
        return

    counts = get_status_counts()
    applied, accepted, rejected = counts.get("Applied", 0), counts.get("Accepted", 0), counts.get("Rejected", 0)
    total_applied = applied + accepted + rejected

    text = (
        "📊 <b>Kariyer İstatistiğin:</b>\n\n"
        f"🚀 Toplam Başvurulan: {total_applied}\n"
        f"⏳ Sonuç Beklenen: {applied}\n"
        f"🎉 Kabul: {accepted}\n"
        f"❌ Ret: {rejected}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def handle_tara(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tara — manually force-runs the Opportunity Hunter (scrape + Gemini
    gatekeeper + Notion + broadcast) immediately, without waiting for the
    09:00/21:00 schedule. Shares its logic with the scheduled job — see
    bot/scheduler.py's hunt_and_broadcast()."""
    if not _is_owner(update):
        return

    await update.message.reply_text("🔍 İnternet taranıyor, lütfen bekle...")
    chat_id = str(update.effective_chat.id)
    count = await hunt_and_broadcast(context.bot, chat_id)
    if count == 0:
        await update.message.reply_text("Şu an için yeni bir fırsat bulunamadı.")


async def handle_yardim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/yardim or /start — a directory of every available command. Also see
    bot/app.py's BOT_COMMANDS, which registers the same list with Telegram's
    native menu button via set_my_commands()."""
    if not _is_owner(update):
        return

    text = (
        "🤖 <b>Kariyer Asistanına Hoş Geldin!</b>\n"
        "İşte sana yardımcı olabileceğim komutlar:\n\n"
        "📌 /basvurularim - Aktif olarak sonucunu beklediğin başvuruları listeler.\n"
        "📥 /bekleyenler - Henüz başvurmadığın ama süresi geçmemiş fırsatları gösterir.\n"
        "📊 /rapor - Başvuru ve kabul/ret istatistiklerini özetler.\n"
        "🔍 /tara - Zamanlayıcıyı beklemeden interneti anında manuel tarar.\n"
        "❓ /yardim - Bu komut menüsünü gösterir."
    )
    await update.message.reply_text(text, parse_mode="HTML")
