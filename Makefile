.PHONY: install test api ui seed lint

install:
	python -m venv .venv && .venv/bin/pip install -r requirements.txt

test:
	.venv/bin/python -m pytest

api:
	.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

ui:
	.venv/bin/streamlit run frontend/streamlit_app.py --server.port 8501

seed:
	.venv/bin/python scripts/generate_seed_data.py
