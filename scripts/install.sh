#!/usr/bin/env bash
# Installs the tanka plugin from this checkout for the current user.
# Usable by hand or as a cloud environment's setup script:
#   if [ -d tanka ]; then bash tanka/scripts/install.sh; fi
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v claude >/dev/null || { echo "tanka: claude CLI not found" >&2; exit 0; }
claude plugin marketplace add "$ROOT" >/dev/null 2>&1 || claude plugin marketplace update tanka >/dev/null 2>&1 || true
claude plugin install tanka@tanka >/dev/null 2>&1 || true
claude plugin list 2>/dev/null | grep -q 'tanka@tanka' && echo "tanka: plugin installed from $ROOT" || { echo "tanka: install failed" >&2; exit 0; }
