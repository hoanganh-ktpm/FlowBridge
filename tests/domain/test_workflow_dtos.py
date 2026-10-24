import json
import subprocess
import sys

import pytest
from scripts.database.registry import REPOSITORY_ROOT


@pytest.mark.parametrize(
    "domain,model,body",
    [
        ("sales", "Line", {"product_ref": "bad", "sku": "A", "quantity": 1}),
        (
            "sales",
            "Line",
            {"product_ref": "00000000-0000-0000-0000-000000000001", "sku": "A", "quantity": -1},
        ),
        (
            "sales",
            "CreateOrder",
            {
                "org_id": "00000000-0000-0000-0000-000000000001",
                "order_number": "historical_order",
                "lines": [],
            },
        ),
        ("warehouse", "OrderRef", {"org_id": "bad", "order_id": "bad"}),
        (
            "logistics",
            "ChangeState",
            {
                "org_id": "00000000-0000-0000-0000-000000000001",
                "order_id": "00000000-0000-0000-0000-000000000002",
                "status": "FAKE",
            },
        ),
    ],
)
def test_domain_dtos_validate_independently_without_core_or_database(domain, model, body):
    directory = REPOSITORY_ROOT / "services" / (domain + "-api")
    program = f"""import sys,json
sys.path.insert(0,{str(directory)!r})
from app.runtime import {model}
from pydantic import ValidationError
try:
    {model}.model_validate(json.loads({json.dumps(body)!r}))
except ValidationError:
    pass
else:
    raise AssertionError('invalid DTO accepted')
assert 'app.core' not in sys.modules
assert 'scripts.database' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", program], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
