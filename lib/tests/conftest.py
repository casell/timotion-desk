"""Fixtures replaying the captures in the repo root (skipped outside the repo)."""

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CAPTURES = REPO / "captures"


def _readers():
    path = REPO / "tools" / "btsnoop.py"
    if not path.exists():
        pytest.skip("capture readers not available outside the repo")
    spec = importlib.util.spec_from_file_location("btsnoop_tools", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def handset_capture() -> list[bytes]:
    return list(_readers().read_bluetoothctl(CAPTURES / "handset_bluetoothctl.txt"))


@pytest.fixture(scope="session")
def probe_notifications() -> list[bytes]:
    """All notifications recorded by tools/probe.py --log during the hardware tests."""
    logs = sorted(CAPTURES.glob("probe_*.log"))
    if not logs:
        pytest.skip("no probe logs")
    frames = []
    for log in logs:
        for line in log.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "<-":
                frames.append(bytes.fromhex(parts[2] if len(parts) > 2 else ""))
    return frames
