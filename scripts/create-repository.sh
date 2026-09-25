#!/usr/bin/env bash
set -euo pipefail

OWNER="${GITHUB_OWNER:-marcocristofolini}"
NAME="${GITHUB_REPO:-ai-sre-rca}"
VISIBILITY="${GITHUB_VISIBILITY:-private}"
REPO="$OWNER/$NAME"

command -v gh >/dev/null || { echo "GitHub CLI (gh) is required" >&2; exit 1; }
gh auth status

if gh repo view "$REPO" >/dev/null 2>&1; then
  echo "$REPO already exists; using it."
else
  gh repo create "$REPO" --"$VISIBILITY" --description "Evidence-first, read-only AI SRE root-cause analysis platform for Kubernetes and cloud-native systems."
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  git remote add origin "https://github.com/$REPO.git"
fi

git push -u origin main
git push origin --tags

"$(dirname "$0")/configure-github.sh" "$REPO"

gh workflow run "Bootstrap historical releases" --repo "$REPO"

echo "Repository ready: https://github.com/$REPO"
