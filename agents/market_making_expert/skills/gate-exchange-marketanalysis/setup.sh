#!/bin/sh
set -e

REPO="gate/gate-cli"
BINARY="gate-cli"

# --- Parse flags ---
VERSION=""
while [ $# -gt 0 ]; do
  case "$1" in
    --version)
      if [ -z "$2" ]; then
        echo "Error: --version requires a value (e.g. --version v0.3.2)" >&2
        exit 1
      fi
      VERSION="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

# --- Detect OS and arch ---
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case "$ARCH" in
  x86_64)  ARCH="amd64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  *)
    echo "Unsupported architecture: $ARCH" >&2
    exit 1
    ;;
esac

case "$OS" in
  linux|darwin) ;;
  *)
    echo "Unsupported OS: $OS" >&2
    exit 1
    ;;
esac

# --- Resolve version ---
if [ -z "$VERSION" ]; then
  VERSION=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
    | grep '"tag_name"' | sed 's/.*"tag_name": *"\([^"]*\)".*/\1/')
fi

# Fallback when API is rate-limited, blocked, or grep/sed yields nothing (matches gate-cli v0.6.0 per releases/latest).
if [ -z "$VERSION" ]; then
  VERSION="v0.6.0"
  echo "setup.sh: GitHub API returned no tag_name; using fallback ${VERSION}" >&2
fi

# Strip leading 'v' for the archive filename
BARE_VERSION="${VERSION#v}"
ARCHIVE="${BINARY}_${BARE_VERSION}_${OS}_${ARCH}.tar.gz"
BASE_URL="https://github.com/${REPO}/releases/download/${VERSION}"

# --- Download ---
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "Downloading ${ARCHIVE}..."
curl -fsSL "${BASE_URL}/${ARCHIVE}" -o "${TMP}/${ARCHIVE}"
curl -fsSL "${BASE_URL}/checksums.txt" -o "${TMP}/checksums.txt"

# --- Verify checksum ---
echo "Verifying checksum..."
CHECKSUM_LINE=$(grep -F "  ${ARCHIVE}" "${TMP}/checksums.txt" || true)
if [ -z "$CHECKSUM_LINE" ]; then
  echo "Error: ${ARCHIVE} not found in checksums.txt" >&2
  exit 1
fi
if command -v shasum > /dev/null 2>&1; then
  (cd "$TMP" && echo "$CHECKSUM_LINE" | shasum -a 256 --check --status)
elif command -v sha256sum > /dev/null 2>&1; then
  (cd "$TMP" && echo "$CHECKSUM_LINE" | sha256sum --check --status)
else
  echo "Warning: no sha256sum or shasum found, skipping checksum verification" >&2
fi

# --- Extract ---
tar -xzf "${TMP}/${ARCHIVE}" -C "$TMP" "${BINARY}"

# --- Install ---
install_bin() {
  local dir="$1"
  local use_sudo="$2"
  if [ "$use_sudo" = "true" ]; then
    sudo install -m 755 "${TMP}/${BINARY}" "${dir}/${BINARY}"
  else
    install -m 755 "${TMP}/${BINARY}" "${dir}/${BINARY}"
  fi
}

LOCAL_BIN="$HOME/.openclaw/skills/bin"
mkdir -p "$LOCAL_BIN" 2>/dev/null || true

if install_bin "$LOCAL_BIN" "false" 2>/dev/null; then
  INSTALL_DIR="$LOCAL_BIN"
  # Check if it's on PATH
  case ":$PATH:" in
    *":${LOCAL_BIN}:"*) ;;
    *)
      echo ""
      echo "Installed to ${LOCAL_BIN}/${BINARY}"
      echo "Add the following to your shell profile to use it:"
      echo "  export PATH=\"\$HOME/.openclaw/skills/bin:\$PATH\""
      ;;
  esac
else
  SYSTEM_BIN="/usr/local/bin"
  install_bin "$SYSTEM_BIN" "true"
  INSTALL_DIR="$SYSTEM_BIN"
fi

echo ""
echo "gate-cli ${VERSION} installed to ${INSTALL_DIR}/${BINARY}"
echo "Run: gate-cli --version"