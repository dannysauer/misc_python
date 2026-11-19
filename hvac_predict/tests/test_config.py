import dataclasses
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

import pytest
from fakes import write_config, write_settings

from hvac_predict.config import Config, _checked, load_config
from hvac_predict.errors import ConfigError

EXAMPLE = Path(__file__).parent.parent / "config.example.toml"


def test_the_example_configuration_loads():
    config = load_config(EXAMPLE)

    assert config.homeassistant.climate_entity == "climate.thermostat"
    assert config.homeassistant.enable_entity is None
    assert config.units.temperature == "°F"
    assert config.history.cooling.endswith('action="cooling"})')
    assert config.comfort.differential == 3.0
    assert config.comfort.targets == {"Home": 68.0, "Morning": 66.0, "Sleep": 64.0}
    assert config.comfort.target_for("Away") is None
    assert config.model_path == EXAMPLE.parent / "model.json"
    assert config.state_path == EXAMPLE.parent / "state.json"


def test_settings_left_out_take_their_defaults(tmp_path):
    config = write_config(tmp_path)

    assert config.homeassistant.comfort_attribute == "climate_mode"
    assert config.decision.horizon_hours == 8.0
    assert config.decision.coast_minutes == 90.0
    assert config.decision.overshoot_minutes == 480.0
    assert config.decision.min_hold_minutes == 30.0
    assert config.training.days == 60.0
    assert config.training.step_seconds == 300
    assert config.training.window_steps == 6
    assert config.forecast.wind_scale == 1.0
    assert config.units.wind_speed == "km/h"
    assert config.history.solar is None
    assert config.model_path == tmp_path / "model.json"
    assert config.state_path == tmp_path / "state.json"


def test_optional_settings_can_be_given(tmp_path):
    config = write_config(
        tmp_path,
        homeassistant={
            "enable_entity": "input_boolean.hvac_predict",
            "comfort_attribute": "preset_mode",
        },
        decision={"coast_minutes": 60, "min_hold_minutes": 15.5},
        training={"step_minutes": 10, "window_minutes": 60},
        forecast={"wind_scale": 0.5},
    )

    assert config.homeassistant.enable_entity == "input_boolean.hvac_predict"
    assert config.homeassistant.comfort_attribute == "preset_mode"
    assert (config.decision.coast_minutes, config.decision.min_hold_minutes) == (60.0, 15.5)
    assert config.training.window_steps == 6
    assert config.forecast.wind_scale == 0.5


def test_the_files_it_keeps_are_relative_to_the_configuration_file(tmp_path):
    path = write_settings(tmp_path)
    kept = 'model_path = "models/house.json"\nstate_path = "run/seen.json"\n'
    path.write_text(kept + path.read_text())

    config = load_config(path)

    assert config.model_path == tmp_path / "models/house.json"
    assert config.state_path == tmp_path / "run/seen.json"


def test_a_whole_number_is_accepted_where_a_decimal_is_expected(tmp_path):
    config = write_config(tmp_path, comfort={"differential": 3, "targets": {"Home": 20}})

    assert config.comfort.differential == 3.0
    assert isinstance(config.comfort.differential, float)
    assert config.comfort.targets == {"Home": 20.0}
    assert isinstance(config.comfort.targets["Home"], float)


def test_a_comfort_setting_is_found_whatever_its_case(tmp_path):
    comfort = write_config(tmp_path).comfort

    assert comfort.target_for("home") == comfort.target_for("HOME") == 21.0
    assert comfort.target_for("Sleep") == 18.0
    assert comfort.target_for("Away") is None


def test_the_pairs_of_all_the_targets_reach_a_differential_beyond_them(tmp_path):
    assert write_config(tmp_path).comfort.reach == (16.0, 23.0)


def test_every_setting_is_of_a_kind_the_loader_understands():
    samples = {float: 1.5, int: 2, str: "text"}
    sections = [each for each in get_type_hints(Config).values() if dataclasses.is_dataclass(each)]

    for section in sections:
        for name, annotation in get_type_hints(section).items():
            if get_origin(annotation) is dict:
                sample = {"name": samples[get_args(annotation)[1]]}
            else:
                kinds = (annotation, *get_args(annotation))
                sample = samples[next(kind for kind in kinds if kind in samples)]
            assert _checked(sample, annotation, name) == sample


