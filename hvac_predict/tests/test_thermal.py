import dataclasses
import math

import numpy as np
import pytest
from house import STEP_SECONDS, TRUE_WEIGHTS, simulate_house

from hvac_predict.errors import DataError
from hvac_predict.thermal import FEATURES, Drivers, ThermalModel, backtest, fit, predict

SHAPE = {"step_seconds": STEP_SECONDS, "lag_hours": 3.0, "window_steps": 6}
STEP_HOURS = STEP_SECONDS / 3600


def constant_drivers(size: int, **values: float) -> Drivers:
    names = [spec.name for spec in dataclasses.fields(Drivers)]
    return Drivers(**{name: np.full(size, values.get(name, 0.0)) for name in names})


def model(**weights: float) -> ThermalModel:
    return ThermalModel({name: weights.get(name, 0.0) for name in FEATURES}, STEP_SECONDS, 3.0)


def run_by_the_book(house_model: ThermalModel, indoor: np.ndarray, drivers: Drivers) -> np.ndarray:
    """Step the equation in the module docstring forward, one grid step and one term at a time."""
    weight = house_model.coefficients
    newest_weight = 1 - math.exp(-STEP_HOURS / house_model.lag_hours)

    def smoothed(values: np.ndarray) -> list[float]:
        result = [float(values[0])]
        for value in values[1:]:
            result.append(result[-1] + newest_weight * (value - result[-1]))
        return result

    outdoor_lag, solar_lag = smoothed(drivers.outdoor), smoothed(drivers.solar)
    temperature, stored = float(indoor[-1]), smoothed(indoor)[-1]
    run = [temperature]
    for point in range(len(indoor) - 1, len(drivers) - 1):
        gap = drivers.outdoor[point] - temperature
        rate = (
            weight["bias"]
            + weight["envelope"] * gap
            + weight["envelope_lag"] * (outdoor_lag[point] - temperature)
            + weight["mass"] * (stored - temperature)
            + weight["wind"] * drivers.wind[point] * gap
            + weight["solar"] * drivers.solar[point]
            + weight["solar_lag"] * solar_lag[point]
            + weight["rain"] * drivers.rain[point]
            + weight["heating"] * drivers.heating[point]
            + weight["cooling"] * drivers.cooling[point]
        )
        temperature += rate * STEP_HOURS
        stored += newest_weight * (temperature - stored)
        run.append(temperature)
    return np.array(run)


@pytest.fixture(scope="module")
def house():
    return simulate_house(days=30)


def test_fit_recovers_the_physics_of_a_simulated_house(house):
    weights = fit(house.indoor, house.drivers, **SHAPE).model.coefficients

    assert weights["envelope"] + weights["envelope_lag"] == pytest.approx(
        TRUE_WEIGHTS["envelope"], rel=0.1
    )
    assert weights["solar"] + weights["solar_lag"] == pytest.approx(TRUE_WEIGHTS["solar"], rel=0.1)
    for name in ("wind", "rain", "heating", "cooling"):
        assert weights[name] == pytest.approx(TRUE_WEIGHTS[name], rel=0.15)


def test_fit_keeps_every_weight_on_its_physical_side():
    # HVP-021
    random = np.random.default_rng(1)
    size = 2000
    noise = Drivers(
        outdoor=random.normal(10, 5, size),
        solar=random.uniform(0, 800, size),
        wind=random.uniform(0, 20, size),
        rain=random.uniform(0, 2, size),
        heating=random.integers(0, 2, size).astype(float),
        cooling=random.integers(0, 2, size).astype(float),
    )

    weights = fit(random.normal(21, 1, size), noise, **SHAPE).model.coefficients

    assert all(weights[name] >= 0 for name in FEATURES if name not in ("bias", "rain", "cooling"))
    assert weights["rain"] <= 0
    assert weights["cooling"] <= 0


def test_fit_gives_exactly_no_weight_to_a_driver_it_could_only_use_backwards(house):
    # HVP-021
    backwards = dataclasses.replace(house.drivers, heating=1 - house.drivers.heating)

    weights = fit(house.indoor, backwards, **SHAPE).model.coefficients

    assert weights["heating"] == 0


def test_fit_gives_no_weight_to_a_driver_that_never_varies(house):
    still = dataclasses.replace(
        house.drivers, wind=np.zeros(len(house.indoor)), heating=np.ones(len(house.indoor))
    )

    weights = fit(house.indoor, still, **SHAPE).model.coefficients

    assert weights["wind"] == 0
    assert weights["heating"] == 0


def test_fit_leaves_out_windows_with_missing_readings(house):
    gappy = house.indoor.copy()
    gappy[::50] = np.nan

    complete = fit(house.indoor, house.drivers, **SHAPE)
    partial = fit(gappy, house.drivers, **SHAPE)

    assert partial.windows < complete.windows
    assert partial.model.coefficients["envelope"] == pytest.approx(
        complete.model.coefficients["envelope"], rel=0.1
    )


