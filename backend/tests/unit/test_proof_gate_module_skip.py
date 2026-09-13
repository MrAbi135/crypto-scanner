"""PROOF ONLY -- never merged. A whole module that skips at collection.

pytest exits 0 for this file; the backend job's suite gate must turn red.
"""

import pytest

pytest.importorskip("a_module_that_does_not_exist_for_the_gate_proof")


def test_never_runs() -> None:
    assert True
