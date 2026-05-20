"""AiCrewStudio – stdlib-only MVP build.

The package is intentionally written without third-party dependencies so it can
run in environments that have no internet access. Module shape mirrors the
production layout described in ``docs/02-architecture.md`` so that swapping
``http.server`` for FastAPI and ``sqlite3`` for SQLAlchemy/Postgres later does
not require a refactor – only re-implementing the same public functions.
"""

__version__ = "0.1.0"
