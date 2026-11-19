"""Talk to Home Assistant over its REST API."""

from dataclasses import dataclass
from typing import Any

import httpx

from .errors import ServiceError
from .http import JsonApi

_TIMEOUT_SECONDS = 30.0
_MAX_UNIT_LENGTH = 8


@dataclass(frozen=True)
class EntityState:
    entity_id: str
    state: str
    attributes: dict[str, Any]


class HomeAssistant:
    """The handful of REST calls this tool makes."""

    def __init__(self, api: JsonApi) -> None:
        self._api = api

    @classmethod
    def connect(
        cls, url: str, token: str, *, transport: httpx.BaseTransport | None = None
    ) -> "HomeAssistant":
        api = JsonApi("Home Assistant", url, token, timeout=_TIMEOUT_SECONDS, transport=transport)
        return cls(api)

    def state(self, entity_id: str) -> EntityState:
        body = self._api.get(f"/api/states/{entity_id}")
        try:
            state, attributes = body["state"], body["attributes"]
            # The state ends up on the terminal and in cron mail, so it has to be plain text.
            if not (
                isinstance(state, str) and state.isprintable() and isinstance(attributes, dict)
            ):
                raise TypeError(type(state).__name__)
            return EntityState(entity_id, state, attributes)
        except (KeyError, TypeError) as error:
            raise _unreadable(f"a state for {entity_id}") from error

    def temperature_unit(self) -> str:
        """Return the unit Home Assistant shows temperatures in, such as a thermostat's."""
        body = self._api.get("/api/config")
        try:
            unit = body["unit_system"]["temperature"]
        except (KeyError, TypeError) as error:
            raise _unreadable("a configuration") from error
        # The unit is quoted in error messages, so it has to be a few plain characters.
        if not (isinstance(unit, str) and unit.isprintable() and len(unit) <= _MAX_UNIT_LENGTH):
            raise _unreadable("a configuration")
        return unit

    def hourly_forecast(self, entity_id: str) -> list[dict[str, Any]]:
        """Return the weather entity's hourly forecast, in the units the entity reports."""
        body = self._api.post(
            "/api/services/weather/get_forecasts",
            {"entity_id": entity_id, "type": "hourly"},
            params={"return_response": ""},
        )
        try:
            forecast = body["service_response"][entity_id]["forecast"]
        except (KeyError, TypeError) as error:
            raise ServiceError(
                f"Home Assistant returned no hourly forecast for {entity_id}"
            ) from error
        if not isinstance(forecast, list) or not all(isinstance(entry, dict) for entry in forecast):
            raise _unreadable(f"an hourly forecast for {entity_id}")
        return forecast

    def set_temperature_range(self, entity_id: str, low: float, high: float) -> None:
        """Set the heating and cooling setpoints of a thermostat in heat_cool mode.

        The temperatures are in the unit Home Assistant shows.
        """
        self._api.post(
            "/api/services/climate/set_temperature",
            {"entity_id": entity_id, "target_temp_low": low, "target_temp_high": high},
        )


def _unreadable(what: str) -> ServiceError:
    return ServiceError(f"Home Assistant returned {what} that this tool cannot read")
