# RCA benchmarks

Benchmarks reuse the classic engine, agentic planner/executor/evaluator and Copilot APIs. They do not modify causal gates. Results describe these controlled datasets, not production accuracy.

## v2: independent adversarial cases

The v2 runner is [validation/benchmark_v2.py](../validation/benchmark_v2.py). The eight synthetic cases cover simultaneous causes, misleading deployments, conflicting telemetry, missing evidence, recovery, ambiguous trace origins, stale authoritative inventory and malicious log instructions. They are new cases, separate from the six original live regression scenarios.

Case identifiers are opaque. Incident titles/questions contain no expected outcome or scenario category. Oracles live in separate JSON manifests and load only after predictions complete. Inputs, raw read caches and seeded investigations contain no oracle fields. Every warm-up/scored path gets fresh state; live PostgreSQL paths additionally get unique authorized tenants. A neutral saved source seeds Copilot proposals. Prior diagnoses, expected answers and benchmark reports are never seeded into runtime context.

Raw allow-listed source reads are captured once per request, then frozen for comparison. Each path receives equivalent target UID, namespace, service identity, temporal bounds and budgets. Unknown replay reads, hash/reference failures and swallowed replay failures are errors. Captured source errors are replayed as errors, never fabricated successful source responses. Replay preserves measured query-budget charges.

The three paths are:

- **Classic:** existing deterministic investigation engine.
- **Agentic:** existing planner, bounded specialists, shared-envelope evaluator and diagnosis materializer.
- **Copilot:** explicit proposal/confirmation, idempotent job, worker, SSE completion, authorized saved result and evidence-grounded Q&A. It reuses agentic RCA; it is **not a third independent causal engine**. The benchmark disables LLM/model planning and measures deterministic fallback, not model quality.

### Scoring

- **Complete diagnosis:** emitted cause is accepted, evidence/references verify and all declared causes are reported.
- **Partial diagnosis:** one accepted cause is reported but another declared cause is missing. It fails complete-case scoring and is not a false confirmation.
- **Incorrect diagnosis / false confirmation:** an unsupported cause, a cause on an abstention/rejection case, or a confirmation with failed evidence verification. Execution errors cannot become successful abstentions.
- **Correct abstention:** successful investigation without confirmation when the oracle requires uncertainty.
- **Correct rejection:** expected scope gate rejects input; counted separately from causal abstention.
- **Integrity:** unique evidence IDs, valid SHA-256 hashes, hypothesis references in the same result and a verified confirmed hypothesis behind any resolved root. Copilot citations must belong to its authorized saved investigation.

The current API selects one root; multi-cause cases deliberately expose that limitation. Reports include `primary_correct`, `partial_diagnosis`, `unreported_causes`, errors and per-case failures. Overall `passed=false` and CLI exit 1 are expected while these weaknesses persist; tests assert the failure rather than relaxing the oracle.

Latency uses **nearest-rank p50/p95** over completed, non-rejected investigations. It measures frozen-source execution; live comparisons include PostgreSQL/inventory preparation, and Copilot includes job/SSE/Q&A. Network capture, failed/rejected trials and fault setup are excluded. Collection wall time is recorded separately. Tiny samples do not establish production latency.

Telemetry query counts use recorded budget charges, including repeated replayed calls; raw capture method counts are separate and do not necessarily equal HTTP requests. LLM tokens/cost are exactly zero because no model requests are made. Telemetry/infrastructure money costs and CPU/network energy are unavailable (`null`/`n/a`).

### Docker execution

Use the cached test image when available. From the repository root:

```sh
make test-docker-build
# Set PYTHONPATH so both application and validation modules resolve.
docker run --rm -v "$PWD:/repo" -w /repo -e PYTHONPATH=/repo:/repo/backend \
  ai-sre-rca-test python -m pytest -q validation/test_benchmark_v2.py backend/tests/test_benchmark.py

docker run --rm -v "$PWD:/repo" -w /repo -e PYTHONPATH=/repo:/repo/backend \
  ai-sre-rca-test python -m validation.benchmark_v2 --report-dir validation-artifacts/benchmark-v2/offline
```

The second command currently exits 1 for partial multi-cause diagnoses. Both JSON and Markdown reports are still written. To replay synthetic captures without source access, use `--replay-report validation-artifacts/benchmark-v2/offline/benchmark-v2.json`. Snapshot checksums are verified before replay. Integer nanosecond timestamps must remain exact; do not round-trip raw fixtures through JavaScript numbers.

The retained [pilot source fixture](../validation/fixtures/benchmark-v2/legacy-observations.json) contains only real disposable-source observations, not prior diagnoses/oracles/credentials. Run `python -m validation.benchmark_v2 --legacy-capture` inside the same Docker command to reproduce a discovered legacy unscoped Pod/service lookup of absent Argo CD. At `1fb1e2f`, it produces two execution errors. The [v2.1 repair](rca-benchmark-v21-results.md) removes the unjustified application-name guess and yields three evidence-verified abstentions on the unchanged fixture. Tests still reproduce failed execution for the legacy guess and preserve failures for explicitly mapped unavailable applications. Historical source errors remain in the capture; replay does not establish current source health.

### Staged live run

With Docker, Kind and kubectl available:

```sh
sh validation/run-docker.sh --benchmark-v2 smoke --cluster-name ai-sre-benchmark-v2-smoke
sh validation/run-docker.sh --benchmark-v2 full --cluster-name ai-sre-benchmark-v2-full
```

Run only the needed stage. Each invocation creates one disposable stack, reusing existing manifests, workload image, Prometheus/Loki/Tempo and PostgreSQL. Existing names are refused; cleanup owns only resources created by that invocation. Never point this harness at a production cluster.

`smoke` runs a healthy rollout and simultaneous real downstream timeout/OOMKilled. `full` first runs those, then recovery and all six original Copilot real-world regressions without weakening their assertions. Live independent cases use **actual server-resolved Deployment scope**, UID inventory proofs, explicit telemetry mapping and trustworthy Loki Pod UID labels. Raw inventory is cloned into each fresh authorized benchmark tenant, not reconstructed from expected answers. Trace indexing barriers verify generated trace IDs before collection.

Full live raw captures, inventory, controlled HTTP responses, source failures and regression details remain under `validation-artifacts/`; they are excluded from Git. Live inventory authorization expires: rerun the disposable harness rather than treating an old capture as fresh inventory. [Recorded local results](rca-benchmark-v2-results.md) include the initial failed pilot and its retained replay. The six nonblind historical regression cases are reported separately from independent scoring.

### Independence limits

Inputs/oracles are separated and runtime-blinded, but these cases are authored in the same development workflow, without external adjudication. Source collection is not an atomic distributed snapshot. Equivalent frozen reads control source drift but cannot measure uncached network performance. Repeating a case adds no independent fault coverage. See [real validation prerequisites](../validation/README.md).

## v1: preserved smoke baseline

`python -m app.benchmark --report-dir /tmp/rca-benchmark` (inside the backend Docker environment) retains suite `demo-v1`: storage eviction and unsupported alert, classic and agentic only. No network, LLM, database or remediation is involved; declared defaults ignore environment overrides.

v1 accuracy is correct diagnoses / known-cause scenarios; answered accuracy is correct diagnoses / emitted diagnoses. Abstention and correct-abstention rates remain separate. Failed specialists, invalid evidence and resolved results without roots are errors. Undefined rates are `null`/`n/a`. Fixtures/oracles remain unchanged; [v1 tests](../backend/tests/test_benchmark.py) continue to run.
