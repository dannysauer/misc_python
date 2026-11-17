"""Convert between the units Home Assistant reports and the units of the history."""

import numpy as np

from .errors import DataError

TEMPERATURE_UNITS = ("°C", "°F")
_METRES_PER_SECOND = {"m/s": 1.0, "km/h": 1 / 3.6, "mph": 0.44704, "kn": 0.514444, "ft/s": 0.3048}
_MILLIMETRES = {"mm": 1.0, "in": 25.4}
WIND_SPEED_UNITS = tuple(_METRES_PER_SECOND)
PRECIPITATION_UNITS = tuple(_MILLIMETRES)


def convert_temperature[Values: (float, np.ndarray)](
    values: Values, source: str, target: str
) -> Values:
    """Convert temperatures between degrees Celsius and Fahrenheit."""
    for unit in (source, target):
        _require_known(unit, TEMPERATURE_UNITS, "temperature")
    if source == target:
        return values
    return (values - 32.0) / 1.8 if source == "°F" else values * 1.8 + 32.0


def convert_temperature_difference(degrees: float, source: str, target: str) -> float:
    """Convert a difference between two temperatures, which has a scale and no offset."""
    for unit in (source, target):
        _require_known(unit, TEMPERATURE_UNITS, "temperature")
    if source == target:
        return degrees
    return degrees * 1.8 if target == "°F" else degrees / 1.8


def convert_wind_speed(values: np.ndarray, source: str, target: str) -> np.ndarray:
    """Convert wind speeds between any two supported units."""
    return values * _ratio(_METRES_PER_SECOND, source, target, "wind speed")


def convert_precipitation(values: np.ndarray, source: str, target: str) -> np.ndarray:
    """Convert precipitation depths between millimetres and inches."""
    return values * _ratio(_MILLIMETRES, source, target, "precipitation")


def _ratio(table: dict[str, float], source: str, target: str, quantity: str) -> float:
    for unit in (source, target):
        _require_known(unit, tuple(table), quantity)
    return table[source] / table[target]


def _require_known(unit: str, known: tuple[str, ...], quantity: str) -> None:
    if unit not in known:
        raise DataError(
            f"cannot convert a {quantity} given in {unit!r}; this tool handles {', '.join(known)}"
        )
