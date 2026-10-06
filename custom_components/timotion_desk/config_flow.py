"""Config and options flow for TiMOTION Desk."""

from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS, UnitOfLength, UnitOfTime
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_ALWAYS_CONNECTED,
    CONF_CONNECTION_MODE,
    CONF_EXPOSE_COVER,
    CONF_IDLE_TIMEOUT,
    CONF_KEEP_AWAKE_INTERVAL,
    CONF_MAX_HEIGHT,
    CONF_MIN_HEIGHT,
    CONF_PRESET_HEIGHT,
    CONF_PRESET_NAME,
    CONNECTION_MODES,
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_KEEP_AWAKE_INTERVAL,
    DOMAIN,
    MODE_ALWAYS,
    MODE_ON_DEMAND,
    NAME_PREFIX,
    PRESET_COUNT,
)


def _is_desk(info: BluetoothServiceInfoBleak) -> bool:
    # By name only: the advertisement carries just flags and the name, the NUS service
    # UUID is in the scan response, which passive scanners never see.
    return (info.name or "").startswith(NAME_PREFIX)


class TimotionConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovery: BluetoothServiceInfoBleak | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> OptionsFlow:
        return TimotionOptionsFlow()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        if not _is_desk(discovery_info):
            return self.async_abort(reason="not_supported")
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovery is not None
        if user_input is not None:
            return self._create(self._discovery)
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._discovery.name},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        configured = self._async_current_ids(include_ignore=False)
        desks = {
            info.address: info
            for info in async_discovered_service_info(self.hass, connectable=True)
            if _is_desk(info) and info.address not in configured
        }
        if user_input is not None:
            info = desks[user_input[CONF_ADDRESS]]
            await self.async_set_unique_id(info.address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self._create(info)
        if not desks:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {a: f"{info.name} ({a})" for a, info in desks.items()}
                    )
                }
            ),
        )

    def _create(self, info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        return self.async_create_entry(title=info.name, data={CONF_ADDRESS: info.address})


def _height_selector() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=50,
            max=150,
            step=0.1,
            unit_of_measurement=UnitOfLength.CENTIMETERS,
            mode=NumberSelectorMode.BOX,
        )
    )


class TimotionOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            low, high = user_input.get(CONF_MIN_HEIGHT), user_input.get(CONF_MAX_HEIGHT)
            if low is not None and high is not None and low >= high:
                errors["base"] = "min_above_max"
            else:
                return self.async_create_entry(data=user_input)

        schema: dict[Any, Any] = {
            vol.Optional(CONF_MIN_HEIGHT): _height_selector(),
            vol.Optional(CONF_MAX_HEIGHT): _height_selector(),
            vol.Required(CONF_IDLE_TIMEOUT, default=DEFAULT_IDLE_TIMEOUT): NumberSelector(
                NumberSelectorConfig(
                    min=5,
                    max=3600,
                    step=1,
                    unit_of_measurement=UnitOfTime.SECONDS,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(CONF_CONNECTION_MODE, default=MODE_ON_DEMAND): SelectSelector(
                SelectSelectorConfig(
                    options=CONNECTION_MODES,
                    translation_key=CONF_CONNECTION_MODE,
                    mode=SelectSelectorMode.LIST,
                )
            ),
            vol.Required(
                CONF_KEEP_AWAKE_INTERVAL, default=DEFAULT_KEEP_AWAKE_INTERVAL
            ): NumberSelector(
                NumberSelectorConfig(
                    min=10,
                    max=55,
                    step=1,
                    unit_of_measurement=UnitOfTime.MINUTES,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(CONF_EXPOSE_COVER, default=False): BooleanSelector(),
        }
        for i in range(1, PRESET_COUNT + 1):
            schema[vol.Optional(CONF_PRESET_NAME.format(i))] = TextSelector()
            schema[vol.Optional(CONF_PRESET_HEIGHT.format(i))] = _height_selector()

        current = dict(self.config_entry.options)
        if CONF_CONNECTION_MODE not in current and current.pop(CONF_ALWAYS_CONNECTED, False):
            current[CONF_CONNECTION_MODE] = MODE_ALWAYS  # option saved before 0.1.5
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), user_input or current
            ),
            errors=errors,
            description_placeholders={"limits": self._limits()},
        )

    def _limits(self) -> str:
        coordinator = getattr(self.config_entry, "runtime_data", None)
        config = coordinator.desk.config if coordinator else None
        if config is None:
            return "unknown (not read from the desk yet)"
        return f"{config.min_mm / 10:.1f} - {config.max_mm / 10:.1f} cm"
