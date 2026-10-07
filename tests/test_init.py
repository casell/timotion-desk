import time
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components import bluetooth
from homeassistant.components.button import DOMAIN as BUTTON
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.cover import (
    ATTR_POSITION,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_SET_COVER_POSITION,
    SERVICE_STOP_COVER,
)
from homeassistant.components.cover import (
    DOMAIN as COVER,
)
from homeassistant.components.number import ATTR_VALUE, SERVICE_SET_VALUE
from homeassistant.components.number import DOMAIN as NUMBER
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.timotion_desk.const import (
    CONF_ALWAYS_CONNECTED,
    CONF_CONNECTION_MODE,
    CONF_EXPOSE_COVER,
    CONF_IDLE_TIMEOUT,
    CONF_KEEP_AWAKE_INTERVAL,
    CONF_MAX_HEIGHT,
    CONF_MIN_HEIGHT,
    DOMAIN,
    MODE_ALWAYS,
    MODE_KEEP_AWAKE,
)

from .conftest import service_info

COVER_ID = "cover.stand_up_1234"
HEIGHT_ID = "sensor.stand_up_1234_height"
TARGET_ID = "number.stand_up_1234_target_height"
MOVING_ID = "binary_sensor.stand_up_1234_moving"
CONNECTED_ID = "binary_sensor.stand_up_1234_connection"


async def setup(hass: HomeAssistant, entry, options=None):
    if options:
        hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data.desk


async def call(hass, domain, service, entity_id, **data):
    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
    )
    await hass.async_block_till_done()


async def test_setup_reads_state(
    hass: HomeAssistant, enable_bluetooth, desk_present, fake_desk, entry
):
    desk = await setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert desk.calls[0] == "connect"  # initial read of height, limits, presets
    assert hass.states.get(HEIGHT_ID).state == "78.0"
    assert hass.states.get(COVER_ID) is None  # opt-in
    assert hass.states.get(CONNECTED_ID).state == STATE_ON
    assert hass.states.get("button.stand_up_1234_handset_preset_2").state != STATE_UNAVAILABLE


async def test_cover_position(
    hass: HomeAssistant, enable_bluetooth, desk_present, fake_desk, entry
):
    await setup(hass, entry, {CONF_EXPOSE_COVER: True})
    # 780 mm within the desk limits 705-1250 mm
    position = hass.states.get(COVER_ID).attributes["current_position"]
    assert position == round((780 - 705) * 100 / (1250 - 705))


async def test_cover_removed_when_option_off(
    hass: HomeAssistant, enable_bluetooth, desk_present, fake_desk, entry
):
    await setup(hass, entry, {CONF_EXPOSE_COVER: True})
    registry = er.async_get(hass)
    assert registry.async_get(COVER_ID) is not None
    hass.config_entries.async_update_entry(entry, options={CONF_EXPOSE_COVER: False})
    await hass.async_block_till_done()  # options change reloads the entry
    assert registry.async_get(COVER_ID) is None
    assert hass.states.get(COVER_ID) is None
    assert hass.states.get(HEIGHT_ID) is not None


async def test_not_advertising_retries(hass: HomeAssistant, enable_bluetooth, fake_desk, entry):
    with patch(
        "homeassistant.components.bluetooth.async_ble_device_from_address", return_value=None
    ):
        await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_cover_commands(
    hass: HomeAssistant, enable_bluetooth, desk_present, fake_desk, entry
):
    desk = await setup(
        hass, entry, {CONF_MIN_HEIGHT: 70.0, CONF_MAX_HEIGHT: 120.0, CONF_EXPOSE_COVER: True}
    )
    await call(hass, COVER, SERVICE_SET_COVER_POSITION, COVER_ID, **{ATTR_POSITION: 50})
    assert desk.calls[-1] == ("move_to", 950)
    await call(hass, COVER, SERVICE_OPEN_COVER, COVER_ID)
    assert desk.calls[-1] == ("move_to", 1200)
    assert hass.states.get(COVER_ID).attributes["current_position"] == 100
    await call(hass, COVER, SERVICE_CLOSE_COVER, COVER_ID)
    assert desk.calls[-1] == ("move_to", 700)
    assert hass.states.get(COVER_ID).state == "closed"
    await call(hass, COVER, SERVICE_STOP_COVER, COVER_ID)
    assert desk.calls[-1] == "stop"