def test_fit_can_be_limited_to_the_start_of_the_history(house):
    assert fit(house.indoor, house.drivers, **SHAPE, until=4000).windows == 4000 - 6


@pytest.mark.parametrize("until", [100, 3])
def test_fit_refuses_too_little_history(house, until):
    with pytest.raises(DataError, match="gap-free windows"):
        fit(house.indoor, house.drivers, **SHAPE, until=until)


def test_predict_closes_the_same_share_of_the_gap_to_outdoors_every_step():
    run = predict(model(envelope=0.5), np.array([21.0]), constant_drivers(121, outdoor=5.0))

    assert run == pytest.approx(5.0 + 16.0 * (1 - 0.5 / 12) ** np.arange(121))


def test_a_fit_and_a_run_agree_about_a_house_that_cools_exponentially():
    # HVP-023
    hours = np.arange(3000) / 12
    indoor = 5.0 + 16.0 * np.exp(-0.5 * (hours % 40))  # reheated every 40 hours
    reheated = (np.arange(3000) % 480 == 479).astype(float)
    drivers = dataclasses.replace(constant_drivers(3000, outdoor=5.0), heating=reheated)

    fitted = fit(indoor, drivers, **SHAPE).model
    run = predict(fitted, indoor[:1], constant_drivers(25, outdoor=5.0))

    # The fit learns how far the house moves in one step, and the run takes the same steps.
    assert run[-1] == pytest.approx(5.0 + 16.0 * np.exp(-0.5 * 2), abs=0.01)


def test_predict_stays_stable_for_a_house_that_responds_within_one_step():
    run = predict(model(envelope=500.0), np.array([21.0]), constant_drivers(11, outdoor=5.0))

    assert run[1:] == pytest.approx(5.0)


def test_predict_handles_a_house_with_no_link_to_outdoors():
    run = predict(model(bias=2.0), np.array([20.0]), constant_drivers(13))

    assert run[-1] == pytest.approx(22.0)


def test_predict_starts_from_the_newest_reading_before_a_gap():
    indoor = np.array([18.0, 19.0, 20.0, np.nan, np.nan])

    run = predict(model(bias=12.0), indoor, constant_drivers(7))

    # The run covers the two grid points with no reading as well as the two future ones.
    assert run == pytest.approx([20.0, 21.0, 22.0, 23.0, 24.0])


def test_each_step_is_driven_by_the_conditions_at_its_start():
    # HVP-023
    drivers = dataclasses.replace(constant_drivers(4), outdoor=np.array([0.0, 100.0, 100.0, 100.0]))

    run = predict(model(envelope=0.1), np.array([0.0]), drivers)

    # Outdoors is still at 0 when the first step begins, so the house has not moved after it.
    first = 100 * 0.1 / 12
    assert run == pytest.approx([0.0, 0.0, first, first + (100 - first) * 0.1 / 12])


def test_a_driver_with_no_reading_is_taken_not_to_have_changed():
    whole = constant_drivers(20, outdoor=5.0, wind=3.0)
    outdoor, wind = whole.outdoor.copy(), whole.wind.copy()
    outdoor[6:8], wind[7] = np.nan, np.nan
    gappy = dataclasses.replace(whole, outdoor=outdoor, wind=wind)
    house_model, indoor = model(envelope=0.3, wind=0.05), np.full(8, 21.0)

    assert predict(house_model, indoor, gappy) == pytest.approx(predict(house_model, indoor, whole))


def test_walls_pull_the_air_back_towards_where_it_has_been():
    # The house sat at 20 degrees for days, then the air alone was warmed to 24.
    indoor = np.append(np.full(2000, 20.0), 24.0)

    run = predict(model(mass=1.0), indoor, constant_drivers(len(indoor) + 36))

    # Air and walls meet at 21: the walls weigh three times the air, because
    # they take three hours to follow it and it takes one hour to follow them.
    assert run[0] == 24.0
    assert (np.diff(run) < 0).all()
    assert run[-1] == pytest.approx(21.0, abs=0.15)


def test_predict_agrees_with_the_documented_equation_term_by_term():
    random = np.random.default_rng(5)
    past, future = 60, 48
    drivers = Drivers(
        outdoor=10 + np.cumsum(random.normal(0, 0.3, past + future)),
        solar=random.uniform(0, 600, past + future),
        wind=random.uniform(0, 15, past + future),
        rain=random.uniform(0, 2, past + future),
        heating=random.integers(0, 2, past + future).astype(float),
        cooling=random.integers(0, 2, past + future).astype(float),
    )
    indoor = 21 + np.cumsum(random.normal(0, 0.1, past))
    house_model = model(
        bias=0.1,
        envelope=0.05,
        envelope_lag=0.08,
        mass=0.4,
        wind=0.004,
        solar=0.001,
        solar_lag=0.002,
        rain=-0.2,
        heating=2.0,
        cooling=-1.5,
    )

    run = predict(house_model, indoor, drivers)

    assert len(run) == future + 1
    assert run == pytest.approx(run_by_the_book(house_model, indoor, drivers), abs=1e-9)


