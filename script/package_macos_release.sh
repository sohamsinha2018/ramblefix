#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="${RAMBLEFIX_APP_NAME:-DictaHue}"
BUNDLE_ID="${RAMBLEFIX_BUNDLE_ID:-app.dictahue.DictaHue}"
EXECUTABLE_NAME="${RAMBLEFIX_EXECUTABLE_NAME:-DictaHue}"
VERSION="${RAMBLEFIX_RELEASE_VERSION:-0.1.0}"
RELEASE_DIR="${RAMBLEFIX_RELEASE_DIR:-$ROOT/dist/release}"
APP="$RELEASE_DIR/$APP_NAME.app"
DMG="$RELEASE_DIR/$APP_NAME-$VERSION.dmg"
ZIP="$RELEASE_DIR/$APP_NAME-$VERSION.zip"
SUMS="$RELEASE_DIR/$APP_NAME-$VERSION.SHA256SUMS"
RUNTIME="$APP/Contents/Resources/RambleFixRuntime"

require_developer_id_for_release() {
  if [[ "${RAMBLEFIX_PUBLIC_RELEASE:-0}" != "1" ]]; then
    return
  fi
  if [[ -z "${RAMBLEFIX_CODESIGN_IDENTITY:-}" ]]; then
    echo "RAMBLEFIX_PUBLIC_RELEASE=1 requires RAMBLEFIX_CODESIGN_IDENTITY='Developer ID Application: ...'" >&2
    exit 2
  fi
  if [[ "$RAMBLEFIX_CODESIGN_IDENTITY" != Developer\ ID\ Application:* ]]; then
    echo "Public release must be signed with a Developer ID Application identity, got: $RAMBLEFIX_CODESIGN_IDENTITY" >&2
    exit 2
  fi
  if [[ "${RAMBLEFIX_NOTARIZE:-0}" != "1" ]]; then
    echo "RAMBLEFIX_PUBLIC_RELEASE=1 requires RAMBLEFIX_NOTARIZE=1" >&2
    exit 2
  fi
  if [[ -z "${RAMBLEFIX_NOTARY_PROFILE:-}" ]]; then
    echo "RAMBLEFIX_PUBLIC_RELEASE=1 requires RAMBLEFIX_NOTARY_PROFILE for xcrun notarytool" >&2
    exit 2
  fi
  if [[ "${RAMBLEFIX_PACKAGE_EMBED_RUNTIME:-1}" != "1" ]]; then
    echo "RAMBLEFIX_PUBLIC_RELEASE=1 requires RAMBLEFIX_PACKAGE_EMBED_RUNTIME=1" >&2
    exit 2
  fi
  if [[ "${RAMBLEFIX_PACKAGE_EMBED_VENV:-0}" != "1" ]]; then
    echo "RAMBLEFIX_PUBLIC_RELEASE=1 requires RAMBLEFIX_PACKAGE_EMBED_VENV=1 or a signed first-run bootstrap" >&2
    exit 2
  fi
}

copy_public_config() {
  mkdir -p "$RUNTIME/config"
  cp "$ROOT/config/dictionary.json" "$RUNTIME/config/dictionary.json"
  cp "$ROOT/config/profile.json" "$RUNTIME/config/profile.json"
  cat > "$RUNTIME/config/memory_terms.json" <<'EOF'
{
  "version": "public.empty.v1",
  "terms": []
}
EOF
  cat > "$RUNTIME/config/phrase_fixes.json" <<'EOF'
{
  "version": "public.empty.v1",
  "phrase_fixes": []
}
EOF
}

