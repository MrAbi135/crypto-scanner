"""PROOF ONLY -- never merged. An integration test that skips when it runs.

pytest exits 0 for this file; the integration job's suite gate must turn red.
"""

import pytest

pytestmark = pytest.mark.integration


def test_skips_at_runtime() -> None:
    pytest.skip("deliberate skip to prove the suite gate fails the job")
