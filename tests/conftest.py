from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def kniga_path() -> Path:
    return FIXTURES / "Книга31.csv"


@pytest.fixture
def sales_path() -> Path:
    return FIXTURES / "sales_sample.csv"


@pytest.fixture
def example_xlsx_path() -> Path:
    return FIXTURES / "claude_example.xlsx"
