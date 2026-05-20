PY ?= python3.12
PORT ?= 8000
DB ?= aicrew.db

.PHONY: help init seed api demo test fmt clean

help:
	@echo "AiCrewStudio MVP (stdlib only, offline-friendly)"
	@echo ""
	@echo "  make init     - create empty SQLite DB and run schema"
	@echo "  make seed     - seed demo project 'AI Weekly' with RU+EN agents and channels"
	@echo "  make api      - start API+UI server on :$(PORT)"
	@echo "  make demo     - init+seed+run one full pipeline (mock providers), prints summary"
	@echo "  make test     - run unit tests"
	@echo "  make clean    - delete SQLite DB and media/"
	@echo ""

init:
	$(PY) -m aicrew.cli init --db $(DB)

seed:
	$(PY) -m aicrew.cli seed --db $(DB)

api:
	AICREW_DB=$(DB) $(PY) -m aicrew.cli serve --port $(PORT)

demo:
	$(PY) -m aicrew.cli demo --db $(DB)

test:
	$(PY) -m unittest discover -s tests -v

fmt:
	@echo "(no formatter installed in offline sandbox)"

clean:
	rm -f $(DB) && rm -rf media/
