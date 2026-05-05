"""
Alembic environment configuration.

Reads DATABASE_URL from .env (via python-dotenv) and wires up
linkedin_db.Base.metadata so autogenerate can diff against the
live models.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from alembic import context

# Load .env before anything else so DATABASE_URL is available.
load_dotenv()

# -- Alembic Config object --------------------------------------------------
config = context.config

# Override sqlalchemy.url from environment so credentials stay out of INI.
config.set_main_option(
    "sqlalchemy.url",
    os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/linkedin_leads",
    ),
)

# Python logging from alembic.ini — only when running from the Alembic CLI.
# When init() calls upgrade() programmatically, the app's loggers are
# already configured and fileConfig would clobber them.
if config.config_file_name is not None and not config.attributes.get("skip_logging"):
    fileConfig(config.config_file_name)

# -- Import all models so Alembic sees their tables -------------------------
# app.linkedin.db.Base is the declarative_base() that every model inherits from.
from app.linkedin.db import Base  # noqa: E402
# Side-effect import: registers the dashboard_users table on Base.metadata.
from app.auth import users as _auth_users  # noqa: E402, F401

target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Offline (SQL script) mode
# ---------------------------------------------------------------------------

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Emits SQL to stdout instead of executing against a live database.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online (live database) mode
# ---------------------------------------------------------------------------

def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
