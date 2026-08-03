"""Inline keyboard builders for the interactive Telegram flows.

Notion page ids are embedded directly in callback_data (e.g.
"applied:3b18a390-27d6-813b-9b79-d80528be2c70") rather than a server-side
lookup table. This is safe: a Notion page id isn't a secret — on its own it
grants no access to anything without the bot's own NOTION_TOKEN — and a
36-char UUID plus a short action prefix comfortably fits Telegram's 64-byte
callback_data limit. bot/handlers.py additionally checks the clicking
chat's id against TELEGRAM_CHAT_ID before acting on any callback, so even a
guessed/replayed callback_data can't mutate Notion from outside your own
chat with the bot.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def applied_keyboard(page_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Başvurdum", callback_data=f"applied:{page_id}")]])


def result_keyboard(page_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎉 Kabul Edildim", callback_data=f"accepted:{page_id}"),
                InlineKeyboardButton("❌ Reddedildim", callback_data=f"rejected:{page_id}"),
            ]
        ]
    )
