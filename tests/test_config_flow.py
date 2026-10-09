"""Tests for the config and options flow."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.edifier_m60.const import (
    CONF_CONNECTION_MODE,
    CONF_IDLE_DISCONNECT,
    CONF_POLL_INTERVAL,
    DOMAIN,
    MODE_ON_DEMAND,
)

from .conftest import (
    ADDRESS,
    M60_SERVICE_INFO,
    OTHER_SERVICE_INFO,
    TITLE,
    FakeSpeaker,
    inject,
    make_service_info,
    setup_entry,
)


async def test_advertisement_starts_discovery(hass: HomeAssistant) -> None:
    """The manifest matchers pick up the M60 but not other devices."""
    inject(hass, OTHER_SERVICE_INFO)
    inject(hass, M60_SERVICE_INFO)
    await hass.async_block_till_done(wait_background_tasks=True)

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["unique_id"] == ADDRESS
    assert flows[0]["context"]["title_placeholders"] == {"name": TITLE}


async def test_discovery_by_service_uuid_only(hass: HomeAssistant) -> None:
    """Adverts without manufacturer data still match on the service UUID."""
    inject(hass, make_service_info(manufacturer_data={}))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(hass.config_entries.flow.async_progress_by_handler(DOMAIN)) == 1


async def test_bluetooth_confirm(hass: HomeAssistant) -> None:
    """Confirming a discovered speaker creates the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=M60_SERVICE_INFO
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == TITLE
    assert result["data"] == {CONF_ADDRESS: ADDRESS}
    assert result["result"].unique_id == ADDRESS


async def test_bluetooth_already_configured(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """A configured speaker is not offered again."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=M60_SERVICE_INFO
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_pick_discovered(hass: HomeAssistant) -> None:
    """The user step lists discovered speakers only."""
    inject(hass, M60_SERVICE_INFO)
    inject(hass, OTHER_SERVICE_INFO)
    await hass.async_block_till_done(wait_background_tasks=True)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    options = result["data_schema"].schema[CONF_ADDRESS].config["options"]
    assert [o["value"] for o in options] == [ADDRESS, "manual"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == TITLE
    assert result["data"] == {CONF_ADDRESS: ADDRESS}
    # The discovery flow started by the advert is superseded
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_user_manual_address(hass: HomeAssistant) -> None:
    """A manually entered address is normalised."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: "manual"}
    )
    assert result["step_id"] == "manual"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: " aa:bb:cc:dd:ee:ff "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Edifier M60 (EE:FF)"
    assert result["data"] == {CONF_ADDRESS: ADDRESS}


async def test_user_manual_already_configured(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Entering an existing address aborts."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(
    hass: HomeAssistant, mock_entry: MockConfigEntry, speaker: FakeSpeaker
) -> None:
    """Changing options stores them and reloads the entry in the new mode."""
    await setup_entry(hass, mock_entry)

    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_CONNECTION_MODE: MODE_ON_DEMAND,
            CONF_POLL_INTERVAL: 60,
            CONF_IDLE_DISCONNECT: 5,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_entry.options == {
        CONF_CONNECTION_MODE: MODE_ON_DEMAND,
        CONF_POLL_INTERVAL: 60,
        CONF_IDLE_DISCONNECT: 5,
    }
    assert mock_entry.runtime_data.mode == MODE_ON_DEMAND
