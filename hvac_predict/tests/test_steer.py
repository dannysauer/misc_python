import dataclasses

import pytest
from advising import COLD_FORECAST, LEAKY, MILD_FORECAST, readings
from fakes import COOL_PAIR, HEAT_PAIR, HOLD, NOW, FakeHome, FakeMetrics, write_config

from hvac_predict.advisor import Steered, steer
from hvac_predict.decision import Choice
from hvac_predict.errors import ConfigError, ServiceError
from hvac_predict.files import held_by_one_run
from hvac_predict.state import State, load_state, save_state
from hvac_predict.thermostat import LeftAlone
from hvac_predict.training import model_inputs, save_model

# A whole run: read, decide, set, and leave a record.


def steered(tmp_path, home: FakeHome, *, apply: bool, previous: State | None = None, **settings):
    """Run the tool once on a house at 21 with 5 outside, which needs the heat pair."""
    config = write_config(tmp_path, **settings)
    save_model(dataclasses.replace(LEAKY, inputs=model_inputs(config)), config.model_path)
    if previous is not None:
        save_state(previous, config.state_path)
    metrics = FakeMetrics({"indoor": readings(21.0), "outdoor": readings(5.0)})
    return config, steer(config, metrics, home, now=NOW, apply=apply)


def recorded(config) -> State | None:
    return load_state(config.state_path, now=NOW)


def test_a_run_sets_the_thermostat_and_records_what_it_did(tmp_path):
    # HVP-001
    home = FakeHome(COLD_FORECAST, setpoints=COOL_PAIR)

    config, outcome = steered(tmp_path, home, apply=True)

    assert isinstance(outcome, Steered)
    assert outcome.applied
    assert home.setpoint_changes == [("climate.house", 21.0, 23.0)]
    assert recorded(config) == State(
        21.0, seen_at=NOW, choice=Choice("heat", at=NOW), sent=(HEAT_PAIR,)
    )


def test_a_run_sends_the_pair_in_the_unit_home_assistant_shows(tmp_path):
    home = FakeHome(COLD_FORECAST, unit="°F", setpoints=(66.2, 69.8), reads=69.8)

    steered(tmp_path, home, apply=True)

    assert home.setpoint_changes == [("climate.house", 69.8, 73.4)]


def test_a_dry_run_records_what_it_saw_and_chooses_nothing(tmp_path):
    # HVP-011
    home = FakeHome(COLD_FORECAST, setpoints=COOL_PAIR)

    config, outcome = steered(tmp_path, home, apply=False)

    assert isinstance(outcome, Steered)
    assert not outcome.applied
    assert outcome.advice.calls_for_a_change
    assert home.setpoint_changes == []
    assert recorded(config) == State(21.0, seen_at=NOW)


def test_a_thermostat_that_already_holds_the_pair_is_not_sent_it_again(tmp_path):
    home = FakeHome(COLD_FORECAST, setpoints=HEAT_PAIR)

    config, outcome = steered(tmp_path, home, apply=True)

    assert isinstance(outcome, Steered)
    assert not outcome.applied
    assert home.setpoint_changes == []
    assert recorded(config) == State(21.0, seen_at=NOW)


def test_a_thermostat_left_alone_needs_no_model_or_history_and_leaves_no_record(tmp_path):
    home = FakeHome(COLD_FORECAST, mode="off")
    config = write_config(tmp_path)

    # No model has been trained and VictoriaMetrics holds nothing, and neither is asked for.
    outcome = steer(config, FakeMetrics({}), home, now=NOW, apply=True)

    assert isinstance(outcome, LeftAlone)
    assert home.setpoint_changes == []
    assert not config.state_path.exists()


def test_a_state_that_cannot_be_written_stops_the_run_before_the_thermostat_is_touched(tmp_path):
    # HVP-020
    home = FakeHome(COLD_FORECAST, setpoints=COOL_PAIR)
    (tmp_path / "state.json").mkdir()

    with pytest.raises(ConfigError, match=r"cannot write .*state\.json"):
        steered(tmp_path, home, apply=True)

    assert home.setpoint_changes == []


def test_a_damaged_state_file_is_replaced_and_the_run_goes_on(tmp_path):
    # HVP-016
    home = FakeHome(COLD_FORECAST, setpoints=COOL_PAIR)
    config = write_config(tmp_path)
    config.state_path.write_text('{"target": "warm"}')

    _, outcome = steered(tmp_path, home, apply=True)

    assert isinstance(outcome, Steered)
    assert recorded(config) is not None


