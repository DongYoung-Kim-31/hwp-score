#!/usr/bin/env bash
# One-time setup for the hwp-score skill (macOS). Idempotent: re-running only fills gaps.
#   - Python venv ~/.cache/hwp-score/venv with verovio, pypdfium2, numpy, pillow
#   - rsvg-convert (Homebrew librsvg)
#   - rhwp CLI in ~/.local/bin (GitHub release of edwardkim/rhwp, SHA-256 verified)
set -euo pipefail

RHWP_VERSION="${RHWP_VERSION:-v0.8.7}"
VENV="$HOME/.cache/hwp-score/venv"
BIN="$HOME/.local/bin"

say() { printf '[hwp-score] %s\n' "$*"; }

# 1. Python venv
if [ ! -x "$VENV/bin/python" ]; then
  say "creating venv $VENV"
  python3 -m venv "$VENV"
fi
if ! "$VENV/bin/python" -c "import verovio, pypdfium2, numpy, PIL, scipy" 2>/dev/null; then
  say "installing Python packages (verovio pypdfium2 numpy pillow scipy)"
  "$VENV/bin/pip" install -q --disable-pip-version-check verovio pypdfium2 numpy pillow scipy
fi
say "python ok: $("$VENV/bin/python" -c 'import verovio; print("verovio", verovio.toolkit().getVersion())')"

# 2. librsvg
if ! command -v rsvg-convert >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    say "installing librsvg with Homebrew"
    brew install librsvg
  else
    say "ERROR: rsvg-convert missing and Homebrew not found — install librsvg manually"; exit 1
  fi
fi
say "rsvg-convert ok: $(command -v rsvg-convert)"

# 3. rhwp CLI
if command -v rhwp >/dev/null 2>&1; then
  RHWP="$(command -v rhwp)"
elif [ -x "$BIN/rhwp" ]; then
  RHWP="$BIN/rhwp"
else
  case "$(uname -m)" in
    arm64) ARCH=aarch64 ;;
    x86_64) ARCH=x86_64 ;;
    *) say "ERROR: unsupported CPU $(uname -m)"; exit 1 ;;
  esac
  F="rhwp-$RHWP_VERSION-macos-$ARCH.tar.gz"
  URL="https://github.com/edwardkim/rhwp/releases/download/$RHWP_VERSION"
  TMP="$(mktemp -d)"
  say "downloading $F"
  curl -sfL -o "$TMP/$F" "$URL/$F"
  curl -sfL -o "$TMP/SHA256SUMS.txt" "$URL/SHA256SUMS.txt"
  (cd "$TMP" && grep " $F\$" SHA256SUMS.txt | shasum -a 256 -c -)
  tar -xzf "$TMP/$F" -C "$TMP"
  mkdir -p "$BIN"
  cp "$TMP/rhwp/rhwp" "$BIN/rhwp"
  chmod +x "$BIN/rhwp"
  rm -rf "$TMP"
  RHWP="$BIN/rhwp"
fi
say "rhwp ok: $("$RHWP" --version 2>&1 | head -1) ($RHWP)"
say "setup complete"
