"""Alembic environment — URL from AppSettings; batch mode for SQLite."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from infrastructure.config.settings import AppSettings
from infrastructure.persistence import orm as _orm  # noqa: F401
from infrastructure.persistence.database import ensure_sqlite_parent_dir
from infrastructure.persistence.metadata import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    explicit_url = config.attributes.get("database_url")
    if explicit_url is not None:
        if not isinstance(explicit_url, str) or not explicit_url.strip():
            raise ValueError("Alembic database_url attribute must be a nonblank string")
        database_url = explicit_url.strip()
    else:
        database_url = AppSettings.load().database_url
    ensure_sqlite_parent_dir(database_url)
    return database_url


def run_migrations_offline() -> None:
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = get_database_url()
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = url
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
