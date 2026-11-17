#!/usr/bin/env bash
# Format+lint edited Python files; skip silently if ruff missing.
f=$(jq -r '.tool_input.file_path // empty')
[[ "$f" == *.py ]] && command -v ruff >/dev/null || exit 0
ruff check --fix --quiet "$f"; ruff format --quiet "$f"
exit 0
