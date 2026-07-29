"""CPC (Điện lực Miền Trung) integration - cổng cskh.cpc.vn.

Component riêng biệt, xây từ đầu, không dùng chung code với các
integration EVN vùng khác.
"""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CPCApi
from .const import CONF_CUSTOMER_CODE, CONF_PASSWORD, CONF_USERNAME, DOMAIN
from .coordinator import CPCDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    api = CPCApi(
        session,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_CUSTOMER_CODE],
    )
    coordinator = CPCDataUpdateCoordinator(hass, api, entry.data[CONF_CUSTOMER_CODE])
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
