.PHONY: test validate-harness validate-real demo compose-up compose-down

test:
	cd backend && python -m pytest -q

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
