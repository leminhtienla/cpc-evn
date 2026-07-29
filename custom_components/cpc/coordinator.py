"""Data coordinator cho CPC."""

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CPCApi, CPCApiError, CPCAuthError
from .const import SCAN_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class CPCDataUpdateCoordinator(DataUpdateCoordinator):
    """Coordinator gọi CPCApi.async_fetch_all() theo chu kỳ."""

    def __init__(self, hass: HomeAssistant, api: CPCApi, customer_code: str):
        super().__init__(
            hass,
            _LOGGER,
            name=f"cpc_{customer_code}",
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.api = api
        self.customer_code = customer_code

    async def _async_update_data(self):
        try:
            return await self.api.async_fetch_all()
        except CPCAuthError as err:
            raise UpdateFailed(f"Lỗi đăng nhập CPC: {err}") from err
        except CPCApiError as err:
            raise UpdateFailed(f"Lỗi gọi API CPC: {err}") from err
