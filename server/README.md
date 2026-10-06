# allskyhub hub (server)

Milestone M2: device pairing, live and latest image, device status (SPEC §6, §9). Stack as in
docs/SPEC.md §2: FastAPI, PostgreSQL (SQLAlchemy async, Alembic), Jinja2 + HTMX without a build
step. The hosted instance runs at allskyhub.org; anyone can run their own.

## Development

Requirements: Python ≥ 3.11 (system), [uv](https://docs.astral.sh/uv/), Docker (only for the
local database).

```bash
tools/dev-postgres.sh                 # PostgreSQL 17 in Docker on 127.0.0.1:55441, writes .dev/env
source .dev/env
uv sync
uv run allskyhub-server migrate       # apply the schema
uv run allskyhub-server dev           # http://127.0.0.1:8000
```

## Tests

```bash
source .dev/env
uv run pytest server
```

The tests use the database from `ALLSKYHUB_SERVER_TEST_DATABASE_URL` and recreate its schema
`public` on every run. Never point it at a database with real data. Without the variable the hub
tests are skipped.

## Configuration

Environment variables with the prefix `ALLSKYHUB_SERVER_`, see
`src/allskyhub_server/settings.py`. Schema changes: edit `models.py`, then
`uv run alembic -c server/alembic.ini revision --autogenerate -m "..."` and review the result.
