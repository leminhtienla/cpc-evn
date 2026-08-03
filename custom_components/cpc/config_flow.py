"""Config flow cho CPC (Điện lực Miền Trung) - cổng cskh.cpc.vn."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CPCApi, CPCAuthError, CPCApiError
from .const import (
    CONF_CUSTOMER_CODE,
    CONF_PASSWORD,
    CONF_TARIFF,
    CONF_USERNAME,
    DOMAIN,
    TARIFF_DEFAULT,
    TARIFF_KINH_DOANH_1_GIA,
    TARIFF_KINH_DOANH_3_GIA,
    TARIFF_SINH_HOAT,
)

_LOGGER = logging.getLogger(__name__)

TARIFF_OPTIONS_UI = [
    {"value": TARIFF_SINH_HOAT, "label": "Sinh hoạt (bậc thang) - hộ gia đình"},
    {"value": TARIFF_KINH_DOANH_1_GIA, "label": "Kinh doanh dịch vụ - 1 giá"},
    {"value": TARIFF_KINH_DOANH_3_GIA, "label": "Kinh doanh dịch vụ - 3 giá (Cao điểm/Bình thường/Thấp điểm)"},
]

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): selector.TextSelector(),
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_CUSTOMER_CODE): selector.TextSelector(),
        vol.Required(CONF_TARIFF, default=TARIFF_DEFAULT): selector.SelectSelector(
            selector.SelectSelectorConfig(options=TARIFF_OPTIONS_UI, mode=selector.SelectSelectorMode.DROPDOWN)
        ),
    }
)


class CPCConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow cho CPC."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "CPCOptionsFlow":
        return CPCOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            customer_code = user_input[CONF_CUSTOMER_CODE].strip().upper()

            if not customer_code.startswith("P") or len(customer_code) < 11:
                errors[CONF_CUSTOMER_CODE] = "invalid_format"
            else:
                # Kiểm tra trùng TRƯỚC khi gọi API - tránh AbortFlow bị
                # nuốt bởi except Exception phía dưới.
                await self.async_set_unique_id(customer_code)
                self._abort_if_unique_id_configured()

                session = async_get_clientsession(self.hass)
                tariff = user_input.get(CONF_TARIFF, TARIFF_DEFAULT)
                api = CPCApi(session, user_input[CONF_USERNAME], user_input[CONF_PASSWORD], customer_code, tariff)
                try:
                    await api.login()
                    await api.get_customer_info()
                except CPCAuthError as err:
                    _LOGGER.debug("Auth error: %s", err)
                    errors["base"] = "invalid_auth"
                except (CPCApiError, aiohttp.ClientError) as err:
                    _LOGGER.debug("API error: %s", err)
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(
                        title=customer_code,
                        data={
                            CONF_USERNAME: user_input[CONF_USERNAME],
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                            CONF_CUSTOMER_CODE: customer_code,
                            CONF_TARIFF: tariff,
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )


class CPCOptionsFlow(config_entries.OptionsFlow):
    """Options flow - cho phép đổi biểu giá SAU KHI đã add integration,
    không cần xoá/tạo lại entry. Truy cập qua nút "Configure"/"Cấu hình"
    trên entry trong Settings > Devices & Services.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            new_tariff = user_input[CONF_TARIFF]
            # Lưu vào entry.data (không dùng entry.options) để __init__.py
            # chỉ cần đọc 1 chỗ duy nhất, đơn giản hơn cho code còn lại.
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_TARIFF: new_tariff},
            )
            # Reload để CPCApi trong hass.data dùng ngay biểu giá mới.
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self.config_entry.entry_id)
            )
            return self.async_create_entry(title="", data={})

        current_tariff = self.config_entry.data.get(CONF_TARIFF, TARIFF_DEFAULT)
        schema = vol.Schema(
            {
                vol.Required(CONF_TARIFF, default=current_tariff): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=TARIFF_OPTIONS_UI, mode=selector.SelectSelectorMode.DROPDOWN)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
