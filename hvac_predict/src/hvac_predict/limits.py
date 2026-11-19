"""Ranges outside which a reading is a fault and not weather.

A sensor that glitches reports something absurd: a DS18B20 that loses power
says 85 °C. Acting on one such reading would move a real thermostat's
setpoints, so anything outside these ranges is treated as a missing reading
instead.
"""

import math
from dataclasses import dataclass

import numpy as np

from .config import Units
from .units import convert_precipitation, convert_temperature, convert_wind_speed

_INDOOR_CELSIUS = (-20.0, 50.0)
_OUTDOOR_CELSIUS = (-70.0, 60.0)
_WIND_METRES_PER_SECOND = (0.0, 100.0)
_RAIN_MILLIMETRES_PER_HOUR = (0.0, 500.0)
NOT_NEGATIVE = (0.0, np.inf)
SHARE = (0.0, 1.0)

Range = tuple[float, float]


@dataclass(frozen=True)
class Limits:
    """The believable range of each kind of reading, in the units of the history."""

    indoor: Range
    outdoor: Range
    wind: Range
    rain: Range

    @classmethod
    def in_units(cls, units: Units) -> "Limits":
        def temperatures(celsius: Range) -> Range:
            low, high = (convert_temperature(bound, "°C", units.temperature) for bound in celsius)
            return low, high

        wind = convert_wind_speed(np.array(_WIND_METRES_PER_SECOND), "m/s", units.wind_speed)
        rain = convert_precipitation(
            np.array(_RAIN_MILLIMETRES_PER_HOUR), "mm", units.precipitation
        )
        return cls(
            indoor=temperatures(_INDOOR_CELSIUS),
            outdoor=temperatures(_OUTDOOR_CELSIUS),
            wind=(float(wind[0]), float(wind[1])),
            rain=(float(rain[0]), float(rain[1])),
        )


def finite(value: object) -> float | None:
    """Return a value a server sent as a float, or None when it is not a finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:  # JSON can spell an integer too large for a float
        return None
    return number if math.isfinite(number) else None


def believable(values: np.ndarray, within: Range) -> np.ndarray:
    """Return ``values`` with every reading outside ``within`` turned into a gap."""
    low, high = within
    return np.where((values >= low) & (values <= high), values, np.nan)
