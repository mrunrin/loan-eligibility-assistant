"""
Alembic migration environment.

Uses a SYNC psycopg2 connection for migrations (Alembic cannot drive asyncpg
directly). The application runtime uses asyncpg — two different URL schemes
for two different purposes. DATABASE_URL in the environment always contains
the asyncpg URL; we swap the driver here.
"""

import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

import app.models  # noqa: F401 — side-effect: registers all ORM models on Base

# Import Base so Alembic autogenerate can see all model metadata
from app.database import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False preserves uvicorn/app loggers so startup
    # output is not silenced when migrations run inside the server process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata
_MIGRATION_LOCK_KEY = 727_100_001  # any fixed bigint, unique to this app's migrations


def _sync_url(url: str) -> str:
    """Replace asyncpg driver with psycopg2 for Alembic's sync connection."""
    return re.sub(r"postgresql\+asyncpg", "postgresql+psycopg2", url)


def get_url() -> str:
    from app.config import DATABASE_URL  # loads .env, same source the app uses

    return _sync_url(DATABASE_URL)


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            # Replicas starting together queue here; the lock is released when the migration transaction ends.
            connection.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _MIGRATION_LOCK_KEY})
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
