#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAILURES=0
CHECK_OUTPUT="$(mktemp)"
trap 'rm -f "$CHECK_OUTPUT"' EXIT

ok() {
  printf 'OK   %s\n' "$*"
}

fail() {
  printf 'FAIL %s\n' "$*" >&2
  FAILURES=$((FAILURES + 1))
}

warn() {
  printf 'WARN %s\n' "$*" >&2
}

section() {
  printf '\n== %s ==\n' "$*"
}

run_quiet() {
  "$@" >"$CHECK_OUTPUT" 2>&1
}

section "Source and claims"
if run_quiet "$ROOT/script/audit_public_source_surface.sh"; then
  ok "public source surface is safe to stage"
else
  fail "public source surface audit failed"
  sed -n '1,12p' "$CHECK_OUTPUT" >&2
fi

if run_quiet python3 "$ROOT/scripts/audit_benchmark_claims.py"; then
  ok "benchmark claims audit passes"
else
  fail "benchmark claims audit failed"
  sed -n '1,12p' "$CHECK_OUTPUT" >&2
fi

section "Machine and eval"
if run_quiet "$ROOT/script/audit_eval_machine_health.sh" --strict; then
  ok "eval machine is clean for publishable latency"
else
  fail "eval machine is dirty; reboot before publishable latency eval"
  sed -n '1,12p' "$CHECK_OUTPUT" >&2
fi

section "Site and repo"
SITE="$ROOT/site/index.html"
if [[ ! -f "$SITE" ]]; then
  fail "site/index.html missing"
elif rg -q 'href="#"|placeholder|Replace these placeholders' "$SITE"; then
  fail "site still has placeholder links; run script/configure_site_links.sh after repo/release exists"
else
  ok "site links are configured"
fi

if git -C "$ROOT" remote get-url origin >"$CHECK_OUTPUT" 2>&1; then
  ok "git origin exists: $(cat "$CHECK_OUTPUT")"
else
  fail "git origin missing; create public GitHub repo and add remote"
fi

if command -v gh >/dev/null 2>&1 && gh auth status >"$CHECK_OUTPUT" 2>&1; then
  ok "GitHub CLI is authenticated"
else
  warn "GitHub CLI auth not available; repo/release setup may be manual"
fi

section "Signing and notarization"
IDENTITY="${RAMBLEFIX_CODESIGN_IDENTITY:-}"
if [[ -z "$IDENTITY" ]]; then
  IDENTITY="$(security find-identity -p codesigning -v 2>/dev/null | awk -F '"' '/"Developer ID Application:/ { print $2; exit }')"
fi
if [[ -n "$IDENTITY" && "$IDENTITY" == Developer\ ID\ Application:* ]]; then
  ok "Developer ID identity available: $IDENTITY"
else
  fail "Developer ID Application certificate missing"
fi

if [[ -n "${RAMBLEFIX_NOTARY_PROFILE:-}" ]]; then
  if xcrun notarytool history --keychain-profile "$RAMBLEFIX_NOTARY_PROFILE" >"$CHECK_OUTPUT" 2>&1; then
    ok "notary profile works: $RAMBLEFIX_NOTARY_PROFILE"
  else
    fail "notary profile is set but not usable: $RAMBLEFIX_NOTARY_PROFILE"
    sed -n '1,12p' "$CHECK_OUTPUT" >&2
  fi
else
  fail "RAMBLEFIX_NOTARY_PROFILE missing"
fi

APP="$ROOT/dist/release/DictaHue.app"
RUNTIME="$APP/Contents/Resources/RambleFixRuntime"
if [[ -x "$RUNTIME/.venv/bin/python" ]]; then
  ok "current artifact embeds Python runtime"
elif [[ "${RAMBLEFIX_PACKAGE_EMBED_VENV:-0}" == "1" ]]; then
  ok "next public package build is configured to embed Python runtime"
else
  fail "packaged runtime missing .venv/bin/python; build with RAMBLEFIX_PACKAGE_EMBED_VENV=1 for public one-click app"
fi

section "Current artifact"
if [[ -d "$APP" ]]; then
  if run_quiet "$ROOT/script/audit_release_security.sh" "$APP" --local; then
    ok "current local release artifact passes local security"
  else
    fail "current local release artifact fails local security"
    sed -n '1,12p' "$CHECK_OUTPUT" >&2
  fi
else
  fail "current release app missing: $APP"
fi

echo
if [[ "$FAILURES" -gt 0 ]]; then
  echo "public launch blockers: $FAILURES"
  exit 1
fi

echo "public launch blockers: 0"
