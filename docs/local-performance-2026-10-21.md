# Local development performance, 2026-10-21

Measured in the isolated `perf/docker-token-efficiency` worktree at base `e0a7e07` on WSL2 ext4. Docker 29.9.2, BuildKit, overlayfs, 6 logical CPUs, 19 GiB reported Linux RAM (Docker limit 20,973,735,936 bytes), 8 GiB swap. No Windows, WSL, Docker Desktop, power, or thermal settings were changed. Build operations were run serially where possible; no thermal telemetry was available. Raw logs and timings are in `/tmp/ai-sre-perf-*` and are not committed.

| Metric | Before | After | Change |
|---|---:|---:|---:|
| Backend test image, `--no-cache` | 39.62 s | 29.04 s | 26.7% faster |
| Backend test image, unchanged inputs | 1.23 s | 1.89 s | 0.66 s slower |
| Frontend image, `--no-cache` | 41.62 s | 32.36 s | 22.2% faster |
| Frontend image, unchanged inputs | 2.56 s | 2.05 s | 19.9% faster |
| Backend PostgreSQL suite, Docker | 11.76 s, 296 passed | 12.78 s, 296 passed | 1.02 s slower in one run |
| Playwright runner | not run | not run | unavailable on host; no browser source changed |
| Kind creation/readiness | not run | not run | harness unchanged; disposable real environment is expensive |
| Prometheus/Loki/Tempo readiness | not run | not run | harness unchanged |
| Integrated real validation | not run | not run | harness unchanged; unit suite passed 50 tests after |
| Benchmark latency/query resources | not run | not run | benchmark classification and datasets unchanged |
| PostgreSQL readiness | not measured | about 2.15 s from container start to log readiness | one disposable container |
| Peak CPU and total RAM | not captured | not captured | no defensible comparison |
| Swap I/O | not captured | `vmstat` sample: 0 KiB/s in and out | no defensible comparison |
| Codex tokens | unavailable | unavailable | no token telemetry supplied |
| Application LLM tokens | unavailable | unavailable | LLM disabled in local runs |

The `--no-cache` runs retain BuildKit download caches and were sequential, so they are **uncached image builds**, not reproducible first-download cold starts. Their timing differences include cache state and normal system variability; they are local observations, not production guarantees. The original frontend warm image was rebuilt from its original Dockerfile and primed before the timed run. The interim 29.36 s build changed Dockerfile inputs and is excluded. Process RSS from `/usr/bin/time` measures the Docker CLI, not container or daemon peak memory. At the initial snapshot, Linux reported 3.2 GiB used RAM and 180 KiB swap used; after validation, 2.5 GiB and 1.3 MiB. Neither is a peak measurement. Docker build cache grew from 19.25 GB to 22.89 GB while retaining all existing cache. PostgreSQL used about 66 MiB in one `docker stats` snapshot.

Changes: dependency installation now precedes backend source and test copies; pip and npm use BuildKit download caches; Docker contexts exclude generated content. Compose startup uses existing images, with an explicit `compose-build` target and backend readiness before frontend startup. `test-docker-run` reuses a built test image. Next-step Copilot questions now return the same deterministic answer without a model call; a test verifies one model request for a separate question and zero additional requests for next steps. Actual token savings need provider usage telemetry and were not measured.

Validation: backend PostgreSQL suite passed 296 tests before and after; validation harness unit suite passed 50 tests after; production backend and frontend images built; standalone frontend served HTTP 200 and included static/public assets; Compose config and `git diff --check` passed. No Playwright, Kind, full real-telemetry, or benchmark execution was claimed. The existing RCA Benchmark v2.1 causal-coverage failure was not changed. No xdist or extra BuildKit parallelism was added: fixtures share PostgreSQL and no isolated-worker or sustained-throughput measurement justified it. The real harness deliberately creates and destroys its Kind cluster; reuse was rejected because persistent telemetry and incident identities could invalidate benchmark freshness and isolation.
