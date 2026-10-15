.PHONY: test test-docker test-docker-build validate-harness validate-real demo compose-up compose-down

test:
	cd backend && python -m pytest -q

test-docker-build:
	docker build -f backend/Dockerfile.test -t ai-sre-rca-test backend

test-docker: test-docker-build
	docker run --rm ai-sre-rca-test

validate-harness:
	cd backend && python -m pytest -q ../validation/test_run.py

validate-real:
	cd backend && python ../validation/run.py --report-dir ../validation-artifacts

demo:
	cd backend && DEMO_MODE=true uvicorn app.main:app --reload --port 8080

compose-up:
	docker compose up --build

compose-down:
	docker compose down
