import os
import sys
from pathlib import Path

import pytest

# Keep Sentry off for every test run (issue #242). ``api/index.py`` calls
# ``sentry_sdk.init`` whenever ``SENTRY_DSN`` is set, and tests raise exceptions on
# purpose, so a DSN exported by the outer shell would report them to a real project.
# This must run before any test module imports ``api.index``.
os.environ["SENTRY_DSN"] = ""

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