def test_backtest_beats_assuming_no_change(house):
    split = len(house.indoor) * 4 // 5
    trained = fit(house.indoor, house.drivers, **SHAPE, until=split).model

    report = backtest(trained, house.indoor, house.drivers, horizons_hours=(1.0, 4.0), first=split)

    assert [horizon.hours for horizon in report] == [1.0, 4.0]
    assert all(horizon.runs > 50 for horizon in report)
    assert all(horizon.model_error < 0.3 * horizon.no_change_error for horizon in report)


def test_backtest_measures_the_same_runs_that_predict_makes(house):
    trained = fit(house.indoor, house.drivers, **SHAPE).model
    start, steps = 5000, 12
    past = dataclasses.replace(
        house.drivers,
        **{
            spec.name: getattr(house.drivers, spec.name)[: start + steps + 1]
            for spec in dataclasses.fields(Drivers)
        },
    )

    (hour,) = backtest(
        trained,
        house.indoor,
        house.drivers,
        horizons_hours=(1.0,),
        first=start,
        stop=start + steps + 1,
    )

    predicted = predict(trained, house.indoor[: start + 1], past)[-1]
    assert hour.runs == 1
    assert hour.model_error == pytest.approx(abs(predicted - house.indoor[start + steps]))


def test_backtest_stays_between_its_first_and_stop_points(house):
    trained = fit(house.indoor, house.drivers, **SHAPE).model

    report = backtest(
        trained, house.indoor, house.drivers, horizons_hours=(1.0, 2.0), first=1000, stop=1100
    )

    # Runs start every 12 steps and need 24 more, so they begin at 1000 through 1072.
    assert report[0].runs == 7


def test_backtest_leaves_out_exactly_the_runs_that_touch_a_gap(house):
    trained = fit(house.indoor, house.drivers, **SHAPE).model
    gappy = house.indoor.copy()
    gappy[1024] = np.nan

    report = backtest(
        trained, gappy, house.drivers, horizons_hours=(1.0, 2.0), first=1000, stop=1100
    )

    # Of the runs beginning at 1000, 1012, ..., 1072, three reach or start at point 1024.
    assert report[0].runs == 4


def test_backtest_compares_against_the_temperature_staying_where_it_started(house):
    trained = fit(house.indoor, house.drivers, **SHAPE).model

    (hour,) = backtest(
        trained, house.indoor, house.drivers, horizons_hours=(1.0,), first=5000, stop=5013
    )

    assert hour.no_change_error == pytest.approx(abs(house.indoor[5012] - house.indoor[5000]))


def test_a_gap_in_the_history_is_bridged_by_the_last_reading_before_it(house):
    trained = fit(house.indoor, house.drivers, **SHAPE).model
    lagged = dataclasses.replace(
        trained, coefficients=trained.coefficients | {"envelope_lag": 0.2, "solar_lag": 0.001}
    )
    gappy_outdoor, held_outdoor = house.drivers.outdoor.copy(), house.drivers.outdoor.copy()
    gappy_solar, held_solar = house.drivers.solar.copy(), house.drivers.solar.copy()
    gappy_outdoor[4900:4960], held_outdoor[4900:4960] = np.nan, house.drivers.outdoor[4899]
    gappy_solar[4900:4960], held_solar[4900:4960] = np.nan, house.drivers.solar[4899]
    gappy = dataclasses.replace(house.drivers, outdoor=gappy_outdoor, solar=gappy_solar)
    held = dataclasses.replace(house.drivers, outdoor=held_outdoor, solar=held_solar)
    replay = {"horizons_hours": (2.0,), "first": 4960, "stop": 5200}

    assert backtest(lagged, house.indoor, gappy, **replay) == backtest(
        lagged, house.indoor, held, **replay
    )


def test_fit_gives_no_weight_to_a_wind_that_never_changes(house):
    stuck = dataclasses.replace(house.drivers, wind=np.full(len(house.indoor), 4.0))

    weights = fit(house.indoor, stuck, **SHAPE).model.coefficients

    assert weights["wind"] == 0
    assert weights["envelope"] + weights["envelope_lag"] > 0.05


def test_backtest_skips_runs_that_cross_a_gap(house):
    gappy = house.indoor.copy()
    gappy[-200:] = np.nan
    trained = fit(house.indoor, house.drivers, **SHAPE).model
    split = len(house.indoor) - 400

    whole = backtest(trained, house.indoor, house.drivers, horizons_hours=(1.0,), first=split)
    partial = backtest(trained, gappy, house.drivers, horizons_hours=(1.0,), first=split)

    assert 0 < partial[0].runs < whole[0].runs


def test_backtest_reports_nothing_when_no_run_is_gap_free(house):
    trained = fit(house.indoor, house.drivers, **SHAPE).model
    blank = np.full(len(house.indoor), np.nan)

    assert backtest(trained, blank, house.drivers, horizons_hours=(1.0,), first=0) == []
