.PHONY: test demo compose-up compose-down

test:
	cd backend && python -m pytest -q

demo:
	cd backend && DEMO_MODE=true uvicorn app.main:app --reload --port 8080

compose-up:
	docker compose up --build

compose-down:
	docker compose down
