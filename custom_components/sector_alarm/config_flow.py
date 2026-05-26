"""Config flow for Sector Alarm."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SectorAlarmClient, SectorApiError, SectorAuthError
from .const import (
    CONF_PANEL_ID,
    CONF_PIN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_PANEL_ID): str,
        vol.Optional(CONF_PIN, default=""): str,
    }
)


async def _validate(
    hass, email: str, password: str, panel_id: str, pin: str
) -> str | None:
    """Try a login + GetPanel. Return None on success, error key on failure."""
    session = async_get_clientsession(hass)
    client = SectorAlarmClient(session, email, password, panel_id, pin)
    try:
        await client.login()
        await client.get_panel()
    except SectorAuthError:
        return "invalid_auth"
    except SectorApiError as err:
        _LOGGER.warning("Sector validation failed: %s", err)
        return "cannot_connect"
    return None


class SectorAlarmConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup and reauth."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            panel_id = user_input[CONF_PANEL_ID].strip()
            await self.async_set_unique_id(panel_id)
            self._abort_if_unique_id_configured()

            err = await _validate(
                self.hass,
                user_input[CONF_EMAIL],
                user_input[CONF_PASSWORD],
                panel_id,
                user_input.get(CONF_PIN, ""),
            )
            if err:
                errors["base"] = err
            else:
                return self.async_create_entry(
                    title=f"Sector Alarm ({panel_id})",
                    data={
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_PANEL_ID: panel_id,
                        CONF_PIN: user_input.get(CONF_PIN, ""),
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        assert self._reauth_entry is not None
        entry = self._reauth_entry

        if user_input is not None:
            err = await _validate(
                self.hass,
                entry.data[CONF_EMAIL],
                user_input[CONF_PASSWORD],
                entry.data[CONF_PANEL_ID],
                user_input.get(CONF_PIN, entry.data.get(CONF_PIN, "")),
            )
            if err:
                errors["base"] = err
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_PIN: user_input.get(
                            CONF_PIN, entry.data.get(CONF_PIN, "")
                        ),
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): str,
                    vol.Optional(
                        CONF_PIN, default=entry.data.get(CONF_PIN, "")
                    ): str,
                }
            ),
            errors=errors,
            description_placeholders={"email": entry.data[CONF_EMAIL]},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:
        return SectorAlarmOptionsFlow()


class SectorAlarmOptionsFlow(OptionsFlow):
    """Allow editing the polling interval after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, int(DEFAULT_SCAN_INTERVAL.total_seconds())
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SCAN_INTERVAL_SECONDS,
                            max=MAX_SCAN_INTERVAL_SECONDS,
                        ),
                    )
                }
            ),
        )
