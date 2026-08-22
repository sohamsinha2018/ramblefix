#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SITE_INDEX="${RAMBLEFIX_SITE_INDEX:-$ROOT/site/index.html}"

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "missing required env var: $name" >&2
    exit 2
  fi
}

require_env RAMBLEFIX_LITE_DOWNLOAD_URL
require_env RAMBLEFIX_HI_DOWNLOAD_URL
require_env RAMBLEFIX_GITHUB_URL
require_env RAMBLEFIX_DISCUSSIONS_URL

SITE_INDEX="$SITE_INDEX" \
RAMBLEFIX_LITE_DOWNLOAD_URL="$RAMBLEFIX_LITE_DOWNLOAD_URL" \
RAMBLEFIX_HI_DOWNLOAD_URL="$RAMBLEFIX_HI_DOWNLOAD_URL" \
RAMBLEFIX_GITHUB_URL="$RAMBLEFIX_GITHUB_URL" \
RAMBLEFIX_DISCUSSIONS_URL="$RAMBLEFIX_DISCUSSIONS_URL" \
RAMBLEFIX_DISCORD_URL="${RAMBLEFIX_DISCORD_URL:-}" \
python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["SITE_INDEX"])
html = path.read_text(encoding="utf-8")
feedback_url = os.environ["RAMBLEFIX_DISCORD_URL"] or os.environ["RAMBLEFIX_DISCUSSIONS_URL"]
feedback_label = "Join Discord" if os.environ["RAMBLEFIX_DISCORD_URL"] else "Ask on GitHub"
lite_url = os.environ["RAMBLEFIX_LITE_DOWNLOAD_URL"]
hi_url = os.environ["RAMBLEFIX_HI_DOWNLOAD_URL"]

replacements = {
    '<a class="button primary" href="#" aria-label="Download RambleFix DMG placeholder">Download for Mac</a>':
        f'<a class="button primary" href="{lite_url}" aria-label="Download RambleFix Lite DMG">Download — English</a>',
    '<span class="button ghost disabled" data-download-pending="true" aria-disabled="true">Download after signed build</span>':
        f'<a class="button primary" href="{lite_url}" aria-label="Download RambleFix Lite DMG">Download — English</a>',
    '<span class="button secondary disabled" data-download-pending="true" aria-disabled="true">Signed Mac build coming soon</span>':
        f'<a class="button primary" href="{hi_url}" aria-label="Download RambleFix HI DMG">Download — English + Hindi</a>',
    '<a class="button ghost" href="#" aria-label="GitHub repository placeholder">Star on GitHub</a>':
        f'<a class="button ghost" href="{os.environ["RAMBLEFIX_GITHUB_URL"]}" aria-label="RambleFix GitHub repository">Star on GitHub</a>',
    '<a class="nav-download" href="https://github.com/sohamsinha2018/ramblefix">View source</a>':
        f'<a class="nav-download" href="{lite_url}">Download</a>',
    '<a class="button primary" href="https://github.com/sohamsinha2018/ramblefix">View the source</a>':
        f'<a class="button primary" href="{lite_url}" aria-label="Download RambleFix Lite DMG">Download — English</a>',
    '<a class="button primary" href="#">Open GitHub Discussions</a>':
        f'<a class="button primary" href="{os.environ["RAMBLEFIX_DISCUSSIONS_URL"]}">Open GitHub Discussions</a>',
    '<a class="button ghost" href="#">Join Discord</a>':
        f'<a class="button ghost" href="{feedback_url}">{feedback_label}</a>',
    '<p class="fine-print">Replace these placeholders after the public repo and community links exist.</p>':
        '<p class="fine-print">Public feedback is tracked through GitHub Discussions.</p>',
    'https://github.com/sohamsinha2018/ramblefix/releases/download/v0.1.0/RambleFix-Lite-0.1.0.dmg':
        lite_url,
    'https://github.com/sohamsinha2018/ramblefix/releases/download/v0.1.0/RambleFix-HI-0.1.0.dmg':
        hi_url,
    'href="https://github.com/sohamsinha2018/ramblefix/releases"':
        f'href="{lite_url}"',
}

changed = 0
for needle, replacement in replacements.items():
    if needle in html:
        html = html.replace(needle, replacement)
        changed += 1

if changed == 0:
    raise SystemExit("site link placeholders not found or already configured")

path.write_text(html, encoding="utf-8")
PY

echo "configured site links in $SITE_INDEX"
