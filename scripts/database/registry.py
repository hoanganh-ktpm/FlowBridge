"""Repository-only administration registry, not a service-to-service boundary."""

import sys
from functools import cache
from importlib import import_module, util
from pathlib import Path

DATABASES = ("sales", "warehouse", "logistics", "core")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def migration_path(database: str) -> Path:
    if database not in DATABASES:
        raise ValueError("Unknown database")
    if database == "core":
        return REPOSITORY_ROOT / "backend/app/core/db/migrations"
    return REPOSITORY_ROOT / "services" / f"{database}-api" / "migrations"


@cache
def models_for(database: str):
    if database not in DATABASES:
        raise ValueError("Unknown database")
    if database == "core":
        return import_module("app.core.db.models")
    path = REPOSITORY_ROOT / "services" / f"{database}-api" / "app/db/models.py"
    module_name = f"flowbridge_database_{database}"
    specification = util.spec_from_file_location(module_name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError("Database models are unavailable in this source checkout")
    module = util.module_from_spec(specification)
    sys.modules[module_name] = module
    try:
        specification.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def metadata_for(database: str):
    module = models_for(database)
    return getattr(module, f"{database.title()}Base").metadata
