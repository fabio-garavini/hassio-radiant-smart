"""Diagnostics tests against a loaded config entry."""

from conftest import BOILER_PRODUCT_ID
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

from custom_components.radiant_smart.const import (
    CONF_HOME,
    CONF_TOKEN_DATA,
)
from custom_components.radiant_smart.diagnostics import (
    TO_REDACT_DEVICE,
    async_get_config_entry_diagnostics,
)


async def build_diagnostics(hass, setup_entry) -> dict:
    """Return diagnostics for a fully loaded entry."""
    return await async_get_config_entry_diagnostics(hass, setup_entry)


async def test_credentials_are_redacted(hass, setup_entry) -> None:
    diagnostics = await build_diagnostics(hass, setup_entry)
    entry_data = diagnostics["entry"]["data"]
    assert entry_data[CONF_EMAIL] == "**REDACTED**"
    assert entry_data[CONF_PASSWORD] == "**REDACTED**"
    assert entry_data[CONF_TOKEN_DATA] == "**REDACTED**"
    assert entry_data[CONF_HOME] == "**REDACTED**"


async def test_device_identifiers_are_redacted(hass, setup_entry) -> None:
    diagnostics = await build_diagnostics(hass, setup_entry)
    for device in diagnostics["devices"]:
        for key in TO_REDACT_DEVICE:
            assert device[key] == "**REDACTED**", key


async def test_data_point_values_are_included(hass, setup_entry) -> None:
    """Point values are useful for debugging and stay in the report."""
    diagnostics = await build_diagnostics(hass, setup_entry)
    boiler = next(d for d in diagnostics["devices"] if d["model"] == "TH311")
    assert boiler["data_points"]["PARAM_ID_BOILER_OUTDOOR_TEMP"]["value"] == "70"
    assert boiler["data_points"]["SYS_WORK_MODE"]["value"] == "10"


async def test_mqtt_state_is_included(hass, setup_entry) -> None:
    diagnostics = await build_diagnostics(hass, setup_entry)
    assert diagnostics["mqtt"]["connected"] is True
    assert diagnostics["mqtt"]["subscriptions"] == sorted(
        {
            f"{BOILER_PRODUCT_ID}/12b602c3aa7c329c5021239f76b69585/business",
            f"{BOILER_PRODUCT_ID}/12b602c3aa7c329c5021239f76b69585/upload/point/data",
            "044e784511e14d34953c5f2221129a4e/12b602c3aa7c329c5021239f76b69585/business",
            "044e784511e14d34953c5f2221129a4e/12b602c3aa7c329c5021239f76b69585/upload/point/data",
        }
    )
