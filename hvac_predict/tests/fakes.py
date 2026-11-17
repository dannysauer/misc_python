"""Stand-ins for VictoriaMetrics and Home Assistant, and a configuration to test with."""

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from house import END

from hvac_predict.config import Config, load_config
from hvac_predict.grid import TimeGrid
from hvac_predict.homeassistant import EntityState

NOW = END  # 2026-05-28 20:30 UTC, after dark at the test location
NOON = NOW - 8.5 * 3600
WEATHER_UNITS = {"temperature_unit": "°C", "wind_speed_unit": "km/h", "precipitation_unit": "mm"}
ALL_DRIVERS = {name: name for name in ("solar", "wind", "rain", "heating", "cooling")}
# The two pairs for the Home setting of the test configuration: a target of 21, 2 degrees wide.
HEAT_PAIR, COOL_PAIR = (21.0, 23.0), (19.0, 21.0)

# Every series is addressed by using its name as the query.
_SETTINGS = {
    "victoriametrics": {"url": "http://vm.test"},
    "homeassistant": {
        "url": "http://ha.test",
        "climate_entity": "climate.house",
        "weather_entity": "weather.home",
    },
    "location": {"latitude": 45.0, "longitude": 0.0},
    "units": {"temperature": "°C"},
    "history": {"indoor_temp": "indoor", "outdoor_temp": "outdoor"},
    "comfort": {"differential": 2.0, "targets": {"Home": 21.0, "Sleep": 18.0}},
}


def write_config(directory: Path, **sections: dict[str, Any]) -> Config:
    """Load the test configuration with ``sections`` merged in.

    A setting given as None is left out of the file altogether.
    """
    return load_config(write_settings(directory, **sections))


def write_settings(directory: Path, **sections: dict[str, Any]) -> Path:
    settings = {name: dict(table) for name, table in _SETTINGS.items()}
    for name, table in sections.items():
        settings.setdefault(name, {}).update(table)
    lines = [line for name, table in settings.items() for line in _table(name, table)]
    path = directory / "hvac-predict.toml"
    path.write_text("\n".join(lines) + "\n")
    return path


def _table(name: str, table: dict[str, Any]) -> list[str]:
    """Spell a table the way TOML does, with any table inside it after its own settings."""
    inner = {key: value for key, value in table.items() if isinstance(value, dict)}
    plain = {key: value for key, value in table.items() if key not in inner and value is not None}
    lines = [f"[{name}]", *(f"{json.dumps(key)} = {_toml(value)}" for key, value in plain.items())]
    for key, value in inner.items():
        lines += _table(f"{name}.{json.dumps(key)}", value)
    return lines


def _toml(value: Any) -> str:
    """Spell a setting the way TOML does, which is JSON's way except for nan and inf."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def hourly_forecast(hours: int, *, start: float = NOW, **values: Any) -> list[dict]:
    """Return an hourly forecast beginning at the top of the hour that ``start`` is in."""
    first = start - start % 3600
    return [
        {"datetime": datetime.fromtimestamp(first + 3600 * hour, UTC).isoformat(), **values}
        for hour in range(hours)
    ]


class FakeMetrics:
    """Serves series whose last sample is at ``end``, one sample per ``step`` seconds."""

    def __init__(self, series: dict[str, np.ndarray], *, end: float = NOW, step: int = 300) -> None:
        self._series = series
        self._end = end
        self._step = step

    def query_range(self, query: str, grid: TimeGrid) -> np.ndarray:
        stored = self._series[query]
        behind = (self._end - grid.times) / self._step
        index = len(stored) - 1 - np.round(behind).astype(int)
        known = (index >= 0) & (index < len(stored)) & (np.abs(behind - np.round(behind)) < 1e-6)
        values = np.full(grid.size, np.nan)
        values[known] = stored[index[known]]
        return values


SCHEDULED = object()  # stands for the preset a thermostat reports with no hold in force
HOLD = "temp"  # the preset reported while a hold on the setpoints alone is in force


def thermostat_attributes(
    *,
    comfort: Any = "Home",
    preset: Any = SCHEDULED,
    setpoints: tuple[Any, Any] = (None, None),
    reads: Any = 21.0,
) -> dict[str, Any]:
    """Return what the code takes an ecobee in auto mode to report.

    This is the one place the tests name the attributes a thermostat reports.
    Every stand-in keeps what its thermostat reports as these keywords and
    builds the attributes from them. It follows the source of Home Assistant's
    ecobee integration and has not been checked on a device.
    """
    if preset is SCHEDULED:
        preset = scheduled_preset(comfort) if isinstance(comfort, str) else None
    return {
        "current_temperature": reads,
        "target_temp_low": setpoints[0],
        "target_temp_high": setpoints[1],
        "preset_mode": preset,
        # The comfort setting the schedule has reached, hold or no hold.
        "climate_mode": comfort,
    }


def scheduled_preset(comfort: str) -> str:
    """Return the preset reported with no hold in force: the scheduled comfort setting.

    The integration puts the names of the settings a thermostat ships with in lower case.
    """
    return comfort.lower()


def holding(reports: dict[str, Any], low: float, high: float) -> dict[str, Any]:
    """Return what a thermostat reports once climate.set_temperature has held its setpoints."""
    return reports | {"setpoints": (low, high), "preset": HOLD}


class FakeHome:
    """An ecobee-like thermostat in auto mode, a weather entity and an enable switch."""

    def __init__(
        self,
        forecast: list[dict],
        *,
        mode: str = "heat_cool",
        unit: str = "°C",
        switch: str = "on",
        **reports: Any,
    ) -> None:
        self._forecast = forecast
        self._unit = unit
        self._mode = mode
        self._switch = switch
        self._reports = reports
        self.setpoint_changes: list[tuple[str, float, float]] = []

    def state(self, entity_id: str) -> EntityState:
        states = {
            "climate": (self._mode, thermostat_attributes(**self._reports)),
            "weather": ("sunny", WEATHER_UNITS),
            "input_boolean": (self._switch, {}),
        }
        state, attributes = states[entity_id.split(".")[0]]
        return EntityState(entity_id, state, dict(attributes))

    def temperature_unit(self) -> str:
        return self._unit

    def hourly_forecast(self, entity_id: str) -> list[dict]:
        return self._forecast

    def set_temperature_range(self, entity_id: str, low: float, high: float) -> None:
        self.setpoint_changes.append((entity_id, low, high))
        self._reports = holding(self._reports, low, high)
