from unittest.mock import patch

from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.timotion_desk.const import (
    CONF_ALWAYS_CONNECTED,
    CONF_IDLE_TIMEOUT,
    CONF_MAX_HEIGHT,
    CONF_MIN_HEIGHT,
    DOMAIN,
)

from .conftest import ADDRESS, NAME, NUS, service_info

DISCOVERED = "custom_components.timotion_desk.config_flow.async_discovered_service_info"


async def test_bluetooth_discovery(hass: HomeAssistant, enable_bluetooth) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    with patch("custom_components.timotion_desk.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == NAME
    assert result["data"] == {CONF_ADDRESS: ADDRESS}
    assert result["result"].unique_id == ADDRESS


async def test_bluetooth_discovery_other_nus_device(hass: HomeAssistant, enable_bluetooth) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info(name="Sensor", address="AA:BB:CC:DD:EE:01", uuids=(NUS,)),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_user_pick(hass: HomeAssistant, enable_bluetooth) -> None:
    other = service_info(name="Sensor", address="AA:BB:CC:DD:EE:01", uuids=(NUS,))
    with patch(DISCOVERED, return_value=[service_info(), other]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        assert result["type"] is FlowResultType.FORM
        choices = result["data_schema"].schema[CONF_ADDRESS].container
        assert list(choices) == [ADDRESS]
        with patch("custom_components.timotion_desk.async_setup_entry", return_value=True):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_ADDRESS: ADDRESS}
            )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS}


async def test_user_nothing_found(hass: HomeAssistant, enable_bluetooth) -> None:
    with patch(DISCOVERED, return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_already_configured(hass: HomeAssistant, enable_bluetooth, entry) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options(hass: HomeAssistant, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_MIN_HEIGHT: 110,
            CONF_MAX_HEIGHT: 80,
            CONF_IDLE_TIMEOUT: 60,
            CONF_ALWAYS_CONNECTED: False,
        },
    )
    assert result["errors"] == {"base": "min_above_max"}
    options = {
        CONF_MIN_HEIGHT: 75,
        CONF_MAX_HEIGHT: 120,
        CONF_IDLE_TIMEOUT: 30,
        CONF_ALWAYS_CONNECTED: True,
        "preset_1_name": "Sit",
        "preset_1_height": 78,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], options)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == options
