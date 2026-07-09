#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SITE_INDEX="${DICTAHUE_SITE_INDEX:-$ROOT/site/index.html}"

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "missing required env var: $name" >&2
    exit 2
  fi
}

require_env DICTAHUE_DOWNLOAD_URL
require_env DICTAHUE_GITHUB_URL
require_env DICTAHUE_DISCUSSIONS_URL

SITE_INDEX="$SITE_INDEX" \
DICTAHUE_DOWNLOAD_URL="$DICTAHUE_DOWNLOAD_URL" \
DICTAHUE_GITHUB_URL="$DICTAHUE_GITHUB_URL" \
DICTAHUE_DISCUSSIONS_URL="$DICTAHUE_DISCUSSIONS_URL" \
DICTAHUE_DISCORD_URL="${DICTAHUE_DISCORD_URL:-}" \
python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["SITE_INDEX"])
html = path.read_text(encoding="utf-8")
feedback_url = os.environ["DICTAHUE_DISCORD_URL"] or os.environ["DICTAHUE_DISCUSSIONS_URL"]
feedback_label = "Join Discord" if os.environ["DICTAHUE_DISCORD_URL"] else "Ask on GitHub"

replacements = {
    '<a class="button primary" href="#" aria-label="Download DictaHue DMG placeholder">Download for Mac</a>':
        f'<a class="button primary" href="{os.environ["DICTAHUE_DOWNLOAD_URL"]}" aria-label="Download DictaHue DMG">Download for Mac</a>',
    '<a class="button ghost" href="#" aria-label="GitHub repository placeholder">Star on GitHub</a>':
        f'<a class="button ghost" href="{os.environ["DICTAHUE_GITHUB_URL"]}" aria-label="DictaHue GitHub repository">Star on GitHub</a>',
    '<a class="button primary" href="#">Open GitHub Discussions</a>':
        f'<a class="button primary" href="{os.environ["DICTAHUE_DISCUSSIONS_URL"]}">Open GitHub Discussions</a>',
    '<a class="button ghost" href="#">Join Discord</a>':
        f'<a class="button ghost" href="{feedback_url}">{feedback_label}</a>',
    '<p class="fine-print">Replace these placeholders after the public repo and community links exist.</p>':
        '<p class="fine-print">Public feedback is tracked through GitHub Discussions.</p>',
}

missing = [needle for needle in replacements if needle not in html]
if missing:
    raise SystemExit("site link placeholders not found or already configured")

for needle, replacement in replacements.items():
    html = html.replace(needle, replacement)

path.write_text(html, encoding="utf-8")
PY

echo "configured site links in $SITE_INDEX"
