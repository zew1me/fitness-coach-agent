import subprocess
import sys


def test_storage3_import_emits_no_pydantic_deprecation_warning():
    # storage3 < 2.33 used the Pydantic v1-style `Extra.ignore` (issue #367). Importing in a
    # fresh interpreter is the only way to observe import-time warnings that pytest's own
    # already-imported modules would hide.
    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "error::DeprecationWarning",
            "-c",
            "import storage3.types",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
