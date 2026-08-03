"""One-time setup helper: creates the Notion database this project pushes
opportunities into, with the right property schema, under a page you've
already shared with your integration.

Usage:
    python scripts/notion_create_database.py

Reads NOTION_TOKEN and NOTION_PARENT_PAGE_ID from the environment (or a
local .env file). On success, prints ONLY the new database's ID to stdout
(everything else goes to stderr) so it's safe to capture directly:

    NOTION_DATABASE_ID=$(python scripts/notion_create_database.py)

Put that value in NOTION_DATABASE_ID (local .env and the GitHub secret).
setup.sh runs this automatically — you shouldn't normally need to run it
by hand.
"""
from __future__ import annotations

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

NOTION_VERSION = "2022-06-28"
API_BASE = "https://api.notion.com/v1"
REQUEST_TIMEOUT = 15

_DATABASE_SCHEMA = {
    "Name": {"title": {}},
    "Application Link": {"url": {}},
    "Eligibility": {"rich_text": {}},
    "Summary": {"rich_text": {}},
    "Turkish CV Summary": {"rich_text": {}},
    "Deadline": {"date": {}},
    "Start Date": {"date": {}},
    "Announcement Date": {"date": {}},
    "Source": {
        "select": {
            "options": [
                {"name": "reddit"},
                {"name": "devpost"},
                {"name": "github"},
            ]
        }
    },
    "Score": {"number": {"format": "number"}},
    "Status": {
        "select": {
            "options": [
                {"name": "New"},
                {"name": "Applied"},
                {"name": "Accepted"},
                {"name": "Rejected"},
            ]
        }
    },
}


def main() -> int:
    token = os.environ.get("NOTION_TOKEN")
    parent_page_id = os.environ.get("NOTION_PARENT_PAGE_ID")
    if not token or not parent_page_id:
        print("Set NOTION_TOKEN and NOTION_PARENT_PAGE_ID first (in .env or the shell env).", file=sys.stderr)
        return 1

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    body = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": "Intern Opportunity Tracker"}}],
        "properties": _DATABASE_SCHEMA,
    }

    try:
        resp = requests.post(f"{API_BASE}/databases", headers=headers, json=body, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        print(f"Notion API request failed: {exc}", file=sys.stderr)
        return 1

    if not resp.ok:
        print(f"Notion API error {resp.status_code}: {resp.text}", file=sys.stderr)
        return 1

    database_id = resp.json()["id"]
    print(f"Created Notion database: {database_id}", file=sys.stderr)
    print(database_id)  # stdout: just the ID, for command substitution
    return 0


if __name__ == "__main__":
    sys.exit(main())
