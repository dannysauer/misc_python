import dataclasses
import json

import numpy as np
import pytest
from fakes import ALL_DRIVERS, NOW, FakeMetrics, write_config
from house import STEP_SECONDS, House, simulate_house

from hvac_predict.errors import ConfigError, DataError
from hvac_predict.solar import clear_sky_irradiance, cloud_attenuation
from hvac_predict.thermal import FEATURES, HorizonError, backtest, fit
from hvac_predict.training import load_model, save_model, train

DAYS = 30.0


@pytest.fixture(scope="module")
def house() -> House:
    return simulate_house(days=DAYS, end=NOW)


@pytest.fixture(scope="module")
def heavy() -> House:
    return simulate_house(days=40, end=NOW, walls=True)


def with_drivers(house: House, **changes: np.ndarray) -> House:
    return dataclasses.replace(house, drivers=dataclasses.replace(house.drivers, **changes))


def train_on(house: House, tmp_path, *, history=ALL_DRIVERS, days: float = DAYS):
    config = write_config(tmp_path, history=history, training={"days": days})
    series = {"indoor": house.indoor, **dataclasses.asdict(house.drivers)}
    return config, train(config, FakeMetrics(series), now=NOW)


def test_train_reports_how_a_fit_to_the_older_history_predicts_the_newest(house, tmp_path):
    # HVP-012
    _, trained = train_on(house, tmp_path)

    split = len(house.indoor) - int(len(house.indoor) * 0.2)
    older = fit(
        house.indoor,
        house.drivers,
        step_seconds=STEP_SECONDS,
        lag_hours=trained.model.lag_hours,
        window_steps=6,
        until=split,
    )
    expected = backtest(
        older.model, house.indoor, house.drivers, horizons_hours=(1.0, 2.0, 4.0, 8.0), first=split
    )
    assert trained.holdout == expected
    assert all(horizon.runs > 100 for horizon in trained.holdout)
    assert all(horizon.model_error < 0.3 * horizon.no_change_error for horizon in trained.holdout)
    assert trained.shortcoming() is None


def test_train_keeps_the_fit_to_all_of_the_history(house, tmp_path):
    _, trained = train_on(house, tmp_path)

    whole = fit(
        house.indoor,
        house.drivers,
        step_seconds=STEP_SECONDS,
        lag_hours=trained.model.lag_hours,
        window_steps=6,
    )
    assert trained.model == whole.model
    assert trained.windows == whole.windows == len(house.indoor) - 6
    assert trained.trained_at == "2026-05-28T20:30:00+00:00"
    assert trained.history_days == pytest.approx(DAYS, abs=0.01)


@pytest.mark.parametrize("replaced_by", ["noise", "a-two-day-swing"])
def test_the_newest_history_has_no_say_in_the_lag_time(heavy, tmp_path, replaced_by):
    # HVP-022. A search that looked at the newest fifth would shrug off the noise, which is as
    # bad for one lag time as for another, and would pick a short lag time for the swing.
    scrambled = heavy.indoor.copy()
    newest = slice(len(scrambled) - int(len(scrambled) * 0.2), None)
    hours = np.arange(len(scrambled))[newest] * STEP_SECONDS / 3600
    scrambled[newest] = {
        "noise": np.random.default_rng(8).uniform(15, 30, len(hours)),
        "a-two-day-swing": 21 + 4 * np.sin(2 * np.pi * hours / 48),
    }[replaced_by]

    _, trained = train_on(heavy, tmp_path, days=40.0)
    _, disturbed = train_on(dataclasses.replace(heavy, indoor=scrambled), tmp_path, days=40.0)

    assert disturbed.model.lag_hours == trained.model.lag_hours


def test_train_predicts_a_house_whose_walls_store_heat(heavy, tmp_path):
    _, trained = train_on(heavy, tmp_path, days=40.0)

    # The walls lag by 1 / (0.05 + 0.02) hours, which is between these two candidates.
    assert trained.model.lag_hours in (12.0, 16.0)
    assert trained.model.coefficients["mass"] > 0.2
    assert all(horizon.model_error < 0.25 * horizon.no_change_error for horizon in trained.holdout)


def test_train_copes_with_a_thermostat_that_reports_whole_degrees(heavy, tmp_path):
    noise = np.random.default_rng(3).normal(0, 0.1, len(heavy.indoor))
    coarse = dataclasses.replace(heavy, indoor=np.round(heavy.indoor + noise))

    _, trained = train_on(coarse, tmp_path, days=40.0)

    # Rounding alone puts 0.25 degrees of error into every reading.
    assert all(horizon.model_error < 0.4 for horizon in trained.holdout)
    assert trained.holdout[-1].model_error < 0.5 * trained.holdout[-1].no_change_error
    assert trained.shortcoming() is None


def test_train_uses_what_history_there_is_when_asked_for_more(house, tmp_path):
    _, trained = train_on(house, tmp_path, days=90.0)

    assert trained.history_days == pytest.approx(DAYS, abs=0.01)
    assert trained.windows == len(house.indoor) - 6
    assert trained.shortcoming() is None


def test_train_ignores_readings_no_sensor_could_mean(house, tmp_path):
    glitchy = house.indoor.copy()
    glitchy[::400] = 85.0
    outdoor = house.drivers.outdoor.copy()
    outdoor[200::400] = -127.0

    _, clean = train_on(house, tmp_path)
    _, trained = train_on(
        dataclasses.replace(with_drivers(house, outdoor=outdoor), indoor=glitchy), tmp_path
    )

    assert trained.windows < clean.windows
    assert trained.model.coefficients["envelope"] == pytest.approx(
        clean.model.coefficients["envelope"], rel=0.1
    )