# Whose hold it is. A hold on the setpoints alone is the kind the tool places, and also the
# kind a person places at the thermostat. The ecobee may report its own hold ahead of a vacation.

SENT_THE_COOL_PAIR = State(21.0, seen_at=NOW - 600, sent=(COOL_PAIR, (17.0, 21.0)))


def test_a_hold_the_tool_placed_is_its_own_to_replace(tmp_path):
    home = FakeHome(COLD_FORECAST, preset=HOLD, setpoints=COOL_PAIR)

    config, outcome = steered(tmp_path, home, apply=True, previous=SENT_THE_COOL_PAIR)

    assert isinstance(outcome, Steered)
    assert home.setpoint_changes == [("climate.house", 21.0, 23.0)]
    assert recorded(config).sent == (HEAT_PAIR, COOL_PAIR)


def test_the_pair_sent_before_the_last_is_still_the_tools_own(tmp_path):
    # The last send may not have arrived, and then the thermostat still holds the one before.
    home = FakeHome(COLD_FORECAST, preset=HOLD, setpoints=(17.0, 21.0))

    _, outcome = steered(tmp_path, home, apply=True, previous=SENT_THE_COOL_PAIR)

    assert isinstance(outcome, Steered)
    assert home.setpoint_changes == [("climate.house", 21.0, 23.0)]


@pytest.mark.parametrize(
    ("holds", "previous", "said"),
    [
        # HVP-024. A vacation's setpoints, reported behind the tool's own stale hold.
        pytest.param((16.5, 24.5), SENT_THE_COOL_PAIR, "is holding 16.5 to 24.5 °C", id="vacation"),
        pytest.param((20.0, 22.0), SENT_THE_COOL_PAIR, "is holding 20.0 to 22.0 °C", id="by-hand"),
        pytest.param(COOL_PAIR, None, "is holding 19.0 to 21.0 °C", id="nothing-on-record"),
        pytest.param((None, None), SENT_THE_COOL_PAIR, "is on a hold", id="no-setpoints"),
    ],
)
def test_a_hold_the_tool_did_not_place_is_left_alone(tmp_path, holds, previous, said):
    home = FakeHome(COLD_FORECAST, preset=HOLD, setpoints=holds)

    config, outcome = steered(tmp_path, home, apply=True, previous=previous)

    assert outcome == LeftAlone(
        f"The thermostat {said} that this tool has no record of placing. The tool leaves "
        "a hold placed by someone else alone until that hold ends."
    )
    assert home.setpoint_changes == []
    assert recorded(config) == previous


def test_a_dry_run_leaves_a_hold_it_did_not_place_alone_too(tmp_path):
    home = FakeHome(COLD_FORECAST, preset=HOLD, setpoints=(16.5, 24.5))

    config, outcome = steered(tmp_path, home, apply=False, previous=SENT_THE_COOL_PAIR)

    assert isinstance(outcome, LeftAlone)
    assert recorded(config) == SENT_THE_COOL_PAIR


def test_setpoints_are_the_tools_to_set_once_no_hold_is_in_force(tmp_path):
    # The hold has ended and the thermostat is back on its schedule, whatever it holds.
    home = FakeHome(COLD_FORECAST, setpoints=(16.5, 24.5))

    _, outcome = steered(tmp_path, home, apply=True, previous=SENT_THE_COOL_PAIR)

    assert isinstance(outcome, Steered)
    assert home.setpoint_changes == [("climate.house", 21.0, 23.0)]


class RefusingHome(FakeHome):
    """A Home Assistant that fails to pass the setpoints on, until told to work again."""

    refusing = True

    def set_temperature_range(self, entity_id: str, low: float, high: float) -> None:
        if self.refusing:
            raise ServiceError("Home Assistant answered 500 Internal Server Error")
        super().set_temperature_range(entity_id, low, high)


def test_a_send_that_fails_leaves_the_pair_on_record_and_no_choice(tmp_path):
    # Nobody knows whether the pair arrived, so it counts as the tool's own if it turns up.
    home = RefusingHome(COLD_FORECAST, preset=HOLD, setpoints=COOL_PAIR)

    with pytest.raises(ServiceError):
        steered(tmp_path, home, apply=True, previous=SENT_THE_COOL_PAIR)

    config = write_config(tmp_path)
    assert recorded(config) == State(21.0, seen_at=NOW, sent=(HEAT_PAIR, COOL_PAIR))


