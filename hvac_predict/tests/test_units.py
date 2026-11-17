import numpy as np
import pytest

from hvac_predict.config import Units
from hvac_predict.errors import DataError
from hvac_predict.limits import Limits, believable
from hvac_predict.units import (
    convert_precipitation,
    convert_temperature,
    convert_temperature_difference,
    convert_wind_speed,
)


def test_temperature_converts_both_ways():
    assert convert_temperature(np.array([32.0, 212.0]), "°F", "°C") == pytest.approx([0.0, 100.0])
    assert convert_temperature(np.array([0.0, 100.0]), "°C", "°F") == pytest.approx([32.0, 212.0])
    assert convert_temperature(np.array([21.0]), "°C", "°C") == pytest.approx([21.0])
    assert convert_temperature(69.8, "°F", "°C") == pytest.approx(21.0)


@pytest.mark.parametrize(
    ("source", "target", "expected"),
    [("m/s", "km/h", 36.0), ("mph", "km/h", 16.09), ("kn", "m/s", 5.144), ("ft/s", "mph", 6.818)],
)
def test_wind_speed_converts_between_units(source, target, expected):
    assert convert_wind_speed(np.array([10.0]), source, target) == pytest.approx(
        [expected], rel=1e-3
    )


def test_precipitation_converts_inches_to_millimetres():
    assert convert_precipitation(np.array([0.5]), "in", "mm") == pytest.approx([12.7])


@pytest.mark.parametrize(
    ("convert", "unit"),
    [(convert_temperature, "K"), (convert_wind_speed, "Beaufort"), (convert_precipitation, "cm")],
)
def test_an_unsupported_unit_is_refused(convert, unit):
    with pytest.raises(DataError, match=f"cannot convert a .* given in '{unit}'"):
        convert(np.array([1.0]), unit, unit)


def test_readings_no_sensor_could_mean_become_gaps():
    readings = np.array([-127.0, -20.0, 21.0, 50.0, 85.0, np.nan, np.inf])

    assert believable(readings, (-20.0, 50.0)) == pytest.approx(
        [np.nan, -20.0, 21.0, 50.0, np.nan, np.nan, np.nan], nan_ok=True
    )


def test_the_believable_ranges_follow_the_units_of_the_history():
    metric = Limits.in_units(Units(temperature="°C", wind_speed="m/s", precipitation="mm"))
    imperial = Limits.in_units(Units(temperature="°F", wind_speed="mph", precipitation="in"))

    assert metric.indoor == (-20.0, 50.0)
    assert metric.outdoor == (-70.0, 60.0)
    assert metric.wind == (0.0, 100.0)
    assert metric.rain == (0.0, 500.0)
    assert imperial.indoor == pytest.approx((-4.0, 122.0))
    assert imperial.outdoor == pytest.approx((-94.0, 140.0))
    assert imperial.wind == pytest.approx((0.0, 223.7), rel=1e-3)
    assert imperial.rain == pytest.approx((0.0, 19.69), rel=1e-3)


def test_a_difference_between_temperatures_is_scaled_and_not_shifted():
    assert convert_temperature_difference(3.0, "°C", "°F") == pytest.approx(5.4)
    assert convert_temperature_difference(5.4, "°F", "°C") == pytest.approx(3.0)
    assert convert_temperature_difference(3.0, "°F", "°F") == 3.0
    with pytest.raises(DataError, match="cannot convert a temperature given in 'K'"):
        convert_temperature_difference(3.0, "K", "°C")