def test_train_learns_how_the_light_sensor_reads_a_clear_sky(house, tmp_path):
    cloud = np.random.default_rng(2).uniform(0, 1, len(house.times))
    lux = 120.0 * clear_sky_irradiance(house.times, 45.0, 0.0) * cloud_attenuation(cloud)

    _, trained = train_on(with_drivers(house, solar=lux), tmp_path)

    assert trained.light_scale == pytest.approx(120.0, rel=0.05)


def test_train_without_a_light_sensor_learns_from_the_clear_sky(house, tmp_path):
    _, trained = train_on(house, tmp_path, history={})

    weights = trained.model.coefficients
    assert trained.light_scale is None
    assert weights["solar"] + weights["solar_lag"] > 0
    assert weights["wind"] == 0


def test_train_refuses_a_light_sensor_that_is_dark_all_day(house, tmp_path):
    dark = with_drivers(house, solar=np.zeros(len(house.times)))

    with pytest.raises(DataError, match="never rises above zero"):
        train_on(dark, tmp_path)


def test_train_refuses_a_light_sensor_with_no_daytime_readings(house, tmp_path):
    daylight = clear_sky_irradiance(house.times, 45.0, 0.0) > 0
    nights_only = with_drivers(house, solar=np.where(daylight, np.nan, 0.0))

    with pytest.raises(DataError, match="too few daytime readings"):
        train_on(nights_only, tmp_path)


def test_train_refuses_a_history_with_no_indoor_readings(house, tmp_path):
    blank = dataclasses.replace(house, indoor=np.full(len(house.indoor), np.nan))

    with pytest.raises(DataError, match=r"history.indoor_temp returned no readings"):
        train_on(blank, tmp_path)


def test_train_refuses_a_history_too_short_to_fit(house, tmp_path):
    with pytest.raises(DataError, match="gap-free windows"):
        train_on(house, tmp_path, days=0.5)


def test_train_refuses_a_history_with_no_stretch_long_enough_to_tune_on(house, tmp_path):
    hourly_gaps = house.indoor.copy()
    hourly_gaps[::60] = np.nan

    with pytest.raises(DataError, match="no gap-free stretch of 8 h to tune the model on"):
        train_on(dataclasses.replace(house, indoor=hourly_gaps), tmp_path)


def test_a_model_that_should_not_control_a_thermostat_says_why(house, tmp_path):
    # HVP-012
    _, trained = train_on(house, tmp_path)
    no_link = dict.fromkeys(FEATURES, 0.0) | {"bias": 0.1}
    no_better = [HorizonError(1.0, model_error=0.5, no_change_error=0.5, runs=10)]

    unlinked = dataclasses.replace(
        trained, model=dataclasses.replace(trained.model, coefficients=no_link)
    )
    unchecked = dataclasses.replace(trained, holdout=[])
    useless = dataclasses.replace(trained, holdout=no_better)

    assert "no link between outdoor and indoor" in unlinked.shortcoming()
    assert "long enough to check the model against" in unchecked.shortcoming()
    assert "no better than assuming the temperature does not change" in useless.shortcoming()


def test_a_model_survives_being_saved_and_loaded(house, tmp_path):
    config, trained = train_on(house, tmp_path)

    save_model(trained, config.model_path)
    save_model(trained, config.model_path)

    assert load_model(config.model_path, config) == trained
    assert sorted(path.name for path in tmp_path.iterdir()) == ["hvac-predict.toml", "model.json"]


def test_saving_where_there_is_no_directory_is_reported(house, tmp_path):
    _, trained = train_on(house, tmp_path)

    with pytest.raises(ConfigError, match=r"cannot write .*nowhere/model.json"):
        save_model(trained, tmp_path / "nowhere" / "model.json")


def test_a_model_is_refused_once_the_settings_it_depends_on_change(house, tmp_path):
    config, trained = train_on(house, tmp_path)
    save_model(trained, config.model_path)
    changed = dataclasses.replace(config, units=dataclasses.replace(config.units, temperature="°F"))

    with pytest.raises(ConfigError, match=r"changed since .* was trained"):
        load_model(config.model_path, changed)


def test_a_missing_model_says_to_train_first(tmp_path):
    config = write_config(tmp_path)

    with pytest.raises(ConfigError, match="run `hvac-predict train` first"):
        load_model(config.model_path, config)


@pytest.mark.parametrize("content", ["not json", "[]", "{}", '{"format": 99}', '{"format": 1}'])
def test_a_file_that_is_not_a_model_is_refused(tmp_path, content):
    config = write_config(tmp_path)
    config.model_path.write_text(content)

    with pytest.raises(ConfigError, match="model"):
        load_model(config.model_path, config)


def test_a_model_with_weights_this_version_does_not_fit_is_refused(house, tmp_path):
    config, trained = train_on(house, tmp_path)
    save_model(trained, config.model_path)
    stored = json.loads(config.model_path.read_text())
    del stored["model"]["coefficients"]["mass"]
    config.model_path.write_text(json.dumps(stored))

    with pytest.raises(ConfigError, match="not a model this version understands"):
        load_model(config.model_path, config)
