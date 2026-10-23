from copy import deepcopy

import pytest

from app.core.data_products.reading import validate_rows
from app.core.data_products.schemas import DataContractDocument
from app.core.data_products.validation import supported_pattern
from app.platform.http.errors import CoreError

from .fakes import contract_document


def document_with(field):
    document = contract_document()
    document["fields"][1] = field
    return DataContractDocument.model_validate(document)


@pytest.mark.parametrize(
    "pattern,allowed",
    [
        ("^SKU-[A-Z0-9]+$", True),
        ("^[a-z_]+$", True),
        ("^(a+)+$", False),
        ("^a+a+a+$", False),
        ("(.*)+", False),
    ],
)
def test_pattern_profile_does_not_run_unbounded_backtracking(pattern, allowed):
    assert supported_pattern(pattern) is allowed


def test_datetime_wire_output_is_losslessly_normalized_to_utc():
    document = document_with(
        {
            "column": "changed_at",
            "name": "changed",
            "physical_type": "TIMESTAMP WITH TIME ZONE",
            "wire_type": "date-time",
            "nullable": False,
            "description": None,
        }
    )
    rows = validate_rows(
        document,
        ("sku", "changed"),
        [{"sku": "SKU-1", "changed_at": "2026-10-23T12:30:00.123456+07:00"}],
    )
    assert rows == [{"sku": "SKU-1", "changed": "2026-10-23T05:30:00.123456Z"}]
    with pytest.raises(CoreError):
        validate_rows(
            document,
            ("sku", "changed"),
            [{"sku": "SKU-1", "changed_at": "2026-10-23T12:30:00.123456789Z"}],
        )


@pytest.mark.parametrize("value", [True, "12", None, 2**60, float("inf"), float("nan")])
def test_numeric_profile_rejects_coercion_nonfinite_and_unsafe_integers(value):
    document = document_with(
        {
            "column": "quantity",
            "name": "quantity",
            "physical_type": "INTEGER",
            "wire_type": "integer",
            "nullable": False,
            "description": None,
        }
    )
    with pytest.raises(CoreError):
        validate_rows(document, ("sku", "quantity"), [{"sku": "SKU-1", "quantity": value}])


def test_published_complex_pattern_fails_closed_even_if_loaded_by_an_adapter():
    document = deepcopy(contract_document())
    document["fields"][0]["constraints"] = {"pattern": "^(a+)+$"}
    with pytest.raises(CoreError):
        validate_rows(DataContractDocument.model_validate(document), ("sku",), [{"sku": "SKU-1"}])