@pytest.mark.parametrize(
    ("sections", "message"),
    [
        ({"forcast": {"wind_scale": 1.0}}, "unknown setting: forcast"),
        ({"decision": {"coast_minuets": 5}}, "unknown setting: decision.coast_minuets"),
        ({"comfort": {"differential": None}}, "missing required setting: comfort.differential"),
        ({"comfort": {"targets": None}}, "missing required setting: comfort.targets"),
        ({"comfort": {"differential": "3"}}, "comfort.differential must be a number"),
        ({"comfort": {"differential": True}}, "comfort.differential must be a number"),
        ({"comfort": {"differential": 0.4}}, "comfort.differential must be between 0.5 and 6 °C"),
        ({"comfort": {"differential": 6.5}}, "comfort.differential must be between 0.5 and 6 °C"),
        (
            {"units": {"temperature": "°F"}, "comfort": {"differential": 0.5}},
            "comfort.differential must be between 0.9 and 10.8 °F",
        ),
        ({"comfort": {"targets": {}}}, "must give a target for at least one comfort setting"),
        ({"comfort": {"targets": {"Home": "20"}}}, "comfort.targets.Home must be a number"),
        ({"comfort": {"targets": 20.0}}, r"\[comfort.targets\] must be a table"),
        (
            {"comfort": {"targets": {"Home": 20.0, "home": 21.0}}},
            "names must differ by more than upper and lower case",
        ),
        (
            {"comfort": {"targets": {" Home": 20.0}}},
            "names must not be empty or begin or end with a space",
        ),
        ({"comfort": {"targets": {"": 20.0}}}, "names must not be empty or begin or end"),
        # Fahrenheit numbers left in a Celsius file, and the other way round.
        (
            {"comfort": {"targets": {"Home": 68.0}}},
            "comfort.targets.Home must be between 5 and 35 °C",
        ),
        (
            {"units": {"temperature": "°F"}, "comfort": {"targets": {"Home": 20.0}}},
            "comfort.targets.Home must be between 41 and 95 °F",
        ),
        ({"training": {"step_minutes": 2.5}}, "training.step_minutes must be a whole number"),
        ({"units": {"temperature": 3}}, "units.temperature must be text"),
        ({"units": {"temperature": "K"}}, "units.temperature must be one of °C, °F"),
        ({"units": {"wind_speed": "Beaufort"}}, "units.wind_speed must be one of"),
        ({"units": {"precipitation": "cm"}}, "units.precipitation must be one of mm, in"),
        ({"location": {"latitude": 141.88}}, "location.latitude must be within ±90"),
        ({"location": {"longitude": -200.0}}, "location.longitude must be within ±180"),
        ({"training": {"days": 0}}, "training.days must be positive"),
        ({"training": {"days": 1e9}}, "training.days must be positive and at most 730"),
        ({"training": {"step_minutes": 0}}, "training.step_minutes must divide 60"),
        ({"training": {"step_minutes": 7}}, "training.step_minutes must divide 60"),
        ({"training": {"window_minutes": 12}}, "multiple of training.step_minutes"),
        ({"training": {"window_minutes": 0}}, "multiple of training.step_minutes"),
        ({"decision": {"horizon_hours": 0.5}}, "decision.horizon_hours must be between 1 and 48"),
        ({"decision": {"horizon_hours": 49}}, "decision.horizon_hours must be between 1 and 48"),
        ({"decision": {"coast_minutes": -1}}, "decision.coast_minutes must be between 0 and 2880"),
        (
            {"decision": {"coast_minutes": 2881}},
            "decision.coast_minutes must be between 0 and 2880",
        ),
        (
            {"decision": {"overshoot_minutes": -1}},
            "decision.overshoot_minutes must be between 0 and 2880",
        ),
        (
            {"decision": {"overshoot_minutes": 1e9}},
            "decision.overshoot_minutes must be between 0 and 2880",
        ),
        (
            {"decision": {"min_hold_minutes": -1}},
            "decision.min_hold_minutes must be between 0 and 2880",
        ),
        (
            {"decision": {"min_hold_minutes": 2881}},
            "decision.min_hold_minutes must be between 0 and 2880",
        ),
        ({"forecast": {"wind_scale": 0}}, "forecast.wind_scale must be positive"),
        ({"history": {"indoor_temp": "  "}}, "history.indoor_temp must not be empty"),
        (
            {"homeassistant": {"comfort_attribute": ""}},
            "homeassistant.comfort_attribute must not be empty",
        ),
        (
            {"homeassistant": {"climate_entity": "climate/../config"}},
            "homeassistant.climate_entity must look like 'domain.object_id'",
        ),
        (
            {"homeassistant": {"weather_entity": "weather"}},
            "homeassistant.weather_entity must look like",
        ),
        (
            {"homeassistant": {"enable_entity": "Input_Boolean.x"}},
            "homeassistant.enable_entity must look like",
        ),
        ({"homeassistant": {"url": "homeassistant:8123"}}, "homeassistant.url must be an http"),
        ({"victoriametrics": {"url": "ftp://vm.test"}}, "victoriametrics.url must be an http"),
        (
            {"victoriametrics": {"url": "http://vm.test:port"}},
            "victoriametrics.url must be an http",
        ),
        (
            {"homeassistant": {"url": "http://user:secret@ha.test"}},
            "homeassistant.url must not contain a user name or password",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_a_bad_setting_is_refused(tmp_path, sections, message):
    with pytest.raises(ConfigError, match=message):
        write_config(tmp_path, **sections)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize(
    ("section", "setting"),
    [("decision", "coast_minutes"), ("decision", "min_hold_minutes"), ("comfort", "differential")],
)
def test_a_number_that_is_not_finite_is_refused(tmp_path, section, setting, value):
    with pytest.raises(ConfigError, match=f"{section}.{setting} must be a finite number"):
        write_config(tmp_path, **{section: {setting: value}})


@pytest.mark.parametrize("hours", [1, 4, 48])
def test_the_horizon_can_be_changed_without_touching_the_other_settings(tmp_path, hours):
    # The defaults of the other look-aheads may reach further than a short horizon.
    assert write_config(tmp_path, decision={"horizon_hours": hours}).decision.horizon_hours == hours


@pytest.mark.parametrize(
    ("unit", "comfort"),
    [
        ("°C", {"differential": 0.5, "targets": {"Low": 5.0, "High": 35.0}}),
        ("°C", {"differential": 6.0, "targets": {"Home": 20.0}}),
        ("°F", {"differential": 0.9, "targets": {"Low": 41.0, "High": 95.0}}),
        ("°F", {"differential": 10.8, "targets": {"Home": 68.0}}),
    ],
)
def test_the_limits_on_comfort_settings_are_themselves_allowed(tmp_path, unit, comfort):
    config = write_config(tmp_path, units={"temperature": unit}, comfort=comfort)

    assert config.comfort.differential == comfort["differential"]


@pytest.mark.parametrize("model_path", ["kept.json", "elsewhere/../kept.json"])
def test_the_model_and_the_state_cannot_share_a_file(tmp_path, model_path):
    path = write_settings(tmp_path)
    path.write_text(f'model_path = "{model_path}"\nstate_path = "kept.json"\n' + path.read_text())

    with pytest.raises(ConfigError, match="model_path and state_path must be two different files"):
        load_config(path)


def test_a_password_in_an_address_is_not_repeated_in_the_error(tmp_path):
    # HVP-013
    with pytest.raises(ConfigError) as refused:
        write_config(tmp_path, homeassistant={"url": "http://user:secret@ha.test"})

    assert "secret" not in str(refused.value)


def test_a_section_has_to_be_a_table(tmp_path):
    path = write_settings(tmp_path)
    path.write_text("forecast = 3\n" + path.read_text())

    with pytest.raises(ConfigError, match=r"\[forecast\] must be a table"):
        load_config(path)


def test_a_missing_file_is_reported(tmp_path):
    with pytest.raises(ConfigError, match=r"cannot read .*missing.toml"):
        load_config(tmp_path / "missing.toml")


def test_invalid_toml_is_reported(tmp_path):
    path = tmp_path / "broken.toml"
    path.write_text("[comfort\n")

    with pytest.raises(ConfigError, match="is not valid TOML"):
        load_config(path)
