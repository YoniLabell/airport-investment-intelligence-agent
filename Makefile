.PHONY: install test run api ui seed lint

install:
	python -m venv .venv && .venv/bin/pip install -r requirements.txt

test:
	.venv/bin/python -m pytest

# Both services at once — this is what you want for local development.
run:
	./scripts/run_local.sh

# The API alone. Serves JSON only; there is no dashboard on port 8000.
api:
	.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

ui:
	.venv/bin/streamlit run frontend/streamlit_app.py --server.port 8501

seed:
	.venv/bin/python scripts/generate_seed_data.py