def test_sends_that_keep_failing_do_not_make_the_tool_disown_its_hold(tmp_path):
    # HVP-024. The thermostat stays on the cool pair the tool sent while Home Assistant refuses
    # the heat pair twice, as it would through an outage. The third run has to steer again.
    home = RefusingHome(COLD_FORECAST, preset=HOLD, setpoints=COOL_PAIR)
    with pytest.raises(ServiceError):
        steered(tmp_path, home, apply=True, previous=SENT_THE_COOL_PAIR)
    with pytest.raises(ServiceError):
        steered(tmp_path, home, apply=True)
    home.refusing = False

    config, outcome = steered(tmp_path, home, apply=True)

    assert isinstance(outcome, Steered)
    assert home.setpoint_changes == [("climate.house", 21.0, 23.0)]
    assert recorded(config).sent == (HEAT_PAIR, COOL_PAIR)


def test_a_send_of_unknown_fate_stays_on_record_until_the_thermostat_shows_another(tmp_path):
    # HVP-024. The heat pair was sent and seemed lost, and the thermostat still shows the cool
    # pair. If the heat pair turns up after all, it has to be known as the tool's own.
    home = RefusingHome(COLD_FORECAST, preset=HOLD, setpoints=COOL_PAIR)
    with pytest.raises(ServiceError):
        steered(tmp_path, home, apply=True, previous=SENT_THE_COOL_PAIR)
    config = write_config(tmp_path)
    assert recorded(config).sent == (HEAT_PAIR, COOL_PAIR)

    late = FakeHome(MILD_FORECAST, preset=HOLD, setpoints=HEAT_PAIR)
    _, outcome = steered(tmp_path, late, apply=True)

    assert isinstance(outcome, Steered)


def test_a_second_run_at_the_same_time_is_refused_and_changes_nothing(tmp_path):
    # Two runs that both read the record and both write it would lose what one of them sent.
    home = FakeHome(COLD_FORECAST, setpoints=COOL_PAIR)
    config = write_config(tmp_path)

    with (
        held_by_one_run(config.state_path),
        pytest.raises(ConfigError, match="another run of hvac-predict is using"),
    ):
        steered(tmp_path, home, apply=True)
    assert home.setpoint_changes == []

    _, outcome = steered(tmp_path, home, apply=True)
    assert isinstance(outcome, Steered)


# The hold between changes of preference, across runs.


def test_setting_a_preference_the_thermostat_had_lost_starts_the_hold_again(tmp_path):
    # HVP-006. The tool chose heat five hours ago; since then the thermostat went back to cool.
    home = FakeHome(COLD_FORECAST, preset=HOLD, setpoints=COOL_PAIR)
    long_ago = dataclasses.replace(SENT_THE_COOL_PAIR, choice=Choice("heat", at=NOW - 18000))

    config, _ = steered(tmp_path, home, apply=True, previous=long_ago)

    assert recorded(config).choice == Choice("heat", at=NOW)


def test_moving_only_the_idle_setpoint_does_not_start_the_hold_again(tmp_path):
    # The thermostat holds the wide pair of a drift that has ended: still the heat preference.
    home = FakeHome(COLD_FORECAST, preset=HOLD, setpoints=(21.0, 26.0))
    chosen = State(
        21.0, seen_at=NOW - 600, choice=Choice("heat", at=NOW - 2400), sent=((21.0, 26.0),)
    )

    config, _ = steered(tmp_path, home, apply=True, previous=chosen)

    assert home.setpoint_changes == [("climate.house", 21.0, 23.0)]
    assert recorded(config).choice == Choice("heat", at=NOW - 2400)


def test_a_preference_the_thermostat_already_had_is_not_recorded_as_the_tools_choice(tmp_path):
    # The schedule's own setpoints already heat to the target, and the tool only tidies the pair.
    home = FakeHome(COLD_FORECAST, setpoints=(21.0, 26.0))

    config, _ = steered(tmp_path, home, apply=True)

    assert home.setpoint_changes == [("climate.house", 21.0, 23.0)]
    assert recorded(config).choice is None
