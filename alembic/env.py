from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.core.database import Base
from app.models import job  # noqa: F401 — registers Job on Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# DATABASE_URL comes from the app's own settings (env / .env) — read
# directly here, NOT round-tripped through config.set_main_option() /
# engine_from_config(). Those go through Python's configparser, which
# treats a literal `%` as the start of an interpolation sequence
# (%(name)s-style) — a URL-encoded password (e.g. `%40` for `@`) breaks
# that parser with a cryptic "invalid interpolation syntax" error that
# has nothing to do with the URL itself being wrong. Reading the URL
# straight from settings sidesteps that whole class of bug.
DATABASE_URL = get_settings().DATABASE_URL

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=DATABASE_URL, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(DATABASE_URL, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