async def test_number_and_buttons(
    hass: HomeAssistant, enable_bluetooth, desk_present, fake_desk, entry
):
    desk = await setup(hass, entry, {"preset_1_name": "Sit", "preset_1_height": 72.5})
    target = hass.states.get(TARGET_ID)
    assert (target.attributes["min"], target.attributes["max"]) == (70.5, 125.0)
    await call(hass, NUMBER, SERVICE_SET_VALUE, TARGET_ID, **{ATTR_VALUE: 101.3})
    assert desk.calls[-1] == ("move_to", 1013)
    await call(hass, BUTTON, SERVICE_PRESS, "button.stand_up_1234_sit")
    assert desk.calls[-1] == ("move_to", 725)
    await call(hass, BUTTON, SERVICE_PRESS, "button.stand_up_1234_handset_preset_3")
    assert desk.calls[-1] == ("move_to", 1206)
    await call(hass, BUTTON, SERVICE_PRESS, "button.stand_up_1234_stop")
    assert desk.calls[-1] == "stop"


async def test_moving_sensor(hass: HomeAssistant, enable_bluetooth, desk_present, fake_desk, entry):
    desk = await setup(hass, entry, {CONF_EXPOSE_COVER: True})
    desk.moving = "up"
    desk.fire()
    await hass.async_block_till_done()
    assert hass.states.get(MOVING_ID).state == STATE_ON
    assert hass.states.get(COVER_ID).state == "opening"


async def test_idle_disconnect(
    hass: HomeAssistant, enable_bluetooth, desk_present, fake_desk, entry
):
    desk = await setup(hass, entry, {CONF_IDLE_TIMEOUT: 30})
    await call(hass, NUMBER, SERVICE_SET_VALUE, TARGET_ID, **{ATTR_VALUE: 100})
    assert desk.connected
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=20))
    await hass.async_block_till_done()
    assert desk.connected
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert not desk.connected
    # Last state stays visible while the desk is released for the vendor app.
    assert hass.states.get(HEIGHT_ID).state == "100.0"


async def test_stays_connected_while_moving(
    hass: HomeAssistant, enable_bluetooth, desk_present, fake_desk, entry
):
    desk = await setup(hass, entry, {CONF_IDLE_TIMEOUT: 30})
    desk.moving = "down"  # e.g. the handset is driving the desk
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert desk.connected
    desk.moving = None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=62))
    await hass.async_block_till_done()
    assert not desk.connected


async def test_always_connected(
    hass: HomeAssistant, enable_bluetooth, desk_present, fake_desk, entry
):
    desk = await setup(hass, entry, {CONF_CONNECTION_MODE: MODE_ALWAYS})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=600))
    await hass.async_block_till_done()
    assert desk.connected
    # Dropped connection: reconnect after the delay.
    desk.connected = False
    desk.fire()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()
    assert desk.connected


async def test_always_connected_legacy_option(
    hass: HomeAssistant, enable_bluetooth, desk_present, fake_desk, entry
):
    """The boolean option saved before 0.1.5 still means always connected."""
    desk = await setup(hass, entry, {CONF_ALWAYS_CONNECTED: True})
    assert entry.runtime_data.connection_mode == MODE_ALWAYS
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=600))
    await hass.async_block_till_done()
    assert desk.connected


async def test_keep_awake(
    hass: HomeAssistant, enable_bluetooth, desk_present, fake_desk, entry, freezer
):
    desk = await setup(
        hass,
        entry,
        {
            CONF_CONNECTION_MODE: MODE_KEEP_AWAKE,
            CONF_KEEP_AWAKE_INTERVAL: 10,
            CONF_IDLE_TIMEOUT: 30,
        },
    )

    async def advance(delta: timedelta) -> None:
        freezer.tick(delta)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    await advance(timedelta(seconds=31))
    assert not desk.connected  # released after the idle timeout, like on demand
    await advance(timedelta(minutes=9, seconds=30))
    # Keep-awake connection, released right away (not after the idle timeout).
    assert desk.calls[-2:] == ["connect", "disconnect"] and not desk.connected
    await advance(timedelta(minutes=10))
    assert desk.calls.count("connect") == 3  # every interval
    assert not desk.connected


