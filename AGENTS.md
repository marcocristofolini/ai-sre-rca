# Repository efficiency policy

- Scope inspection with `rg`, targeted reads, and diffs; reuse verified context.
- Keep tool output and saved logs concise, and make only necessary calls.
- Prefer deterministic local checks over unnecessary model calls.
- Run focused tests before broader suites; rebuild only when inputs change.
- Never trade correctness, security, authorization, or evidence integrity for speed or tokens.
