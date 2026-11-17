# Competitive roadmap

Gaps between Tracecroft AI and hosted AI SRE products, ordered by impact. Each item is one GitHub issue that carries the problem, the safety boundary, a recommended model and, where relevant, its blockers.

Tracecroft's differentiator is auditable, deterministic verdicts. No item here may weaken evidence integrity, authorization or the read-only investigator boundary.

| Order | Item | Issue | Model | Status |
| --- | --- | --- | --- | --- |
| 1 | Slack inbound: start and answer investigations from a mention | #71 | Sonnet 5.5 | Deferred |
| 2 | Alert grouping and noise report | #77 | Sonnet 5.5 | Done |
| 3 | Source-neutral telemetry interface (prerequisite for 4 and 5) | #73 | Opus 5.5 (ADR), Sonnet 5.5 (code) | Done |
| 4 | Datadog read-only evidence adapter | #74 | Sonnet 5.5 | Done |
| 5 | CloudWatch read-only evidence adapter | #75 | Sonnet 5.5 | Done |
| 6 | Degraded mode for incomplete telemetry identity | #81 | Opus 5.5 | Open |
| 7 | Preflight `doctor` command for onboarding | #82 | Sonnet 5.5 | Open |
| 8 | One-command reproducible public benchmark | #85 | Haiku 5.5 | Open |
| 9 | Kafka live failure and recovery validation | #83 | Sonnet 5.5 | Open |
| 10 | Deterministic fallback text follows the UI language | #84 | Haiku 5.5 | Closed |
| 11 | Generic signed inbound webhook and result write-back | #72 | Sonnet 5.5 | Open |
| 12 | Scheduled proactive checks | #78 | Sonnet 5.5 | Open |
| 13 | Runbook memory and pluggable embeddings | #80 | Sonnet 5.5 | Open |
| 14 | More allow-listed remediation actions | #79 | Opus 5.5 | Open |
| 15 | Investigation scope beyond Kubernetes (ADR first) | #76 | Opus 5.5 | Open |

## Model choice

Model recommendations are a judgment by task risk and ambiguity, not a measured benchmark.

- **Haiku 5.5:** mechanical changes with an obvious shape (strings, Makefile wiring, short docs).
- **Sonnet 5.5:** bounded feature work that follows an existing pattern in the codebase.
- **Opus 5.5:** design decisions, and anything touching evidence integrity, identity or mutating credentials.

## Keeping token use low

- One issue per session; start with `/clear` so earlier context is not carried along.
- Give the agent a short prompt that names the few files to read first; everything else is located with `rg`.
- For ADR-first items, do the design with the stronger model, review it, then implement in a new session with the cheaper one.
- Verify with the `focused-tests` skill before any broader suite.
- Use the `security-reviewer` and `evidence-integrity-reviewer` agents on the diff instead of re-reading the code in the main session.
