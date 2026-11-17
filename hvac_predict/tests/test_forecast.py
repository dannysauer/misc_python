import numpy as np
import pytest
from fakes import WEATHER_UNITS

from hvac_predict.config import Location, Units
from hvac_predict.errors import DataError
from hvac_predict.forecast import Conditions, future_drivers, parse_forecast
from hvac_predict.solar import clear_sky_irradiance

UNITS = Units(temperature="°C")
PLACE = Location(latitude=45.0, longitude=0.0)
# 2026-06-21 10:00 UTC, mid-morning at the test location.
START = 1_782_036_000.0


def entry(hour: float, **values) -> dict:
    moment = np.datetime64(int(START + 3600 * hour), "s")
    return {"datetime": f"{moment}+00:00", **values}


def drivers_for(
    entries, times, *, now=START, outdoor=10.0, wind=7.0, light_scale=None, wind_scale=1.0
):
    forecast = parse_forecast(entries, WEATHER_UNITS, UNITS)
    current = Conditions(time=now, outdoor=outdoor, wind=wind)
    return future_drivers(
        forecast, times, current, location=PLACE, light_scale=light_scale, wind_scale=wind_scale
    )


def test_parse_converts_from_the_units_the_weather_entity_reports():
    imperial = {"temperature_unit": "°F", "wind_speed_unit": "mph", "precipitation_unit": "in"}
    entries = [entry(0, temperature=50.0, wind_speed=10.0, precipitation=0.1, cloud_coverage=40)]

    forecast = parse_forecast(entries, imperial, UNITS)

    assert forecast.temperature == pytest.approx([10.0])
    assert forecast.wind_speed == pytest.approx([16.09], rel=1e-3)
    assert forecast.rain_rate == pytest.approx([2.54])
    assert forecast.cloud_fraction == pytest.approx([0.4])
    assert forecast.end == START + 3600


def test_parse_orders_entries_and_drops_repeats():
    entries = [entry(2, temperature=3.0), entry(0, temperature=1.0), entry(2, temperature=9.0)]

    forecast = parse_forecast(entries, WEATHER_UNITS, UNITS)

    assert forecast.times.tolist() == [START, START + 7200]
    assert forecast.temperature.tolist() == [1.0, 3.0]


def test_parse_reads_a_naive_time_as_utc():
    forecast = parse_forecast(
        [{"datetime": "2026-06-21T10:00:00", "temperature": 1.0}], WEATHER_UNITS, UNITS
    )

    assert forecast.times.tolist() == [START]


def test_parse_spreads_precipitation_over_the_length_of_each_entry():
    entries = [entry(0, temperature=1.0, precipitation=6.0), entry(3, temperature=1.0)]

    forecast = parse_forecast(entries, WEATHER_UNITS, UNITS)

    assert forecast.rain_rate == pytest.approx([2.0, np.nan], nan_ok=True)
    assert forecast.end == START + 6 * 3600


def test_parse_guesses_cloud_cover_from_the_condition():
    entries = [
        entry(0, temperature=1.0, condition="sunny"),
        entry(1, temperature=1.0, condition="partlycloudy"),
        entry(2, temperature=1.0, condition="rainy", cloud_coverage=80),
        entry(3, temperature=1.0, condition="something-new"),
        entry(4, temperature=1.0),
        entry(5, temperature=1.0, condition=["cloudy"]),
    ]

    forecast = parse_forecast(entries, WEATHER_UNITS, UNITS)

    assert forecast.cloud_fraction.tolist() == [0.0, 0.5, 0.8, 0.5, 0.5, 0.5]


def test_parse_turns_values_no_forecast_could_mean_into_gaps():
    entries = [
        entry(0, temperature=1e6, wind_speed=-400.0, precipitation=float("inf")),
        entry(1, temperature=float("nan"), wind_speed="strong", precipitation=10**400),
        entry(2, temperature=True, wind_speed=12.0, precipitation=-1.0),
        entry(3, temperature=4.0),
    ]

    forecast = parse_forecast(entries, WEATHER_UNITS, UNITS)

    assert forecast.temperature == pytest.approx([np.nan, np.nan, np.nan, 4.0], nan_ok=True)
    assert forecast.wind_speed == pytest.approx([np.nan, np.nan, 12.0, np.nan], nan_ok=True)
    assert np.isnan(forecast.rain_rate).all()


def test_parse_treats_wind_in_a_unit_that_is_not_a_speed_as_no_wind_forecast():
    beaufort = WEATHER_UNITS | {"wind_speed_unit": "Beaufort"}

    forecast = parse_forecast([entry(0, temperature=1.0, wind_speed=4)], beaufort, UNITS)

    assert np.isnan(forecast.wind_speed).all()


