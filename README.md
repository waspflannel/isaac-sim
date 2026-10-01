# Factory Intelligence

Local development foundation for an intelligent factory layer, demonstrated with simulated production.

## Start locally

Requires Python 3.13 (uv can install it), uv, Node.js 22.12+ and Docker Compose.
Run these commands from the repository root in PowerShell:

```powershell
Copy-Item .env.example .env # First setup only; preserve your existing .env.
uv sync --locked
npm --prefix ./frontend ci
docker compose up -d --wait
```

Start the API:

```powershell
uv run --env-file .env uvicorn factory_intelligence.api:app --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
npm --prefix ./frontend run dev
```

Open http://127.0.0.1:5173. The screen checks API liveness and PostgreSQL readiness. API reference: http://127.0.0.1:8000/docs.

PostgreSQL is bound to localhost on port 54329, with data in a Docker volume. The `POSTGRES_*` settings initialize Docker's database; the matching `PG*` settings connect the API. Keep both sets in sync. Changing initialization credentials does not change an existing database volume's credentials. Example credentials are for local development only.

## Smoke event capture

```powershell
uv run factory-simulate --run-id smoke-001 | uv run factory-collect
```

This runs three single-station operations in SimPy and captures NDJSON events in `.data/edge/spool.sqlite3`. It reports three new events. Repeat the same command and it reports zero: identical event IDs and payloads are deduplicated; conflicting payloads fail explicitly. Use a new run ID for a new run.

This is raw local capture only. Forwarding to PostgreSQL, full event validation, the five-station model, metrics, and investigation workflows are still to be implemented. The UI does not display simulated production metrics yet.

## Isaac Sim

Install Isaac Sim separately and use its bundled Python. Do not install it into the application virtual environment or commit its runtime/assets here.

```powershell
& 'C:\path\to\isaacsim\python.bat' .\isaac\smoke.py --frames 120
```

The script opens an empty application, advances update frames, prints elapsed time, and closes. It is a starting point for the local compatibility experiment; it does not benchmark factory physics. Isaac Sim and the physical cell have not yet been tested on this machine.

## Checks

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
npm --prefix ./frontend run lint
npm --prefix ./frontend run build
```

Tests cover capture durability, duplicate replay, conflicting IDs, invalid inputs, and API readiness during database failure. To stop the local database while retaining its data, run `docker compose stop`.

## Layout

- `src/factory_intelligence/`: FastAPI app, SimPy smoke source, SQLite capture CLI.
- `frontend/`: React + JavaScript + Vite, plain CSS.
- `isaac/`: scripts for the separate NVIDIA runtime.
- `tests/`: foundation checks.
- `compose.yaml`: local PostgreSQL.

Planning documents, secrets, dependencies, generated output, event data, and downloaded simulation assets remain local through `.gitignore`. Dependency locks are committed for reproducible installs.
