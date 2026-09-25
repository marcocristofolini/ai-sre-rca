# Contributing

Thank you for considering a contribution to AI SRE RCA.

## Development principles

1. Preserve the read-only investigator boundary.
2. Every deterministic conclusion must be traceable to evidence IDs.
3. LLM output may propose checks, but cannot independently confirm a root cause.
4. Prefer typed integrations over generic shell execution.
5. Add or update regression tests for behavior changes.

## Local development

```bash
cp .env.example .env
docker compose up --build
```

Backend tests:

```bash
cd backend
python -m pip install -e '.[dev]'
pytest -q
```

Frontend build:

```bash
cd frontend
npm install
npm run build
```

## Pull requests

- Use a focused branch and a clear conventional commit message.
- Explain the incident/RCA behavior being changed.
- Include tests for new playbooks or scoring rules.
- Do not add mutating infrastructure permissions without an explicit architecture decision.
