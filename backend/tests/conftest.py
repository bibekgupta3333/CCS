import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.simulator import CCSSimulator


@pytest.fixture(scope="session")
def simulator() -> CCSSimulator:
    sim = CCSSimulator()
    sim.load_data()
    return sim
