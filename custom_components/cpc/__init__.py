"""CPC (Điện lực Miền Trung) integration - cổng cskh.cpc.vn.

Component riêng biệt, xây từ đầu, không dùng chung code với các
integration EVN vùng khác.
"""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CPCApi
from .const import CONF_CUSTOMER_CODE, CONF_PASSWORD, CONF_TARIFF, CONF_USERNAME, DOMAIN, TARIFF_DEFAULT
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
        entry.data.get(CONF_TARIFF, TARIFF_DEFAULT),
    )
    coordinator = CPCDataUpdateCoordinator(hass, api, entry.data[CONF_CUSTOMER_CODE])
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Đăng ký update listener CHUẨN của HA: mỗi khi entry.data/options
    # đổi (ví dụ đổi biểu giá qua Options flow), HA tự gọi lại hàm này
    # -> tự reload integration, không cần tự gọi async_reload thủ công
    # trong config_flow.py (dễ bị race, không chắc chạy đúng lúc).
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Tự reload integration khi entry bị cập nhật (ví dụ đổi biểu giá)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
