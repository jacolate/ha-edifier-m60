"""Config flow for Edifier M60."""

from __future__ import annotations

from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers import selector
import voluptuous as vol

from . import protocol
from .const import (
    CONF_CONNECTION_MODE,
    CONF_IDLE_DISCONNECT,
    CONF_POLL_INTERVAL,
    DEFAULT_CONNECTION_MODE,
    DEFAULT_IDLE_DISCONNECT,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MANUFACTURER_ID,
    MODE_ON_DEMAND,
    MODE_PERSISTENT,
    SEARCH_SERVICE_UUID,
)

CONF_MANUAL = "manual"


def _is_edifier(info: BluetoothServiceInfoBleak) -> bool:
    return MANUFACTURER_ID in info.manufacturer_data or SEARCH_SERVICE_UUID in [
        u.lower() for u in info.service_uuids
    ]


def _title(info: BluetoothServiceInfoBleak | None, address: str) -> str:
    mac = None
    if info is not None:
        mac = protocol.parse_manufacturer_data(
            info.manufacturer_data.get(MANUFACTURER_ID)
        ).classic_mac
    suffix = (mac or address)[-5:]
    return f"Edifier M60 ({suffix})"


class EdifierM60ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Edifier M60 speakers."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return EdifierM60OptionsFlow()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a speaker found by bluetooth discovery."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery = discovery_info
        self.context["title_placeholders"] = {
            "name": _title(discovery_info, discovery_info.address)
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered speaker."""
        assert self._discovery is not None
        title = _title(self._discovery, self._discovery.address)
        if user_input is not None:
            return self.async_create_entry(
                title=title, data={CONF_ADDRESS: self._discovery.address}
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm", description_placeholders={"name": title}
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick a discovered speaker."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            if address == CONF_MANUAL:
                return await self.async_step_manual()
            return await self._create(address, self._discovered.get(address))

        current = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address not in current and _is_edifier(info):
                self._discovered[info.address] = info

        options = [
            selector.SelectOptionDict(
                value=addr, label=f"{_title(info, addr)} - {addr}"
            )
            for addr, info in self._discovered.items()
        ]
        options.append(
            selector.SelectOptionDict(value=CONF_MANUAL, label="Enter address manually")
        )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user enter a BLE address."""
        if user_input is not None:
            return await self._create(user_input[CONF_ADDRESS].strip().upper(), None)
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): str}),
        )

    async def _create(
        self, address: str, info: BluetoothServiceInfoBleak | None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(address, raise_on_progress=False)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=_title(info, address), data={CONF_ADDRESS: address}
        )


class EdifierM60OptionsFlow(OptionsFlow):
    """Options for the connection strategy."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        opts = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONNECTION_MODE,
                        default=opts.get(CONF_CONNECTION_MODE, DEFAULT_CONNECTION_MODE),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[MODE_PERSISTENT, MODE_ON_DEMAND],
                            translation_key=CONF_CONNECTION_MODE,
                        )
                    ),
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=opts.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=30,
                            max=3600,
                            step=1,
                            unit_of_measurement="s",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_IDLE_DISCONNECT,
                        default=opts.get(CONF_IDLE_DISCONNECT, DEFAULT_IDLE_DISCONNECT),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=2,
                            max=300,
                            step=1,
                            unit_of_measurement="s",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )
