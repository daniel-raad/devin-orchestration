.PHONY: install test run docker-up docker-down docker-test fmt

install:
	pip install -r requirements.txt

test:
	PYTHONPATH=. pytest -q

run:
	PYTHONPATH=. uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

docker-up:
	docker compose up --build

docker-down:
	docker compose down

docker-test:
	docker compose run --rm app pytest -q