@pytest.mark.parametrize(
    ("entries", "reported", "message"),
    [
        pytest.param([], WEATHER_UNITS, "empty hourly forecast", id="empty"),
        pytest.param([entry(0, wind_speed=3.0)], WEATHER_UNITS, "no usable temp", id="no-temp"),
        pytest.param([entry(0, temperature=999.0)], WEATHER_UNITS, "no usable temp", id="absurd"),
        pytest.param([{"temperature": 1.0}], WEATHER_UNITS, "cannot read", id="no-datetime"),
        pytest.param([entry(0) | {"datetime": 5}], WEATHER_UNITS, "cannot read", id="bad-time"),
        pytest.param([entry(0, temperature=1.0)], {}, "cannot read", id="no-units"),
        pytest.param(
            [entry(0, temperature=1.0)],
            WEATHER_UNITS | {"temperature_unit": "K"},
            "cannot convert a temperature given in 'K'",
            id="kelvin",
        ),
    ],
)
def test_parse_refuses_a_forecast_it_cannot_use(entries, reported, message):
    with pytest.raises(DataError, match=message):
        parse_forecast(entries, reported, UNITS)


def test_outdoor_starts_from_the_sensor_and_settles_on_the_forecast():
    entries = [entry(0, temperature=10.0), entry(12, temperature=22.0)]
    times = START + 3600 * np.array([0.0, 3.0, 12.0])

    drivers = drivers_for(entries, times, outdoor=13.0)

    assert drivers.outdoor == pytest.approx([13.0, 13.0 + 3 * np.exp(-1), 22.0 + 3 * np.exp(-4)])


def test_the_gap_to_the_forecast_is_measured_now_and_not_at_its_first_entry():
    entries = [entry(0, temperature=10.0), entry(4, temperature=18.0)]
    now = START + 2 * 3600

    drivers = drivers_for(entries, np.array([now]), now=now, outdoor=15.0)

    # The forecast says 14 for now, so the sensor is one degree above it.
    assert drivers.outdoor == pytest.approx([15.0])


def test_a_missing_temperature_is_bridged_from_its_neighbours():
    entries = [entry(0, temperature=10.0), entry(1), entry(2, temperature=14.0)]

    drivers = drivers_for(entries, START + 3600 * np.array([1.0]), outdoor=10.0)

    assert drivers.outdoor == pytest.approx([12.0])


def test_forecast_wind_is_scaled_to_match_the_sensor():
    entries = [
        entry(0, temperature=1.0, wind_speed=10.0),
        entry(2, temperature=1.0, wind_speed=30.0),
    ]
    times = START + 3600 * np.array([0.0, 1.0, 2.0])

    drivers = drivers_for(entries, times, wind_scale=0.5)

    assert drivers.wind == pytest.approx([5.0, 10.0, 15.0])


def test_wind_holds_at_the_sensor_reading_when_the_forecast_has_none():
    entries = [entry(0, temperature=1.0), entry(2, temperature=1.0)]

    drivers = drivers_for(entries, START + 3600 * np.array([0.5, 1.5]), wind=7.0, wind_scale=0.5)

    assert drivers.wind.tolist() == [7.0, 7.0]


def test_rain_is_held_through_each_forecast_hour_and_absent_outside_them():
    entries = [
        entry(1, temperature=1.0, precipitation=3.0),
        entry(2, temperature=1.0, precipitation=4.0),
        entry(3, temperature=1.0),
    ]
    times = START + 3600 * np.array([0.5, 1.0, 1.9, 2.0, 2.9, 3.5])

    drivers = drivers_for(entries, times)

    assert drivers.rain.tolist() == [0.0, 3.0, 3.0, 4.0, 4.0, 0.0]


def test_without_a_light_sensor_sunlight_is_the_clear_sky_whatever_the_clouds():
    entries = [entry(0, temperature=1.0, cloud_coverage=100), entry(4, temperature=1.0)]
    times = START + 3600 * np.array([1.0, 2.0])

    drivers = drivers_for(entries, times)

    assert drivers.solar == pytest.approx(clear_sky_irradiance(times, 45.0, 0.0))
    assert (drivers.solar > 700).all()


def test_with_a_light_sensor_sunlight_is_scaled_and_dimmed_by_cloud():
    entries = [
        entry(0, temperature=1.0, cloud_coverage=0),
        entry(2, temperature=1.0, cloud_coverage=100),
    ]
    times = START + 3600 * np.array([0.0, 2.0])

    drivers = drivers_for(entries, times, light_scale=120.0)

    clear = clear_sky_irradiance(times, 45.0, 0.0)
    assert drivers.solar == pytest.approx([120.0 * clear[0], 120.0 * clear[1] * 0.25])


def test_the_equipment_is_off_in_the_future():
    drivers = drivers_for([entry(0, temperature=1.0)], START + 3600 * np.array([1.0, 2.0]))

    assert not drivers.heating.any()
    assert not drivers.cooling.any()
