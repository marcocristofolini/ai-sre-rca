#!/usr/bin/env bash
set -euo pipefail

REPO="${1:-marcocristofolini/ai-sre-rca}"

command -v gh >/dev/null || { echo "GitHub CLI (gh) is required" >&2; exit 1; }
gh auth status

echo "Configuring $REPO"
gh repo edit "$REPO" \
  --description "Evidence-first, read-only AI SRE root-cause analysis platform for Kubernetes and cloud-native systems." \
  --enable-issues \
  --enable-wiki=false \
  --enable-projects=false \
  --delete-branch-on-merge \
  --enable-squash-merge \
  --enable-merge-commit=false \
  --enable-rebase-merge=false

gh repo edit "$REPO" --add-topic sre --add-topic kubernetes --add-topic observability --add-topic root-cause-analysis --add-topic devops --add-topic opentelemetry --add-topic prometheus --add-topic incident-response --add-topic aiops

for label in "area/backend" "area/frontend" "area/kubernetes" "area/observability" "area/llm" "playbook" "security"; do
  gh label create "$label" --repo "$REPO" --force >/dev/null
done

echo "Repository metadata and labels configured."
echo "Run: gh workflow run 'Bootstrap historical releases' --repo $REPO"
