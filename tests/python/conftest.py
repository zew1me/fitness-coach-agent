import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def clear_fastapi_dependency_overrides():
    try:
        yield
    finally:
        api_index = sys.modules.get("api.index")
        if api_index is not None:
            api_index.app.dependency_overrides.clear()
