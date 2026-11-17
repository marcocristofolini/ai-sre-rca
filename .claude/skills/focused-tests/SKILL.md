---
name: focused-tests
description: Run the pytest subset related to changed backend files, then the full suite only if green
disable-model-invocation: true
---

1. `git diff --name-only` → list changed files under `backend/app`.
2. Map each to tests in `backend/tests` (`rg -l <module>` or `-k <topic>`).
3. Run `cd backend && pytest <files> -x -q`.
4. Only if green, run `cd backend && pytest -q`.
5. Report exact failures; never claim pass without output.
