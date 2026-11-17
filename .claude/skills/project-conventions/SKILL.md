---
name: project-conventions
description: Project rules for ai-sre-rca — evidence integrity, authorization, scope, efficiency. Apply when editing backend/app (agentic, awareness, remediation, api, core).
user-invocable: false
---

- Never trade correctness, security, authorization, or evidence integrity for speed/tokens (see AGENTS.md).
- RCA verdicts must rest on verified evidence; never fabricate or infer unverified causes. Preserve causal conflicts (multi-causal verdicts).
- Remediation: keep policy, approval and drift checks intact; never bypass them.
- Respect scope/coverage checks in awareness and agentic code (`resource_in_coverage`, `scope_error`).
- Backend: Python 3.12, FastAPI, async SQLAlchemy, Pydantic v2. Tests: pytest, `asyncio_mode=auto`.
- Run focused tests before the full suite; rebuild only when inputs change.
