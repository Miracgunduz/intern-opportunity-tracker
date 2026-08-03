"""One-time setup: tells Telegram to start sending updates to your deployed
webhook URL instead of expecting you to poll for them.

Usage (run locally, once, after your PythonAnywhere app is live):
    python -m webhook.register https://<you>.pythonanywhere.com/webhook

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET from .env — the same
secret must also be set as an environment variable on PythonAnywhere
(webhook/app.py checks incoming requests against it).
"""
from __future__ import annotations

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python -m webhook.register <webhook-url>", file=sys.stderr)
        return 1

    webhook_url = sys.argv[1]
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    secret = os.environ["TELEGRAM_WEBHOOK_SECRET"]

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json={
            "url": webhook_url,
            "secret_token": secret,
            "allowed_updates": ["message", "callback_query"],
        },
        timeout=15,
    )
    print(resp.status_code, resp.json())

    info = requests.get(f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=15)
    print(info.json())
    return 0 if resp.ok else 1


if __name__ == "__main__":
    sys.exit(main())
