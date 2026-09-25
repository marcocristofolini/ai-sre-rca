# GitHub repository setup

The repository is designed for `marcocristofolini/ai-sre-rca`.

## One-command bootstrap

Prerequisites: Git, GitHub CLI (`gh`) and an authenticated GitHub session.

```bash
./scripts/create-repository.sh
```

By default the script creates a **private** repository. To create it as public instead:

```bash
GITHUB_VISIBILITY=public ./scripts/create-repository.sh
```

The bootstrap script:

1. creates `marcocristofolini/ai-sre-rca` when it does not exist;
2. configures `origin`;
3. pushes `main` and all SemVer tags;
4. configures repository metadata, merge behavior, topics and labels;
5. triggers the historical-release bootstrap workflow.

## Version history

- `v0.1.0` — initial Kubernetes RCA MVP;
- `v0.2.0` — Awareness Graph, change correlation, Karpenter, pgvector memory and LLM guardrails;
- `v0.2.1` — bilingual documentation and Mermaid architecture diagrams.

`main` may contain repository automation changes after the latest published tag. The next functional release is reserved for `v0.3.0`.

## Workflows

- `CI` — backend tests/compile plus frontend build;
- `CodeQL` — scheduled and PR security scanning;
- `Release` — builds source archives and publishes a GitHub Release for future SemVer tags;
- `Bootstrap historical releases` — creates releases for tags that predate the release workflow.

## Release flow

For the next release:

```bash
git checkout main
git pull
# update VERSION + package/chart metadata + CHANGELOG
git commit -am 'release: v0.3.0'
git tag -a v0.3.0 -m 'AI SRE RCA v0.3.0'
git push origin main
git push origin v0.3.0
```

The tag push triggers `.github/workflows/release.yml`.
