"""Fixtures for the timotion_desk integration tests (pytest-homeassistant-custom-component)."""

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from timotion_ble import ConfigFrame

from custom_components.timotion_desk.const import DOMAIN

ADDRESS = "AA:BB:CC:DD:EE:FF"
NAME = "stand UP- 1234"
NUS = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"


def service_info(
    name: str = NAME, address: str = ADDRESS, uuids=(NUS,)
) -> BluetoothServiceInfoBleak:
    device = MagicMock(address=address)
    device.name = name
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data={},
        service_data={},
        service_uuids=list(uuids),
        source="local",
        device=device,
        advertisement=MagicMock(),
        connectable=True,
        time=0,
        tx_power=None,
    )


class FakeDesk:
    """Stands in for timotion_ble.TimotionDesk."""

    instance: "FakeDesk"

    def __init__(self, ble_device) -> None:
        FakeDesk.instance = self
        self.address = ble_device.address
        self.name = ble_device.name
        self.connected = False
        self.height_mm: int | None = None
        self.moving = None
        self.handset_active = False
        self.at_limit = None
        self.config: ConfigFrame | None = None
        self.calls: list = []
        self.fail_connect = False
        self._callbacks: list = []

    def register_callback(self, callback):
        self._callbacks.append(callback)
        return lambda: self._callbacks.remove(callback)

    def fire(self) -> None:
        for callback in list(self._callbacks):
            callback()

    def set_ble_device(self, device) -> None:
        self.calls.append(("set_ble_device", device.address))

    async def connect(self) -> None:
        if self.fail_connect:
            raise TimeoutError("no answer")
        if not self.connected:
            self.connected = True
            self.height_mm = 780
            self.config = ConfigFrame(None, (705, 1250, 780, 1100, 1206, 1110))
            self.calls.append("connect")
            self.fire()

    async def disconnect(self) -> None:
        if self.connected:
            self.connected = False
            self.calls.append("disconnect")
            self.fire()

    async def move_to(self, mm: int) -> str:
        self.calls.append(("move_to", mm))
        self.height_mm = mm
        self.fire()
        return "arrived"

    async def stop(self) -> None:
        self.calls.append("stop")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def desk_present() -> Generator[MagicMock]:
    """The desk is advertising: HA has a BLEDevice for it."""
    info = service_info()
    with (
        patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address",
            return_value=info.device,
        ),
        patch("homeassistant.components.bluetooth.async_address_present", return_value=True),
    ):
        yield info.device


@pytest.fixture
def fake_desk() -> Generator[type[FakeDesk]]:
    with patch("custom_components.timotion_desk.TimotionDesk", FakeDesk):
        yield FakeDesk


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title=NAME, unique_id=ADDRESS, data={CONF_ADDRESS: ADDRESS}
    )
    entry.add_to_hass(hass)
    return entry
