#!/usr/bin/env bash
set -euo pipefail

IDENTITY_NAME="${RAMBLEFIX_LOCAL_CODESIGN_NAME:-RambleFix Local Dev}"
KEYCHAIN="${RAMBLEFIX_KEYCHAIN:-$HOME/Library/Keychains/login.keychain-db}"

if security find-identity -v -p codesigning 2>/dev/null | grep -F "\"$IDENTITY_NAME\"" >/dev/null; then
  echo "Code-signing identity already exists: $IDENTITY_NAME"
  exit 0
fi

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT
P12PASS="$(uuidgen)"

cat > "$TMPDIR/openssl.cnf" <<EOF
[ req ]
prompt = no
distinguished_name = dn
x509_extensions = ext

[ dn ]
CN = $IDENTITY_NAME

[ ext ]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = codeSigning
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
EOF

openssl req -new -newkey rsa:2048 -nodes -x509 -days 3650 \
  -keyout "$TMPDIR/identity.key" \
  -out "$TMPDIR/identity.crt" \
  -config "$TMPDIR/openssl.cnf" >/dev/null 2>&1

openssl pkcs12 -export \
  -inkey "$TMPDIR/identity.key" \
  -in "$TMPDIR/identity.crt" \
  -out "$TMPDIR/identity.p12" \
  -name "$IDENTITY_NAME" \
  -passout "pass:$P12PASS" >/dev/null 2>&1

security import "$TMPDIR/identity.p12" \
  -k "$KEYCHAIN" \
  -P "$P12PASS" \
  -T /usr/bin/codesign >/dev/null

security add-trusted-cert -d -r trustRoot -p codeSign \
  -k "$KEYCHAIN" \
  "$TMPDIR/identity.crt" >/dev/null 2>&1 || true

echo "Created code-signing identity: $IDENTITY_NAME"
security find-identity -v -p codesigning | grep -F "\"$IDENTITY_NAME\""
