.PHONY: install test run seed lint

install:
	python -m venv .venv && .venv/bin/pip install -r requirements.txt

test:
	.venv/bin/python -m pytest

# Serves the API and the dashboard together on http://localhost:8000
run:
	.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

seed:
	.venv/bin/python scripts/generate_seed_data.py
