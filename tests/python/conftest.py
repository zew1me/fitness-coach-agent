import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The Settings default ("replace-me") is a 10-byte placeholder, so every JWT the suite
# signed or verified tripped PyJWT's InsecureKeyLengthWarning (issue #261). Pin a
# 33-byte test-only secret (HS256 needs >= 32) before any test module imports
# backend.config. This also keeps a developer's real APP_JWT_SECRET (exported, or from
# .env) out of the suite.
os.environ["APP_JWT_SECRET"] = "test-only-jwt-secret-0123456789ab"


@pytest.fixture(autouse=True)
def clear_fastapi_dependency_overrides():
    try:
        yield
    finally:
        api_index = sys.modules.get("api.index")
        if api_index is not None:
            api_index.app.dependency_overrides.clear()
