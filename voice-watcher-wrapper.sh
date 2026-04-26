#!/bin/bash
# Wrapper for LaunchAgent — bash gets TCC access to ~/Desktop on macOS
# while a bare python3 invocation does not.
#
# Loads .env from the same directory as this script,
# then runs process-voice.py with explicit Homebrew PATH.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a
  source "$SCRIPT_DIR/.env"
  set +a
fi

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
cd "$SCRIPT_DIR"
exec python3 process-voice.py
