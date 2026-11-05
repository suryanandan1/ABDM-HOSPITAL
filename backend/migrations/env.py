"""Alembic environment: the database URL and table metadata come from the application itself."""

from logging.config import fileConfig

from alembic import context

from app import models  # noqa: F401  (registers every table on Base.metadata)
from app.database import Base, engine, is_sqlite

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata
# SQLite cannot ALTER most column properties; batch mode rebuilds the table instead.
options = {"target_metadata": target_metadata, "render_as_batch": is_sqlite, "compare_type": True}


def run_migrations_offline() -> None:
    context.configure(url=str(engine.url), literal_binds=True, dialect_opts={"paramstyle": "named"}, **options)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:  # handed in by init_db.py / tests
        context.configure(connection=connection, **options)
        with context.begin_transaction():
            context.run_migrations()
        return
    with engine.connect() as connection:
        context.configure(connection=connection, **options)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