copy_runtime() {
  rm -rf "$RUNTIME"
  mkdir -p "$RUNTIME/src" "$RUNTIME/bin" "$RUNTIME/lib" "$RUNTIME/models" "$RUNTIME/logs"

  rsync -a --delete \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '*.pyo' \
    --exclude '*.egg-info/' \
    --exclude 'ramblefix/cloud_asr.py' \
    --exclude 'ramblefix/eval.py' \
    --exclude 'ramblefix/gemini_asr.py' \
    --exclude 'ramblefix/sarvam_asr.py' \
    --exclude 'ramblefix/tts.py' \
    --exclude 'ramblefix/ludo_asr.py' \
    --exclude 'ramblefix/chinese_polish.py' \
    --exclude 'ramblefix/meeting_engine.py' \
    "$ROOT/src/" "$RUNTIME/src/"
  mkdir -p "$RUNTIME/script"
  cp "$ROOT/script/start_srota_server.sh" "$RUNTIME/script/start_srota_server.sh"
  chmod +x "$RUNTIME/script/start_srota_server.sh"
  copy_public_config
  cp "$ROOT/pyproject.toml" "$ROOT/requirements.txt" "$ROOT/requirements-runtime.txt" "$RUNTIME/"

  if [[ "${RAMBLEFIX_PACKAGE_EMBED_VENV:-0}" == "1" ]]; then
    "$ROOT/script/build_release_runtime_venv.sh" "$RUNTIME"
  else
    cat > "$RUNTIME/README_RUNTIME.txt" <<'EOF'
This runtime is source-only. For a one-click public build, set RAMBLEFIX_PACKAGE_EMBED_VENV=1
or replace this with a signed bootstrap that creates the runtime venv on first launch.
EOF
  fi

  local model="${RAMBLEFIX_WHISPER_MODEL:-$ROOT/models/ggml-small.bin}"
  if [[ -f "$model" ]]; then
    cp "$model" "$RUNTIME/models/ggml-small.bin"
  else
    echo "warning: missing whisper model, not embedding: $model" >&2
  fi

  local server="${RAMBLEFIX_WHISPER_SERVER_BINARY:-$(command -v whisper-server || true)}"
  if [[ -n "$server" && -f "$server" ]]; then
    cp "$server" "$RUNTIME/bin/whisper-server"
    chmod +x "$RUNTIME/bin/whisper-server"
    rewrite_whisper_binary "$RUNTIME/bin/whisper-server"
  else
    echo "warning: missing whisper-server binary; packaged app will need first-run bootstrap" >&2
  fi

  local cli="${RAMBLEFIX_WHISPER_CPP_BINARY:-$(command -v whisper-cli || true)}"
  if [[ -n "$cli" && -f "$cli" ]]; then
    cp "$cli" "$RUNTIME/bin/whisper-cli"
    chmod +x "$RUNTIME/bin/whisper-cli"
    rewrite_whisper_binary "$RUNTIME/bin/whisper-cli"
  else
    echo "warning: missing whisper-cli binary; Hinglish GGML polish will need first-run bootstrap" >&2
  fi

  local oriserve_model="${RAMBLEFIX_ORISERVE_GGML_MODEL:-$ROOT/models/oriserve-ggml/ggml-oriserve-hinglish-q8_0.bin}"
  if [[ -f "$oriserve_model" ]]; then
    mkdir -p "$RUNTIME/models/oriserve-ggml"
    cp "$oriserve_model" "$RUNTIME/models/oriserve-ggml/ggml-oriserve-hinglish-q8_0.bin"
  else
    echo "warning: missing Oriserve Hinglish GGML model, not embedding: $oriserve_model" >&2
  fi

  printf '%s\n' "RambleFixRuntime" > "$APP/Contents/Resources/ramblefix-root.txt"
}

copy_whisper_dylib() {
  local source="$1"
  local name="$2"
  if [[ ! -f "$source" ]]; then
    echo "warning: missing whisper runtime library, not embedding: $source" >&2
    return
  fi
  cp "$source" "$RUNTIME/lib/$name"
  chmod u+w "$RUNTIME/lib/$name"
  install_name_tool -id "@rpath/$name" "$RUNTIME/lib/$name" 2>/dev/null || true
}

