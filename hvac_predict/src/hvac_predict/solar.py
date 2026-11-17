"""Estimate sunlight from the sun's position, so no irradiance forecast is needed.

The model takes its sunlight from one of two sources, and uses the same one to
train and to predict. With a light sensor in the history it is the sensor's
reading, and a forecast is turned into that reading by forecast_sunlight.
Without a sensor it is the irradiance of a cloudless sky, in both places.
"""

import numpy as np

from .errors import DataError

_DAYLIGHT_IRRADIANCE = 100.0
_MIN_DAYLIGHT_READINGS = 50
_CLEAR_DAY_QUANTILE = 0.9

_UNIX_EPOCH_JULIAN_DAY = 2440587.5
_J2000_JULIAN_DAY = 2451545.0


def sun_elevation(times: np.ndarray, latitude: float, longitude: float) -> np.ndarray:
    """Return the sun's elevation in degrees at each Unix time.

    This follows the NOAA solar calculator. It ignores atmospheric refraction,
    which only matters within a degree of the horizon.
    """
    times = np.asarray(times, dtype=float)
    century = (times / 86400.0 + _UNIX_EPOCH_JULIAN_DAY - _J2000_JULIAN_DAY) / 36525.0
    mean_longitude = np.radians((280.46646 + century * (36000.76983 + century * 0.0003032)) % 360)
    mean_anomaly = np.radians(357.52911 + century * (35999.05029 - 0.0001537 * century))
    eccentricity = 0.016708634 - century * (0.000042037 + 0.0000001267 * century)
    centre = (
        np.sin(mean_anomaly) * (1.914602 - century * (0.004817 + 0.000014 * century))
        + np.sin(2 * mean_anomaly) * (0.019993 - 0.000101 * century)
        + np.sin(3 * mean_anomaly) * 0.000289
    )
    node = np.radians(125.04 - 1934.136 * century)
    apparent_longitude = mean_longitude + np.radians(centre - 0.00569 - 0.00478 * np.sin(node))
    mean_obliquity = (
        23
        + (26 + (21.448 - century * (46.815 + century * (0.00059 - century * 0.001813))) / 60) / 60
    )
    obliquity = np.radians(mean_obliquity + 0.00256 * np.cos(node))
    declination = np.arcsin(np.sin(obliquity) * np.sin(apparent_longitude))

    half_tilt = np.tan(obliquity / 2) ** 2
    equation_of_time = 4 * np.degrees(
        half_tilt * np.sin(2 * mean_longitude)
        - 2 * eccentricity * np.sin(mean_anomaly)
        + 4 * eccentricity * half_tilt * np.sin(mean_anomaly) * np.cos(2 * mean_longitude)
        - 0.5 * half_tilt**2 * np.sin(4 * mean_longitude)
        - 1.25 * eccentricity**2 * np.sin(2 * mean_anomaly)
    )
    solar_minutes = ((times % 86400.0) / 60.0 + equation_of_time + 4 * longitude) % 1440
    hour_angle = np.radians(solar_minutes / 4 - 180)

    lat = np.radians(latitude)
    sine = np.sin(lat) * np.sin(declination) + np.cos(lat) * np.cos(declination) * np.cos(
        hour_angle
    )
    return np.degrees(np.arcsin(np.clip(sine, -1.0, 1.0)))


def clear_sky_irradiance(times: np.ndarray, latitude: float, longitude: float) -> np.ndarray:
    """Return cloudless irradiance on level ground in W/m² (Haurwitz model)."""
    sine = np.sin(np.radians(sun_elevation(times, latitude, longitude)))
    up = sine > 0
    irradiance = np.zeros_like(sine)
    irradiance[up] = 1098.0 * sine[up] * np.exp(-0.059 / sine[up])
    return irradiance


def cloud_attenuation(cloud_fraction: np.ndarray) -> np.ndarray:
    """Return the share of clear-sky irradiance that gets through (Kasten and Czeplak)."""
    return 1.0 - 0.75 * np.clip(cloud_fraction, 0.0, 1.0) ** 3.4


def light_per_irradiance(light: np.ndarray, clear_sky: np.ndarray) -> float:
    """Return what a light sensor reads per W/m² of irradiance under a clear sky.

    Clouds only ever lower the ratio, so a high quantile of it across all the
    daylight readings is close to its clear-day value.
    """
    daylight = (clear_sky > _DAYLIGHT_IRRADIANCE) & np.isfinite(light)
    if daylight.sum() < _MIN_DAYLIGHT_READINGS:
        raise DataError("history.solar has too few daytime readings to calibrate against the sun")
    scale = float(np.quantile(light[daylight] / clear_sky[daylight], _CLEAR_DAY_QUANTILE))
    if scale <= 0:
        raise DataError("history.solar never rises above zero in daylight; check the query")
    return scale


def forecast_sunlight(
    clear_sky: np.ndarray, cloud_fraction: np.ndarray, light_scale: float | None
) -> np.ndarray:
    """Return the sunlight the model should expect, in the units it was trained on.

    light_scale is the result of light_per_irradiance, or None for a model
    trained without a light sensor. Such a model learned from clear-sky
    irradiance alone, so it is given the same again and the clouds are ignored.
    """
    if light_scale is None:
        return clear_sky
    return light_scale * clear_sky * cloud_attenuation(cloud_fraction)
