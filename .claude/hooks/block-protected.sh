#!/usr/bin/env bash
# Block edits to real env files, lock files and VERSION (release-owned).
f=$(jq -r '.tool_input.file_path // empty')
case "$(basename "$f")" in
  .env.example) exit 0 ;;
  .env|.env.*|VERSION|package-lock.json|pnpm-lock.yaml|yarn.lock|poetry.lock|uv.lock)
    echo "Blocked: $f is protected (secrets/lock/release-owned)." >&2; exit 2 ;;
esac
exit 0