copy_whisper_runtime_libraries() {
  copy_whisper_dylib "${RAMBLEFIX_LIBWHISPER_DYLIB:-/opt/homebrew/opt/whisper-cpp/lib/libwhisper.1.dylib}" "libwhisper.1.dylib"
  copy_whisper_dylib "${RAMBLEFIX_LIBGGML_DYLIB:-/opt/homebrew/opt/ggml/lib/libggml.0.dylib}" "libggml.0.dylib"
  copy_whisper_dylib "${RAMBLEFIX_LIBGGML_BASE_DYLIB:-/opt/homebrew/opt/ggml/lib/libggml-base.0.dylib}" "libggml-base.0.dylib"
  for dylib in "$RUNTIME/lib/"*.dylib; do
    [[ -f "$dylib" ]] || continue
    install_name_tool -change /opt/homebrew/opt/whisper-cpp/lib/libwhisper.1.dylib @rpath/libwhisper.1.dylib "$dylib" 2>/dev/null || true
    install_name_tool -change /opt/homebrew/opt/ggml/lib/libggml.0.dylib @rpath/libggml.0.dylib "$dylib" 2>/dev/null || true
    install_name_tool -change /opt/homebrew/opt/ggml/lib/libggml-base.0.dylib @rpath/libggml-base.0.dylib "$dylib" 2>/dev/null || true
  done
}

rewrite_whisper_binary() {
  local binary="$1"
  copy_whisper_runtime_libraries
  install_name_tool -change /opt/homebrew/opt/ggml/lib/libggml.0.dylib @rpath/libggml.0.dylib "$binary" 2>/dev/null || true
  install_name_tool -change /opt/homebrew/opt/ggml/lib/libggml-base.0.dylib @rpath/libggml-base.0.dylib "$binary" 2>/dev/null || true
}

create_dmg() {
  rm -f "$DMG" "$ZIP" "$SUMS"
  ditto -c -k --keepParent "$APP" "$ZIP"
  hdiutil create -volname "$APP_NAME" -srcfolder "$APP" -ov -format UDZO "$DMG" >/dev/null
}

generate_checksums() {
  (
    cd "$RELEASE_DIR"
    shasum -a 256 "$(basename "$DMG")" "$(basename "$ZIP")" > "$(basename "$SUMS")"
  )
}

notarize_if_requested() {
  if [[ "${RAMBLEFIX_NOTARIZE:-0}" != "1" ]]; then
    return
  fi
  if [[ -z "${RAMBLEFIX_NOTARY_PROFILE:-}" ]]; then
    echo "RAMBLEFIX_NOTARIZE=1 requires RAMBLEFIX_NOTARY_PROFILE for xcrun notarytool" >&2
    exit 2
  fi
  xcrun notarytool submit "$DMG" --keychain-profile "$RAMBLEFIX_NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
}

require_developer_id_for_release
"$ROOT/script/validate_v0_release_scope.sh"
rm -rf "$RELEASE_DIR"
mkdir -p "$RELEASE_DIR"
RAMBLEFIX_APP_NAME="$APP_NAME" \
RAMBLEFIX_BUNDLE_ID="$BUNDLE_ID" \
RAMBLEFIX_EXECUTABLE_NAME="$EXECUTABLE_NAME" \
RAMBLEFIX_APP_VERSION="$VERSION" \
"$ROOT/script/build_macos_app.sh" "$APP"

if [[ "${RAMBLEFIX_PACKAGE_EMBED_RUNTIME:-1}" == "1" ]]; then
  copy_runtime
  "$ROOT/script/validate_public_runtime_local_only.sh" "$RUNTIME"
fi

codesign --force --deep --options runtime --sign "${RAMBLEFIX_CODESIGN_IDENTITY:--}" "$APP" >/dev/null
"$ROOT/script/audit_macos_release_artifact.sh" "$APP"
create_dmg
notarize_if_requested
generate_checksums

echo "$DMG"
echo "$ZIP"
echo "$SUMS"
