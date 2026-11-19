import dataclasses

import numpy as np
import pytest
from advising import COLD_FORECAST, KEPT, LEAKY, readings, trained
from fakes import ALL_DRIVERS, NOON, NOW, WEATHER_UNITS, FakeMetrics, hourly_forecast, write_config
from house import simulate_house

from hvac_predict.decision import Outlook
from hvac_predict.errors import DataError
from hvac_predict.forecast import parse_forecast
from hvac_predict.outlook import look_ahead, recent_history
from hvac_predict.training import TrainedModel, train


def outlook_for(
    tmp_path,
    forecast: list[dict],
    *,
    indoor: np.ndarray,
    outdoor: np.ndarray,
    drivers: dict[str, np.ndarray] | None = None,
    settings: dict[str, dict] | None = None,
    model: TrainedModel = LEAKY,
    now: float = NOW,
) -> Outlook:
    """Predict the house from its readings and a forecast, with no thermostat in the picture.

    ``drivers`` holds the series of any drivers besides the outdoor
    temperature, and ``settings`` holds sections of the configuration.
    """
    drivers = drivers or {}
    config = write_config(tmp_path, history={name: name for name in drivers}, **(settings or {}))
    metrics = FakeMetrics({"indoor": indoor, "outdoor": outdoor, **drivers}, end=now)
    history = recent_history(metrics, config, model.model, now)
    return look_ahead(config, model, history, parse_forecast(forecast, WEATHER_UNITS, config.units))


def test_predicts_the_house_closing_on_the_forecast_temperature_step_by_step(tmp_path):
    outlook = outlook_for(tmp_path, COLD_FORECAST, indoor=readings(21.0), outdoor=readings(5.0))

    assert outlook.minutes.tolist() == [5.0 * step for step in range(97)]
    assert outlook.indoor == pytest.approx(5.0 + 16.0 * KEPT ** np.arange(97))
    assert outlook.outdoor == pytest.approx(5.0)


def test_carries_the_gap_between_sensor_and_forecast_into_the_prediction(tmp_path):
    outlook = outlook_for(tmp_path, COLD_FORECAST, indoor=readings(21.0), outdoor=readings(9.0))

    assert outlook.outdoor[0] == 9.0
    assert outlook.outdoor[12] == pytest.approx(5.0 + 4.0 * np.exp(-1 / 3))
    assert outlook.outdoor[-1] == pytest.approx(5.0 + 4.0 * np.exp(-8 / 3))


def test_a_driver_whose_newest_sample_has_not_arrived_counts_at_its_last_reading(tmp_path):
    outdoor = readings(9.0, latest=np.nan)

    outlook = outlook_for(tmp_path, COLD_FORECAST, indoor=readings(21.0), outdoor=outdoor)

    assert outlook.outdoor[0] == 9.0
    assert outlook.outdoor[12] == pytest.approx(5.0 + 4.0 * np.exp(-1 / 3))


def test_looks_as_far_ahead_as_it_is_told_to(tmp_path):
    outlook = outlook_for(
        tmp_path,
        COLD_FORECAST,
        indoor=readings(21.0),
        outdoor=readings(5.0),
        settings={"decision": {"horizon_hours": 4.0, "overshoot_minutes": 240.0}},
    )

    assert outlook.minutes[-1] == 240


def test_looks_no_further_ahead_than_the_forecast_reaches(tmp_path):
    three_hours = hourly_forecast(3, temperature=5.0)

    outlook = outlook_for(tmp_path, three_hours, indoor=readings(21.0), outdoor=readings(5.0))

    assert outlook.minutes[-1] == 150


def test_refuses_a_forecast_shorter_than_an_hour(tmp_path):
    one_hour = hourly_forecast(1, temperature=5.0)

    with pytest.raises(DataError, match="reaches less than 1 h ahead"):
        outlook_for(tmp_path, one_hour, indoor=readings(21.0), outdoor=readings(5.0))


@pytest.mark.parametrize("stale", ["indoor", "outdoor", "wind"])
def test_refuses_to_act_on_readings_more_than_twenty_minutes_old(tmp_path, stale):
    # HVP-008
    series = {"indoor": readings(21.0), "outdoor": readings(5.0), "wind": readings(3.0)}

    def outlook_with(gap_steps: int) -> Outlook:
        gappy = series | {stale: readings(21.0, latest=np.nan, latest_count=gap_steps)}
        return outlook_for(
            tmp_path,
            COLD_FORECAST,
            indoor=gappy["indoor"],
            outdoor=gappy["outdoor"],
            drivers={"wind": gappy["wind"]},
        )

    outlook_with(4)
    with pytest.raises(DataError, match=f"no believable {stale} reading from the last 20 min"):
        outlook_with(5)


def test_runs_on_from_a_reading_that_is_a_few_minutes_old(tmp_path):
    indoor = readings(21.0, latest=np.nan)

    outlook = outlook_for(tmp_path, COLD_FORECAST, indoor=indoor, outdoor=readings(5.0))

    # The newest reading is two steps old, so the house has moved on two steps by now.
    assert outlook.indoor[0] == pytest.approx(5.0 + 16.0 * KEPT**2)
    assert outlook.indoor[1] == pytest.approx(5.0 + 16.0 * KEPT**3)


