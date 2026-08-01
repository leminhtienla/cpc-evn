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
from .const import CONF_CUSTOMER_CODE, CONF_PASSWORD, CONF_USERNAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): selector.TextSelector(),
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_CUSTOMER_CODE): selector.TextSelector(),
    }
)


class CPCConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow cho CPC."""

    VERSION = 1

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
                api = CPCApi(session, user_input[CONF_USERNAME], user_input[CONF_PASSWORD], customer_code)
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
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )
