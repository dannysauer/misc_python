"""Turn a Home Assistant hourly forecast into the drivers the thermal model needs."""

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np

from .config import Location, Units
from .errors import DataError
from .limits import Limits, believable, finite
from .solar import clear_sky_irradiance, forecast_sunlight
from .thermal import Drivers
from .units import (
    WIND_SPEED_UNITS,
    convert_precipitation,
    convert_temperature,
    convert_wind_speed,
)

# A forecast is for a grid cell and the sensor is in the garden. The gap between
# them right now is carried into the forecast, shrinking by this time constant.
_NOWCAST_FADE_HOURS = 3.0
# Rough cloud cover for Home Assistant's weather conditions, used when the
# forecast has no cloud_coverage of its own.
_CLOUD_BY_CONDITION = {
    "sunny": 0.0,
    "clear-night": 0.0,
    "windy": 0.2,
    "partlycloudy": 0.5,
    "windy-variant": 0.6,
    "cloudy": 0.9,
    "fog": 0.9,
    "hail": 1.0,
    "lightning": 1.0,
    "lightning-rainy": 1.0,
    "pouring": 1.0,
    "rainy": 1.0,
    "snowy": 1.0,
    "snowy-rainy": 1.0,
}
_UNKNOWN_CLOUD = 0.5


@dataclass(frozen=True)
class Forecast:
    """An hourly forecast in the units of the history; a missing value is NaN."""

    times: np.ndarray
    period_hours: np.ndarray
    temperature: np.ndarray
    wind_speed: np.ndarray
    rain_rate: np.ndarray
    cloud_fraction: np.ndarray

    @property
    def end(self) -> float:
        """The last moment the forecast says anything about."""
        return float(self.times[-1] + self.period_hours[-1] * 3600)


@dataclass(frozen=True)
class Conditions:
    """What the sensors read at the moment the prediction starts."""

    time: float
    outdoor: float
    wind: float


def parse_forecast(
    entries: list[dict[str, Any]], reported: dict[str, Any], units: Units
) -> Forecast:
    """Convert forecast entries from the units the weather entity reports.

    A value that is missing, or too extreme to believe, becomes a gap.
    """
    if not entries:
        raise DataError("the weather entity returned an empty hourly forecast")
    try:
        moments = [_timestamp(entry["datetime"]) for entry in entries]
        temperature_unit, wind_unit, rain_unit = (
            reported[f"{quantity}_unit"]
            for quantity in ("temperature", "wind_speed", "precipitation")
        )
    except (KeyError, TypeError, ValueError) as error:
        raise DataError(
            f"the weather entity returned a forecast this tool cannot read: {error!r}"
        ) from error
    times, order = np.unique(moments, return_index=True)
    entries = [entries[index] for index in order]
    period_hours = (
        np.append(np.diff(times), np.diff(times)[-1:] if len(times) > 1 else 3600.0) / 3600
    )

    limits = Limits.in_units(units)
    temperature = believable(
        convert_temperature(_column(entries, "temperature"), temperature_unit, units.temperature),
        limits.outdoor,
    )
    if not np.isfinite(temperature).any():
        raise DataError("the hourly forecast has no usable temperatures")
    # Home Assistant can report wind in Beaufort, which is not a speed. A forecast
    # in a unit like that is treated as having no wind in it.
    wind = np.full(len(entries), np.nan)
    if wind_unit in WIND_SPEED_UNITS:
        wind = convert_wind_speed(_column(entries, "wind_speed"), wind_unit, units.wind_speed)
    rain = convert_precipitation(_column(entries, "precipitation"), rain_unit, units.precipitation)
    cover = _column(entries, "cloud_coverage") / 100
    guess = [_cloud_for(entry.get("condition")) for entry in entries]
    return Forecast(
        times=times,
        period_hours=period_hours,
        temperature=temperature,
        wind_speed=believable(wind, limits.wind),
        rain_rate=believable(rain / period_hours, limits.rain),
        cloud_fraction=np.where(np.isfinite(cover), cover, guess),
    )


def future_drivers(
    forecast: Forecast,
    times: np.ndarray,
    current: Conditions,
    *,
    location: Location,
    light_scale: float | None,
    wind_scale: float,
) -> Drivers:
    """Return the drivers at each future time, with the equipment switched off.

    ``light_scale`` is what the model's light sensor reads per W/m² of clear-sky
    irradiance, or None for a model trained without a light sensor.
    """
    offset = current.outdoor - _interpolate(forecast, forecast.temperature, current.time)[0]
    fade = np.exp(-(times - current.time) / (_NOWCAST_FADE_HOURS * 3600))
    if np.isfinite(forecast.wind_speed).any():
        wind = wind_scale * _interpolate(forecast, forecast.wind_speed, times)
    else:
        wind = np.full(len(times), current.wind)
    # Rain is forecast as an amount for each entry's whole period, so it is held,
    # not interpolated. Before the first entry there is no forecast of it at all.
    period = np.searchsorted(forecast.times, times, side="right") - 1
    rain = np.where(period >= 0, forecast.rain_rate[np.clip(period, 0, None)], 0.0)
    return Drivers(
        outdoor=_interpolate(forecast, forecast.temperature, times) + offset * fade,
        solar=forecast_sunlight(
            clear_sky_irradiance(times, location.latitude, location.longitude),
            _interpolate(forecast, forecast.cloud_fraction, times),
            light_scale,
        ),
        wind=wind,
        rain=np.where(np.isfinite(rain), rain, 0.0),
        heating=np.zeros(len(times)),
        cooling=np.zeros(len(times)),
    )


def _interpolate(forecast: Forecast, values: np.ndarray, times: np.ndarray | float) -> np.ndarray:
    """Interpolate between the forecast entries that have a value, holding the first and last."""
    known = np.isfinite(values)
    return np.interp(np.atleast_1d(times), forecast.times[known], values[known])


def _cloud_for(condition: Any) -> float:
    """Return rough cloud cover for a Home Assistant weather condition."""
    if not isinstance(condition, str):
        return _UNKNOWN_CLOUD
    return _CLOUD_BY_CONDITION.get(condition, _UNKNOWN_CLOUD)


def _column(entries: list[dict[str, Any]], key: str) -> np.ndarray:
    return np.array([_number(entry.get(key)) for entry in entries])


def _number(value: Any) -> float:
    """Return a forecast value as a float, or NaN when it is not a finite number."""
    number = finite(value)
    return math.nan if number is None else number


def _timestamp(text: str) -> float:
    moment = datetime.fromisoformat(text)
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp()