def test_what_the_equipment_is_doing_now_does_not_carry_into_the_prediction(tmp_path):
    furnace = trained(envelope=0.1, heating=5.0)

    outlook = outlook_for(
        tmp_path,
        COLD_FORECAST,
        indoor=readings(21.0),
        outdoor=readings(5.0),
        drivers={"heating": readings(1.0)},
        model=furnace,
    )

    assert outlook.indoor == pytest.approx(5.0 + 16.0 * KEPT ** np.arange(97))


def test_the_equipment_counts_for_the_minutes_since_the_newest_reading(tmp_path):
    furnace = trained(envelope=0.1, heating=5.0)

    outlook = outlook_for(
        tmp_path,
        COLD_FORECAST,
        indoor=readings(21.0, latest=np.nan),
        outdoor=readings(5.0),
        drivers={"heating": readings(1.0)},
        model=furnace,
    )

    temperature = 21.0
    for _ in range(2):
        temperature += (0.1 * (5.0 - temperature) + 5.0) / 12
    assert outlook.indoor[0] == pytest.approx(temperature)
    assert outlook.indoor[1] == pytest.approx(temperature + 0.1 * (5.0 - temperature) / 12)


def test_forecast_wind_is_scaled_before_the_model_sees_it(tmp_path):
    draughty = trained(envelope=0.05, wind=0.01)

    outlook = outlook_for(
        tmp_path,
        hourly_forecast(12, temperature=5.0, wind_speed=20.0),
        indoor=readings(21.0),
        outdoor=readings(5.0),
        drivers={"wind": readings(20.0)},
        model=draughty,
        settings={"forecast": {"wind_scale": 0.5}},
    )

    # The first step is driven by the sensor's 20; the rest by half the forecast's 20.
    first, later = 1 - (0.05 + 0.01 * 20) / 12, 1 - (0.05 + 0.01 * 10) / 12
    assert outlook.indoor[-1] == pytest.approx(5.0 + 16.0 * first * later**95)


@pytest.mark.parametrize(
    ("light_scale", "drivers"), [(100.0, {"solar": readings(0.0)}), (None, {})]
)
def test_clouds_dim_the_sun_only_for_a_model_trained_on_a_light_sensor(
    tmp_path, light_scale, drivers
):
    sunny_house = trained(solar=0.00002, light_scale=light_scale)

    def end_of_afternoon(cloud_coverage: int) -> float:
        return outlook_for(
            tmp_path,
            hourly_forecast(8, start=NOON, temperature=5.0, cloud_coverage=cloud_coverage),
            indoor=readings(21.0),
            outdoor=readings(5.0),
            drivers=drivers,
            model=sunny_house,
            now=NOON,
            settings={"decision": {"horizon_hours": 4.0, "overshoot_minutes": 240.0}},
        ).indoor[-1]

    clear, overcast = end_of_afternoon(0), end_of_afternoon(100)

    if light_scale is None:
        assert clear == overcast
    else:
        assert clear > overcast + 1.0


def test_refuses_a_prediction_no_house_could_follow(tmp_path):
    runaway = trained(bias=50.0)

    with pytest.raises(DataError, match="leaves the range a house can be in"):
        outlook_for(
            tmp_path, COLD_FORECAST, indoor=readings(21.0), outdoor=readings(5.0), model=runaway
        )


def test_the_outlook_matches_a_house_that_really_is_left_alone(tmp_path):
    idle_hours = 6
    house = simulate_house(days=30, end=NOW + idle_hours * 3600, idle_hours=idle_hours)
    now_index = len(house.indoor) - 1 - idle_hours * 12
    series = {"indoor": house.indoor, **dataclasses.asdict(house.drivers)}
    known = {name: values[: now_index + 1] for name, values in series.items()}
    config = write_config(
        tmp_path,
        history=ALL_DRIVERS,
        training={"days": 29.0},
        decision={"horizon_hours": float(idle_hours), "overshoot_minutes": 360.0},
    )
    model = train(config, FakeMetrics(known), now=NOW)

    # A forecast that knows the weather to come: each hour's temperature and wind, and its rain.
    hours = [now_index - 6 + 12 * hour for hour in range(idle_hours + 1)]
    forecast = [
        entry
        | {
            "temperature": float(house.drivers.outdoor[at]),
            "wind_speed": float(house.drivers.wind[at]),
            "precipitation": float(house.drivers.rain[at : at + 12].mean()),
        }
        for entry, at in zip(hourly_forecast(idle_hours + 1), hours, strict=True)
    ]
    history = recent_history(FakeMetrics(known), config, model.model, NOW)
    outlook = look_ahead(
        config, model, history, parse_forecast(forecast, WEATHER_UNITS, config.units)
    )

    left_to_itself = house.indoor[now_index:]
    assert outlook.indoor == pytest.approx(left_to_itself, abs=0.1)
    assert abs(left_to_itself[-1] - left_to_itself[0]) > 2.0
