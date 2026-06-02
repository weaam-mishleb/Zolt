# ───────────────────────── Zolt — developer Makefile ─────────────────────────
# Quick start:  make setup   →   make run
VENV     := .venv
PY       := $(VENV)/bin/python
PIP      := $(VENV)/bin/pip
UVICORN  := $(VENV)/bin/uvicorn
API_PORT ?= 8000
ARGS     ?=
PW       ?=

.DEFAULT_GOAL := help
.PHONY: help setup venv node-modules db-up db-down run backend frontend stop \
        etl etl-dry etl-full test hash clean

help:
	@echo "Zolt — available targets:"
	@echo "  make setup       venv + Python deps (backend/etl) + npm install"
	@echo "  make run         MySQL (Docker) + FastAPI + Vite together (Ctrl+C stops)"
	@echo "  make stop        Stop all services (Docker + dev servers)"
	@echo "  make etl         Load the dataset into MySQL    (e.g. ARGS=\"--full\")"
	@echo "  make etl-dry     Parse/validate only, no DB needed"
	@echo "  make backend     Run only the FastAPI server"
	@echo "  make frontend    Run only the Vite dev server"
	@echo "  make db-up       Start the MySQL container only"
	@echo "  make test        Run backend unit tests"
	@echo "  make hash PW=..  Print a bcrypt hash for an admin password"
	@echo "  make clean       Remove venv, node_modules, build caches"

# ───────────────────────────── setup ─────────────────────────────
setup: venv node-modules
	@echo "✅  setup complete — next:  make run"

venv:
	@test -d $(VENV) || python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r backend/requirements.txt -r backend/requirements-dev.txt -r etl/requirements.txt

node-modules:
	cd frontend && npm install

# ──────────────────────────── run / stop ─────────────────────────
db-up:
	docker compose up -d

db-down:
	docker compose down

run: db-up
	@echo "▶  backend → http://127.0.0.1:$(API_PORT)/docs    frontend → http://localhost:5173"
	@echo "   (Ctrl+C stops both)"
	@trap 'kill 0' INT TERM; \
	$(UVICORN) backend.app.main:app --reload --host 0.0.0.0 --port $(API_PORT) & \
	( cd frontend && npm run dev ) & \
	wait

backend:
	$(UVICORN) backend.app.main:app --reload --port $(API_PORT)

frontend:
	cd frontend && npm run dev

stop:
	@echo "Stopping services…"
	-docker compose down
	-pkill -f "uvicorn backend.app.main:app" 2>/dev/null || true
	-pkill -f "$(CURDIR)/frontend" 2>/dev/null || true
	@echo "Stopped."

# ────────────────────────────── etl ──────────────────────────────
etl:
	$(PY) -m etl.run $(ARGS)

etl-dry:
	$(PY) -m etl.run --dry-run $(ARGS)

etl-full:
	$(PY) -m etl.run --full $(ARGS)

# ────────────────────────────── misc ─────────────────────────────
test:
	SCHEDULER_ENABLED=false $(PY) -m pytest backend/tests -q

hash:
	@$(PY) -m backend.app.security $(PW)

clean:
	rm -rf $(VENV) frontend/node_modules frontend/dist .run
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
