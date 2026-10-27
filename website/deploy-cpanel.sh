#!/usr/bin/env bash
set -euo pipefail

# Deploy only to an existing, independently verified cPanel subdomain document root.
: "${TRACE_DOCROOT:?Set TRACE_DOCROOT to the cPanel-confirmed subdomain root}"
: "${TRACE_SSH_TARGET:?Set TRACE_SSH_TARGET to your authorized SSH user and host}"

SITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$TRACE_DOCROOT" != /home/marcofc/* ]] ||
   [[ "$TRACE_DOCROOT" == /home/marcofc/public_html ]] ||
   [[ "$TRACE_DOCROOT" == /home/marcofc/ ]]; then
  echo "Refusing an unverified or primary-domain document root" >&2
  exit 2
fi

test -s "$SITE_DIR/index.html"
test -s "$SITE_DIR/architecture/ai-sre-rca.html"
test -s "$SITE_DIR/assets/tracecroft-mark.svg"

SSH_PORT="${TRACE_SSH_PORT:-2006}"
SSH_KEY="${TRACE_SSH_KEY:-$HOME/.ssh/id_ed25519}"

ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -p "$SSH_PORT" -i "$SSH_KEY" "$TRACE_SSH_TARGET" "test -d '$TRACE_DOCROOT' && test -w '$TRACE_DOCROOT'"
rsync -rlptz --checksum --itemize-changes --exclude='README.md' --exclude='deploy-cpanel.sh' -e "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -p $SSH_PORT -i $SSH_KEY" "$SITE_DIR/" "$TRACE_SSH_TARGET:$TRACE_DOCROOT/"
echo "Files copied. Confirm public HTTPS and architecture iframe before declaring the site live."
