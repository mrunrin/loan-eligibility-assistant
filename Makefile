# Common tasks. On Windows run these from Git Bash, or copy the command after the colon.
PY ?= python

.PHONY: install migrate seed backend frontend test lint eval-retrieval eval docker-up docker-seed

install:
	$(PY) -m pip install -r requirements.txt -c constraints.txt

migrate:
	$(PY) -m alembic upgrade head

seed:
	$(PY) scripts/seed_demo.py

backend:
	$(PY) -m uvicorn app.main:app --port 8000

frontend:
	$(PY) -m streamlit run frontend/streamlit_app.py

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .

eval-retrieval:
	$(PY) eval/retrieval_eval.py

eval:
	npx promptfoo@0.102.0 eval --no-cache --no-write -o eval/promptfoo_results.json; $(PY) eval/check_pass_rate.py eval/promptfoo_results.json

docker-up:
	docker compose up --build

docker-seed:
	docker compose --profile seed run --rm seed
