#!/usr/bin/env bash
# One-shot deployment: turns this local folder into a private GitHub repo
# with the daily cron job fully wired up (Notion database created, secrets
# set, first run triggered). Run this yourself from inside intern/:
#
#   ./setup.sh
#
# Requires: gh CLI installed and authenticated (gh auth login), git, python.
# Re-running is safe — it reuses whatever's already in .env / already
# pushed instead of redoing it.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) not found. Install it first: https://cli.github.com" >&2
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "gh isn't authenticated yet. Run: gh auth login" >&2
  exit 1
fi

ENV_FILE=".env"
touch "$ENV_FILE"

env_value() {
  grep -m1 "^$1=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true
}

# Writes/updates a key in .env (portable in-place edit).
write_env() {
  local name="$1" value="$2"
  if grep -q "^$name=" "$ENV_FILE" 2>/dev/null; then
    sed -i.bak "s|^$name=.*|$name=$value|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
  else
    echo "$name=$value" >> "$ENV_FILE"
  fi
}

# Prompts for a secret only if it isn't already in .env (input hidden).
prompt_secret() {
  local name="$1" prompt_text="$2" existing value
  existing=$(env_value "$name")
  if [ -n "$existing" ]; then
    echo "$name already set in .env, reusing it."
    value="$existing"
  else
    read -r -s -p "$prompt_text: " value
    echo
    write_env "$name" "$value"
  fi
  printf '%s' "$value"
}

echo "== 1. Credentials (Gemini, Telegram, Notion) =="
GEMINI_API_KEY=$(prompt_secret GEMINI_API_KEY "Gemini API key (aistudio.google.com/apikey)")
TELEGRAM_BOT_TOKEN=$(prompt_secret TELEGRAM_BOT_TOKEN "Telegram bot token (from @BotFather)")
TELEGRAM_CHAT_ID=$(prompt_secret TELEGRAM_CHAT_ID "Telegram chat ID")
NOTION_TOKEN=$(prompt_secret NOTION_TOKEN "Notion internal integration token")
NOTION_PARENT_PAGE_ID=$(prompt_secret NOTION_PARENT_PAGE_ID "Notion parent page ID (page shared with your integration)")

NOTION_DATABASE_ID=$(env_value NOTION_DATABASE_ID)
if [ -z "$NOTION_DATABASE_ID" ]; then
  echo "== 2. Creating the Notion database =="
  python -m pip install -q -r requirements.txt
  NOTION_DATABASE_ID=$(NOTION_TOKEN="$NOTION_TOKEN" NOTION_PARENT_PAGE_ID="$NOTION_PARENT_PAGE_ID" python scripts/notion_create_database.py)
  if [ -z "$NOTION_DATABASE_ID" ]; then
    echo "Failed to create the Notion database — check the error above and re-run." >&2
    exit 1
  fi
  write_env "NOTION_DATABASE_ID" "$NOTION_DATABASE_ID"
  echo "Notion database ready: $NOTION_DATABASE_ID"
else
  echo "NOTION_DATABASE_ID already set in .env, reusing it: $NOTION_DATABASE_ID"
fi

echo "== 3. Git + GitHub repo =="
if [ ! -d .git ]; then
  git init -b main
fi

read -r -p "GitHub repo name [intern-opportunity-tracker]: " REPO_NAME
REPO_NAME="${REPO_NAME:-intern-opportunity-tracker}"

git add -A
git commit -m "chore: initial commit" || echo "Nothing new to commit."

if ! git remote get-url origin >/dev/null 2>&1; then
  gh repo create "$REPO_NAME" --private --source=. --remote=origin --push
else
  git push -u origin HEAD
fi

echo "== 4. Setting GitHub Actions secrets =="
gh secret set GEMINI_API_KEY --body "$GEMINI_API_KEY"
gh secret set TELEGRAM_BOT_TOKEN --body "$TELEGRAM_BOT_TOKEN"
gh secret set TELEGRAM_CHAT_ID --body "$TELEGRAM_CHAT_ID"
gh secret set NOTION_TOKEN --body "$NOTION_TOKEN"
gh secret set NOTION_DATABASE_ID --body "$NOTION_DATABASE_ID"

echo "== 5. Triggering first run =="
gh workflow run daily.yml
echo "Done. Watch it with: gh run watch"
