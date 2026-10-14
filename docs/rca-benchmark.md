# RCA Benchmark Engine v1

Run from `backend` with the project Python environment:

```sh
python -m app.benchmark --report-dir /tmp/rca-benchmark
python -m pytest -q tests/test_benchmark.py
```

The runner exercises the existing deterministic engine and agentic planner,
executor and evidence evaluator using local `DemoTools` evidence and a fresh
in-memory repository per investigation. No network, LLM, database or remediation
is involved. Declared settings defaults are used independently of environment
variables and `.env`; production settings and CI gates are unchanged.

Suite `demo-v1` contains a storage eviction with expected hypothesis
`disk-pressure` and an unsupported alert without a target requiring abstention.
Inputs have fixed incident times. Reports retain only stable hypothesis IDs and
outcomes, excluding engine-generated IDs and observation times. Fixture or
ground-truth changes require a new suite version. This small smoke benchmark
does not measure broad production accuracy or model planning quality.

`benchmark.json` includes individual outcomes and metrics per engine;
`benchmark.md` presents the same results as tables. Exit status is 1 on a wrong
diagnosis, unexpected abstention or execution error, otherwise 0.
Failed specialists, invalid evidence hashes and resolved results without a root
cause are errors, never successful abstentions.

- Accuracy: correct diagnoses / scenarios with a known cause.
- Answered accuracy: correct diagnoses / all emitted diagnoses.
- Abstention rate: successful investigations without a resolved diagnosis / all scenarios.
- Correct abstention rate: correct abstentions / scenarios requiring abstention.
- Errors and false diagnoses are reported as counts. Errors never count as correct abstentions.

Undefined rates use JSON `null` and Markdown `n/a`. An inconclusive result counts
as abstention even when the deterministic engine supplies a placeholder root cause.