async def test_keep_awake_keeps_a_command_connection(
    hass: HomeAssistant, enable_bluetooth, desk_present, fake_desk, entry, freezer
):
    """A command that arrives while a keep-awake connection is set up is not cut off."""
    desk = await setup(
        hass,
        entry,
        {
            CONF_CONNECTION_MODE: MODE_KEEP_AWAKE,
            CONF_KEEP_AWAKE_INTERVAL: 10,
            CONF_IDLE_TIMEOUT: 30,
        },
    )
    coordinator = entry.runtime_data
    await desk.disconnect()
    connect = desk.connect

    async def connect_while_command_arrives() -> None:
        await connect()
        freezer.tick(timedelta(seconds=1))
        coordinator._last_command = time.monotonic()  # as async_move / async_stop do

    desk.connect = connect_while_command_arrives
    freezer.tick(timedelta(minutes=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert desk.connected  # kept for the command's idle window
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not desk.connected


async def test_keep_awake_skips_silent_desk(
    hass: HomeAssistant, enable_bluetooth, desk_present, fake_desk, entry
):
    desk = await setup(hass, entry, {CONF_CONNECTION_MODE: MODE_KEEP_AWAKE})
    await desk.disconnect()
    with patch("homeassistant.components.bluetooth.async_address_present", return_value=False):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=46))
        await hass.async_block_till_done()
    assert desk.calls.count("connect") == 1  # only the initial one


async def test_stale_connection_closed(
    hass: HomeAssistant, enable_bluetooth, desk_present, fake_desk, entry
):
    desk = await setup(hass, entry, {CONF_CONNECTION_MODE: MODE_ALWAYS})
    now = dt_util.utcnow()
    desk.last_frame_age = 3.0  # live link: frames keep coming
    async_fire_time_changed(hass, now + timedelta(seconds=6))
    await hass.async_block_till_done()
    assert desk.connected
    desk.last_frame_age = 20.0  # looks connected, but nothing arrives
    async_fire_time_changed(hass, now + timedelta(seconds=12))
    await hass.async_block_till_done()
    assert desk.calls.count("disconnect") == 1
    async_fire_time_changed(hass, now + timedelta(seconds=24))  # reconnect delay
    await hass.async_block_till_done()
    assert desk.connected and desk.calls.count("connect") == 2


@pytest.mark.parametrize(
    ("silent_s", "message"),
    [(2, "Cannot connect to"), (600, "probably in standby")],
)
async def test_connect_failure_raises(
    hass: HomeAssistant, enable_bluetooth, desk_present, fake_desk, entry, silent_s, message
):
    desk = await setup(hass, entry)
    await desk.disconnect()
    desk.fail_connect = True
    last = MagicMock(time=bluetooth.MONOTONIC_TIME() - silent_s)
    with (
        patch("homeassistant.components.bluetooth.async_last_service_info", return_value=last),
        pytest.raises(HomeAssistantError, match=message),
    ):
        await hass.services.async_call(
            NUMBER, SERVICE_SET_VALUE, {ATTR_ENTITY_ID: TARGET_ID, ATTR_VALUE: 100}, blocking=True
        )


async def test_unavailable_when_not_advertising(
    hass: HomeAssistant, enable_bluetooth, desk_present, fake_desk, entry
):
    desk = await setup(hass, entry)
    await desk.disconnect()
    with patch("homeassistant.components.bluetooth.async_address_present", return_value=False):
        desk.fire()
        await hass.async_block_till_done()
        assert hass.states.get(HEIGHT_ID).state == STATE_UNAVAILABLE
        assert hass.states.get(CONNECTED_ID).state == "off"  # diagnostic stays available


async def test_unload(hass: HomeAssistant, enable_bluetooth, desk_present, fake_desk, entry):
    desk = await setup(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not desk.connected


async def test_brand_icon(hass: HomeAssistant):
    """The integration ships its own icon (served by HA's brands view)."""
    integration = await async_get_integration(hass, DOMAIN)
    assert integration.has_branding
    brand = integration.file_path / "brand"
    assert {p.name for p in brand.iterdir()} >= {"icon.png", "icon@2x.png"}


@pytest.mark.parametrize(("mode", "expect_issue"), [(MODE_KEEP_AWAKE, True), ("on_demand", False)])
async def test_unreachable_issue(
    hass: HomeAssistant,
    enable_bluetooth,
    desk_present,
    fake_desk,
    entry,
    freezer,
    mode,
    expect_issue,
):
    desk = await setup(hass, entry, {CONF_CONNECTION_MODE: mode})
    await desk.disconnect()
    issue_id = f"unreachable_{entry.entry_id}"
    silent = MagicMock(time=bluetooth.MONOTONIC_TIME() - 600, source="AA:AA:AA:AA:AA:AA", rssi=-70)
    with patch("homeassistant.components.bluetooth.async_last_service_info", return_value=silent):
        freezer.tick(timedelta(minutes=6))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert (issue is not None) is expect_issue
    if expect_issue:
        assert issue.translation_key == "unreachable"
        # The desk is heard again: the notice goes away.
        entry.runtime_data._on_advertisement(service_info(), None)
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
