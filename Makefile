.PHONY: test test-docker test-docker-build test-docker-run validate-harness validate-real demo compose-up compose-build compose-down

test:
	cd backend && python -m pytest -q

test-docker-build:
	docker build -f backend/Dockerfile.test -t ai-sre-rca-test backend

test-docker: test-docker-build
	$(MAKE) test-docker-run

test-docker-run:
	docker run --rm ai-sre-rca-test

validate-harness:
	cd backend && python -m pytest -q ../validation/test_run.py

validate-real:
	cd backend && python ../validation/run.py --report-dir ../validation-artifacts

demo:
	cd backend && DEMO_MODE=true uvicorn app.main:app --reload --port 8080

compose-up:
	docker compose up --no-build

compose-build:
	docker compose build

compose-down:
	docker compose down
