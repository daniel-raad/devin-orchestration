.PHONY: install test run docker-up docker-down docker-test fmt clean-venv

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
UVICORN := $(VENV)/bin/uvicorn

$(VENV)/.installed: requirements.txt
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	touch $@

install: $(VENV)/.installed

test: $(VENV)/.installed
	PYTHONPATH=. $(PYTEST) -q

run: $(VENV)/.installed
	PYTHONPATH=. $(UVICORN) app.main:app --host 0.0.0.0 --port 8000 --reload

clean-venv:
	rm -rf $(VENV)

docker-up:
	docker compose up --build

docker-down:
	docker compose down

docker-test:
	docker compose run --rm app pytest -q
