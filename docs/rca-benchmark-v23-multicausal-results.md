# Multi-causal RCA — local validation, October 22, 2026

This follow-up extends the existing deterministic RCA contract; it does not change the frozen benchmark oracle, source captures, tenant authorization, remediation approval, or GitHub Actions settings.

## Verified behavior

- `root_cause` remains the primary backward-compatible result. `root_causes` is an optional ordered collection, with every cause referencing its own confirmed playbook hypothesis and integrity-verified evidence IDs.
- Classic and Agentic/Copilot preserve Kubernetes precedence when selecting a primary cause. A second cause is reportable only when **independently** supported by direct Node conditions or repeated ancestry-verified trace origins.
- The Agentic evaluator makes all selectable secondary decisions; specialists and LLM output cannot persist a separate verdict.
- Copilot's deterministic answers cite verified authorized evidence for all reported causes. The Next.js UI displays the secondary causes and links to the supporting evidence. Results with missing evidence, duplicate IDs, or unsupported causes fail validation.

## Executed validations

| Check | Observed local result |
| --- | --- |
| Docker PostgreSQL backend tests | **306 passed**, one existing Starlette warning |
| Blinded synthetic benchmark (8 cases × 3 paths) | **PASS**; per path: 2 complete, 5 correct abstentions, 1 correct rejection; 0 partial, false confirmations, incorrect diagnoses, or errors |
| Live benchmark in disposable Kind (3 cases × 3 paths) | **PASS**; `l002` complete with `container-oom` and `trace-causal-propagation` on Classic, Agentic and Copilot; `l001`/`l003` abstained correctly |
| Live evidence integrity | **9/9** verified; no captured source errors or false confirmations |
| Controlled Copilot regression checks within Kind benchmark stage | **PASS**, six historical scenarios |
| Next.js production Docker build | **PASS** |
| Running browser screenshots (demo API, 4 routes) | **4 HTTP 200**, zero browser page errors |
| Running browser screenshot of `l002` | **HTTP 200**; two causes rendered, second with linked evidence; frozen real Kind result served locally in read-only mode |

The synthetic `b002` correctly returns `container-oom` + `node-not-ready`; the *live* `l002` correctly returns `container-oom` + `trace-causal-propagation`. A correctly incomplete or unauthorized case still fails closed. Model planning/LLM were disabled, so model tokens and costs were zero.

The Kind command was `KIND_BIN=/tmp/kind sh validation/run-docker.sh --benchmark-v2 full --cluster-name ai-sre-multiroot-live`. That **benchmark-v2 full stage** also runs the controlled Copilot regressions, but **does not run every independent legacy full-stack, PostgreSQL/Redis, awareness or browser harness path**. Those other report sections show `None` and `FAIL` because they were *not executed*, and must not be described as passed. The disposable cluster and its PostgreSQL container were deleted. Uncommitted raw captures remain excluded from Git.

Screenshot provenance: `docs/images/app-*-running.png` contains actual Chromium captures of the Docker-built Next.js UI with a running FastAPI demo backend (synthetic adapters); `app-multicausal-kind-running.png` uses a frozen, validated `l002` diagnosis from that day's disposable Kind run displayed through the same production frontend. These are not production telemetry screenshots.

## Remaining limits

The controlled cases are not an externally adjudicated production benchmark. Larger independent fault collections, concurrency, Kafka live failure/recovery scenarios, environment-specific readiness, and automated release gates remain separate work. Retain deterministic checks and explicit human approval before any remediation. The results do not establish production accuracy or release certification.

Historical failed runs remain available in the [v2.1 report](rca-benchmark-v21-results.md) and [v2.2 incremental report](rca-benchmark-v22-results.md).
