import subprocess
import sys

import pytest
from scripts.database.fixtures.business import business_seed_rows
from scripts.database.registry import REPOSITORY_ROOT


@pytest.mark.parametrize("database,expected", [("sales", 38), ("warehouse", 54), ("logistics", 22)])
def test_business_fixtures_preserve_counts(database, expected):
    assert sum(map(len, business_seed_rows()[database].values())) == expected


@pytest.mark.parametrize("database,expected", [("sales", 7), ("warehouse", 13), ("logistics", 6)])
def test_domain_models_import_independently(database, expected):
    directory = REPOSITORY_ROOT / "services" / f"{database}-api"
    program = (
        "import sys; "
        f"sys.path.insert(0, {str(directory)!r}); "
        "from app.db import models; "
        f"assert len(models.{database.title()}Base.metadata.tables) == {expected}; "
        "assert 'app.core' not in sys.modules; "
        "assert 'scripts.database' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", program], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
