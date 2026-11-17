import dataclasses

import numpy as np
import pytest
from fakes import (
    ALL_DRIVERS,
    COOL_PAIR,
    HEAT_PAIR,
    HOLD,
    NOON,
    NOW,
    FakeHome,
    FakeMetrics,
    hourly_forecast,
    write_config,
)
from house import STEP_SECONDS, simulate_house

from hvac_predict.advisor import Advice, Steered, advise, steer
from hvac_predict.errors import ConfigError, DataError, ServiceError
from hvac_predict.files import held_by_one_run
from hvac_predict.state import Change, Choice, State, load_state, save_state
from hvac_predict.thermal import FEATURES, ThermalModel
from hvac_predict.thermostat import LeftAlone, Thermostat, read_thermostat
from hvac_predict.training import TrainedModel, model_inputs, save_model, train

HISTORY_POINTS = 12 * 12 + 1  # twelve hours at five-minute steps
COLD_FORECAST = hourly_forecast(12, temperature=5.0)
MILD_FORECAST = hourly_forecast(12, temperature=21.0)
# Each five-minute step closes this share of the gap to outdoors when the envelope weight is 0.1.
KEPT = 1 - 0.1 / 12


def trained(*, light_scale: float | None = None, **weights: float) -> TrainedModel:
    return TrainedModel(
        model=ThermalModel(dict.fromkeys(FEATURES, 0.0) | weights, STEP_SECONDS, 3.0),
        light_scale=light_scale,
        inputs={},
        trained_at="",
        history_days=0.0,
        windows=0,
        rate_error=0.0,
        holdout=[],
    )


# A house that closes a tenth of its gap to outdoors every hour and feels nothing else.
LEAKY = trained(envelope=0.1)


def readings(value: float, *, latest: float | None = None, latest_count: int = 2) -> np.ndarray:
    """Return a steady series, optionally ending in a few different readings."""
    series = np.full(HISTORY_POINTS, value)
    if latest is not None:
        series[-latest_count:] = latest
    return series


def advice_for(
    tmp_path,
    home: FakeHome,
    *,
    indoor: np.ndarray,
    outdoor: np.ndarray,
    drivers: dict[str, np.ndarray] | None = None,
    settings: dict[str, dict] | None = None,
    model: TrainedModel = LEAKY,
    previous: State | None = None,
    now: float = NOW,
) -> Advice:
    """Ask for advice about a thermostat the tool may steer.

    ``drivers`` holds the series of any drivers besides the outdoor
    temperature, and ``settings`` holds sections of the configuration.
    """
    drivers = drivers or {}
    config = write_config(tmp_path, history={name: name for name in drivers}, **(settings or {}))
    metrics = FakeMetrics({"indoor": indoor, "outdoor": outdoor, **drivers}, end=now)
    thermostat = read_thermostat(config, home)
    assert isinstance(thermostat, Thermostat)
    return advise(config, model, metrics, home, thermostat, previous, now=now)


def test_heats_to_the_target_when_the_house_would_get_cold(tmp_path):
    # HVP-004
    home = FakeHome(COLD_FORECAST, setpoints=COOL_PAIR)

    advice = advice_for(tmp_path, home, indoor=readings(21.0), outdoor=readings(5.0))

    assert advice.thermostat.comfort == "Home"
    assert advice.goal.target == 21.0
    assert advice.decision.preference == "heat"
    assert advice.decision.setpoints == HEAT_PAIR
    assert advice.thermostat.setpoints == COOL_PAIR
    assert advice.calls_for_a_change


def test_cools_to_the_target_when_the_house_would_get_hot(tmp_path):
    home = FakeHome(hourly_forecast(12, temperature=35.0), setpoints=HEAT_PAIR)

    advice = advice_for(tmp_path, home, indoor=readings(21.0), outdoor=readings(35.0))

    assert advice.decision.preference == "cool"
    assert advice.decision.setpoints == COOL_PAIR


@pytest.mark.parametrize(("indoor", "preference"), [(21.6, "heat"), (20.4, "cool")])
def test_a_house_that_stays_near_the_target_gets_the_pair_it_sits_in(tmp_path, indoor, preference):
    # HVP-005
    advice = advice_for(
        tmp_path, FakeHome(MILD_FORECAST), indoor=readings(indoor), outdoor=readings(21.0)
    )

    assert (advice.decision.preference, advice.decision.rule) == (preference, "resting")


@pytest.mark.parametrize(
    ("holds", "needs_changing"),
    [
        (HEAT_PAIR, False),
        ((21.1, 23.1), False),  # a thermostat rounds what it is sent
        ((21.25, 23.25), True),
        ((21.0, 23.5), True),
        (COOL_PAIR, True),
        ((None, None), True),
    ],
)
def test_the_thermostat_is_changed_only_when_it_holds_something_else(
    tmp_path, holds, needs_changing
):
    home = FakeHome(COLD_FORECAST, setpoints=holds)

    advice = advice_for(tmp_path, home, indoor=readings(21.0), outdoor=readings(5.0))

    assert advice.decision.setpoints == HEAT_PAIR
    assert advice.calls_for_a_change is needs_changing


def test_a_thermostat_shown_in_another_unit_is_compared_in_the_units_of_the_history(tmp_path):
    # The history is in °C and this thermostat is shown in °F: 69.8 to 73.4 is 21 to 23.
    home = FakeHome(COLD_FORECAST, unit="°F", setpoints=(69.8, 73.4), reads=69.8)

    advice = advice_for(tmp_path, home, indoor=readings(21.0), outdoor=readings(5.0))

    assert advice.thermostat.setpoints == pytest.approx(HEAT_PAIR)
    assert not advice.calls_for_a_change


# Settling on a new target: the schedule has just moved from Home (21) to Sleep (18).


def seen(
    target: float,
    *,
    changed_minutes_ago: float | None = None,
    started_from: float = 21.0,
    chose: str | None = None,
    chosen_minutes_ago: float = 0.0,
) -> State:
    """Return what a run five minutes ago would have recorded."""
    return State(
        target,
        seen_at=NOW - 300,
        change=None
        if changed_minutes_ago is None
        else Change(NOW - 60 * changed_minutes_ago, started_from),
        choice=None if chose is None else Choice(chose, NOW - 60 * chosen_minutes_ago),
    )


def test_a_new_target_is_noticed_and_starts_the_clock(tmp_path):
    home = FakeHome(COLD_FORECAST, comfort="Sleep")

    advice = advice_for(
        tmp_path, home, indoor=readings(21.0), outdoor=readings(5.0), previous=seen(21.0)
    )

    assert advice.goal.target == 18.0
    assert advice.state == State(18.0, seen_at=NOW, change=Change(at=NOW, started_from=21.0))
    assert advice.decision.rule == "drive"


@pytest.mark.parametrize(
    ("outdoor", "rule", "pair"),
    [
        # HVP-003. At -5 outside the house is within reach of 18 in 70 minutes; at 5, in 115.
        pytest.param(-5.0, "drift", (18.0, 23.0), id="cools-itself-in-time"),
        pytest.param(5.0, "drive", (16.0, 18.0), id="too-slow-so-the-air-conditioner-helps"),
    ],
)
def test_after_a_target_drop_the_house_cools_itself_only_if_that_is_quick_enough(
    tmp_path, outdoor, rule, pair
):
    home = FakeHome(hourly_forecast(12, temperature=outdoor), comfort="Sleep")

    advice = advice_for(
        tmp_path, home, indoor=readings(21.0), outdoor=readings(outdoor), previous=seen(21.0)
    )

    assert (advice.decision.rule, advice.decision.setpoints) == (rule, pair)


def test_the_time_to_settle_runs_from_the_change_and_not_from_now(tmp_path):
    # HVP-003. The house needs 70 minutes, and 40 of the 90 are already gone.
    home = FakeHome(hourly_forecast(12, temperature=-5.0), comfort="Sleep")

    advice = advice_for(
        tmp_path,
        home,
        indoor=readings(21.0),
        outdoor=readings(-5.0),
        previous=seen(18.0, changed_minutes_ago=40),
    )

    assert advice.decision.preference == "cool"
    assert advice.decision.reason.endswith("in the 50 min it has left.")


def test_a_house_still_short_of_the_target_when_time_is_up_is_driven_there(tmp_path):
    # HVP-003. At 0 outside the house would get from 19.5 to 18 in under an hour: too late.
    home = FakeHome(hourly_forecast(12, temperature=0.0), comfort="Sleep", reads=19.5)

    advice = advice_for(
        tmp_path,
        home,
        indoor=readings(19.5),
        outdoor=readings(0.0),
        previous=seen(18.0, changed_minutes_ago=200),
    )

    assert advice.decision.setpoints == (16.0, 18.0)
    assert advice.decision.reason.startswith("The house has had its time to cool to")


def test_a_drift_keeps_clear_of_where_the_house_started_and_not_of_where_it_is_now(tmp_path):
    # HVP-015. The house started from 21 and has cooled to 19.5, with ten minutes to go.
    home = FakeHome(hourly_forecast(12, temperature=-5.0), comfort="Sleep", reads=19.5)

    advice = advice_for(
        tmp_path,
        home,
        indoor=readings(19.5),
        outdoor=readings(-5.0),
        previous=seen(18.0, changed_minutes_ago=40),
    )

    assert advice.decision.setpoints == (18.0, 23.0)


@pytest.mark.parametrize(
    ("started_from", "pair"),
    [
        # HVP-018. The configured pairs keep a house between 16 and 23.
        pytest.param(49.0, (18.0, 25.0), id="a-start-no-pair-would-allow"),
        pytest.param(23.4, (18.0, 25.0), id="just-outside"),
        pytest.param(22.9, (18.0, 25.0), id="just-inside"),
        pytest.param(21.9, (18.0, 24.0), id="well-inside"),
    ],
)
def test_the_idle_setpoint_of_a_drift_goes_no_further_than_a_configured_pair_allows(
    tmp_path, started_from, pair
):
    home = FakeHome(hourly_forecast(12, temperature=-5.0), comfort="Sleep")

    advice = advice_for(
        tmp_path,
        home,
        indoor=readings(21.0),
        outdoor=readings(-5.0),
        previous=seen(18.0, changed_minutes_ago=5, started_from=started_from),
    )

    assert advice.decision.setpoints == pair


def test_the_idle_setpoint_of_a_warming_drift_is_limited_the_same_way(tmp_path):
    # HVP-018. The schedule moved from Sleep (18) to Home (21), and a warm day does the rest.
    home = FakeHome(hourly_forecast(12, temperature=45.0), comfort="Home", reads=18.0)

    def pair_from(started_from: float):
        return advice_for(
            tmp_path,
            home,
            indoor=readings(18.0),
            outdoor=readings(45.0),
            previous=seen(21.0, changed_minutes_ago=5, started_from=started_from),
        ).decision.setpoints

    assert pair_from(-19.0) == (14.0, 21.0)
    assert pair_from(16.1) == (14.0, 21.0)
    assert pair_from(17.1) == (15.0, 21.0)


def test_a_house_that_has_reached_the_target_has_settled(tmp_path):
    # HVP-004. It cooled from 21 through 18 and is still going: now it needs heat.
    home = FakeHome(COLD_FORECAST, comfort="Sleep", reads=18.0)
    indoor = readings(21.0, latest=17.9, latest_count=1)

    advice = advice_for(
        tmp_path,
        home,
        indoor=indoor,
        outdoor=readings(5.0),
        previous=seen(18.0, changed_minutes_ago=40),
    )

    assert advice.state.change is None
    assert (advice.decision.preference, advice.decision.rule) == ("heat", "overshoot")


def test_a_house_a_hair_short_of_the_target_has_reached_it(tmp_path):
    # A tenth of the 2 degree differential is close enough: 18.2 counts, 18.3 does not.
    home = FakeHome(MILD_FORECAST, comfort="Sleep", reads=18.0)

    def arrived_at(reading: float) -> bool:
        advice = advice_for(
            tmp_path,
            home,
            indoor=readings(21.0, latest=reading, latest_count=1),
            outdoor=readings(21.0),
            previous=seen(18.0, changed_minutes_ago=40),
        )
        return advice.state.change is None

    assert arrived_at(18.2)
    assert not arrived_at(18.3)


def test_a_house_that_settled_earlier_is_not_driven_back_when_it_wanders(tmp_path):
    # The target has not changed since the house reached it, and the house is now at 19.5.
    home = FakeHome(hourly_forecast(12, temperature=19.5), comfort="Sleep", reads=19.5)

    advice = advice_for(
        tmp_path, home, indoor=readings(19.5), outdoor=readings(19.5), previous=seen(18.0)
    )

    assert advice.decision.rule == "resting"
    assert advice.decision.setpoints == (18.0, 20.0)


def test_a_change_seen_after_a_long_silence_has_already_used_its_time(tmp_path):
    # The last run was 100 minutes ago, at the Home target. The house would take 125 minutes.
    home = FakeHome(COLD_FORECAST, comfort="Sleep")
    long_ago = dataclasses.replace(seen(21.0), seen_at=NOW - 6000)

    advice = advice_for(
        tmp_path, home, indoor=readings(21.0), outdoor=readings(5.0), previous=long_ago
    )

    assert advice.state.change == Change(at=NOW - 6000, started_from=21.0)
    assert advice.decision.setpoints == (16.0, 18.0)
    assert advice.decision.reason.startswith("The house has had its time to cool to")


# The hold between changes of preference.

# The house sits just below the target, which alone would mean the cool pair.
JUST_BELOW = {"indoor": readings(20.4), "outdoor": readings(21.0)}


def test_a_choice_made_minutes_ago_is_kept(tmp_path):
    # HVP-006
    home = FakeHome(MILD_FORECAST, setpoints=HEAT_PAIR)
    previous = seen(21.0, chose="heat", chosen_minutes_ago=10)

    advice = advice_for(tmp_path, home, **JUST_BELOW, previous=previous)

    assert (advice.decision.preference, advice.decision.rule) == ("heat", "hold")
    assert "chosen 10 min ago" in advice.decision.reason
    assert not advice.calls_for_a_change


def test_a_cool_preference_chosen_minutes_ago_is_kept_too(tmp_path):
    home = FakeHome(MILD_FORECAST, setpoints=COOL_PAIR)
    previous = seen(21.0, chose="cool", chosen_minutes_ago=10)

    advice = advice_for(
        tmp_path, home, indoor=readings(21.6), outdoor=readings(21.0), previous=previous
    )

    assert (advice.decision.preference, advice.decision.rule) == ("cool", "hold")


def test_a_choice_made_long_ago_can_be_changed(tmp_path):
    home = FakeHome(MILD_FORECAST, setpoints=HEAT_PAIR)
    previous = seen(21.0, chose="heat", chosen_minutes_ago=45)

    advice = advice_for(tmp_path, home, **JUST_BELOW, previous=previous)

    assert (advice.decision.preference, advice.decision.rule) == ("cool", "resting")


@pytest.mark.parametrize("holds", [COOL_PAIR, (17.0, 25.0), (None, None)])
def test_a_choice_the_thermostat_no_longer_holds_is_not_kept(tmp_path, holds):
    # Somebody, or the schedule, has moved the setpoints since the tool chose the heat pair.
    home = FakeHome(MILD_FORECAST, setpoints=holds)
    previous = seen(21.0, chose="heat", chosen_minutes_ago=10)

    advice = advice_for(tmp_path, home, **JUST_BELOW, previous=previous)

    assert (advice.decision.preference, advice.decision.rule) == ("cool", "resting")


def test_a_pair_the_tool_did_not_choose_is_not_kept(tmp_path):
    home = FakeHome(MILD_FORECAST, setpoints=HEAT_PAIR)

    advice = advice_for(tmp_path, home, **JUST_BELOW, previous=seen(21.0))

    assert (advice.decision.preference, advice.decision.rule) == ("cool", "resting")


def test_a_drift_set_minutes_ago_gives_way_when_the_house_turns_out_too_slow(tmp_path):
    # HVP-003. Ten minutes ago the tool let the house drift; now the forecast is milder.
    home = FakeHome(COLD_FORECAST, comfort="Sleep", setpoints=(18.0, 23.0))
    previous = seen(18.0, changed_minutes_ago=10, chose="heat", chosen_minutes_ago=10)

    advice = advice_for(
        tmp_path, home, indoor=readings(21.0), outdoor=readings(5.0), previous=previous
    )

    assert (advice.decision.rule, advice.decision.setpoints) == ("drive", (16.0, 18.0))


def test_a_choice_made_minutes_ago_does_not_hold_up_a_house_that_will_get_cold(tmp_path):
    # HVP-004. The tool set the cool pair ten minutes ago, and the house is now heading below 19.
    home = FakeHome(COLD_FORECAST, setpoints=COOL_PAIR)
    previous = seen(21.0, chose="cool", chosen_minutes_ago=10)

    advice = advice_for(
        tmp_path, home, indoor=readings(21.0), outdoor=readings(5.0), previous=previous
    )

    assert (advice.decision.rule, advice.decision.setpoints) == ("overshoot", HEAT_PAIR)


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


# The prediction the decision rests on.


def test_predicts_the_house_closing_on_the_forecast_temperature_step_by_step(tmp_path):
    outlook = advice_for(
        tmp_path, FakeHome(COLD_FORECAST), indoor=readings(21.0), outdoor=readings(5.0)
    ).outlook

    assert outlook.minutes.tolist() == [5.0 * step for step in range(97)]
    assert outlook.indoor == pytest.approx(5.0 + 16.0 * KEPT ** np.arange(97))
    assert outlook.outdoor == pytest.approx(5.0)


def test_carries_the_gap_between_sensor_and_forecast_into_the_prediction(tmp_path):
    outlook = advice_for(
        tmp_path, FakeHome(COLD_FORECAST), indoor=readings(21.0), outdoor=readings(9.0)
    ).outlook

    assert outlook.outdoor[0] == 9.0
    assert outlook.outdoor[12] == pytest.approx(5.0 + 4.0 * np.exp(-1 / 3))
    assert outlook.outdoor[-1] == pytest.approx(5.0 + 4.0 * np.exp(-8 / 3))


def test_looks_as_far_ahead_as_it_is_told_to(tmp_path):
    outlook = advice_for(
        tmp_path,
        FakeHome(COLD_FORECAST),
        indoor=readings(21.0),
        outdoor=readings(5.0),
        settings={"decision": {"horizon_hours": 4.0, "overshoot_minutes": 240.0}},
    ).outlook

    assert outlook.minutes[-1] == 240


def test_looks_no_further_ahead_than_the_forecast_reaches(tmp_path):
    home = FakeHome(hourly_forecast(3, temperature=5.0))

    outlook = advice_for(tmp_path, home, indoor=readings(21.0), outdoor=readings(5.0)).outlook

    assert outlook.minutes[-1] == 150


def test_refuses_a_forecast_shorter_than_an_hour(tmp_path):
    home = FakeHome(hourly_forecast(1, temperature=5.0))

    with pytest.raises(DataError, match="reaches less than 1 h ahead"):
        advice_for(tmp_path, home, indoor=readings(21.0), outdoor=readings(5.0))


@pytest.mark.parametrize("stale", ["indoor", "outdoor", "wind"])
def test_refuses_to_act_on_readings_more_than_twenty_minutes_old(tmp_path, stale):
    # HVP-008
    series = {"indoor": readings(21.0), "outdoor": readings(5.0), "wind": readings(3.0)}

    def advice_with(gap_steps: int) -> Advice:
        gappy = series | {stale: readings(21.0, latest=np.nan, latest_count=gap_steps)}
        return advice_for(
            tmp_path,
            FakeHome(COLD_FORECAST),
            indoor=gappy["indoor"],
            outdoor=gappy["outdoor"],
            drivers={"wind": gappy["wind"]},
        )

    advice_with(4)
    with pytest.raises(DataError, match=f"no believable {stale} reading from the last 20 min"):
        advice_with(5)


def test_runs_on_from_a_reading_that_is_a_few_minutes_old(tmp_path):
    indoor = readings(21.0, latest=np.nan)

    outlook = advice_for(
        tmp_path, FakeHome(COLD_FORECAST), indoor=indoor, outdoor=readings(5.0)
    ).outlook

    # The newest reading is two steps old, so the house has moved on two steps by now.
    assert outlook.indoor[0] == pytest.approx(5.0 + 16.0 * KEPT**2)
    assert outlook.indoor[1] == pytest.approx(5.0 + 16.0 * KEPT**3)


def test_a_new_target_seen_while_the_newest_reading_is_missing_starts_from_the_prediction(
    tmp_path,
):
    # HVP-015. The drift pair has to clear the house even when its last sample has not arrived.
    home = FakeHome(hourly_forecast(12, temperature=-5.0), comfort="Sleep")
    indoor = readings(21.0, latest=np.nan, latest_count=1)

    advice = advice_for(tmp_path, home, indoor=indoor, outdoor=readings(-5.0), previous=seen(21.0))

    assert advice.state.change == Change(at=NOW, started_from=pytest.approx(-5.0 + 26.0 * KEPT))
    assert advice.decision.setpoints == (18.0, 23.0)


@pytest.mark.parametrize("glitch", [85.0, -127.0])
def test_a_reading_no_sensor_could_mean_is_treated_as_missing(tmp_path, glitch):
    # HVP-009
    advice = advice_for(
        tmp_path,
        FakeHome(COLD_FORECAST),
        indoor=readings(21.0, latest=glitch),
        outdoor=readings(5.0),
    )

    assert advice.outlook.indoor[0] == pytest.approx(5.0 + 16.0 * KEPT**2)
    assert advice.decision.preference == "heat"


def test_what_the_equipment_is_doing_now_does_not_carry_into_the_prediction(tmp_path):
    furnace = trained(envelope=0.1, heating=5.0)

    outlook = advice_for(
        tmp_path,
        FakeHome(COLD_FORECAST),
        indoor=readings(21.0),
        outdoor=readings(5.0),
        drivers={"heating": readings(1.0)},
        model=furnace,
    ).outlook

    assert outlook.indoor == pytest.approx(5.0 + 16.0 * KEPT ** np.arange(97))


def test_the_equipment_counts_for_the_minutes_since_the_newest_reading(tmp_path):
    furnace = trained(envelope=0.1, heating=5.0)

    outlook = advice_for(
        tmp_path,
        FakeHome(COLD_FORECAST, reads=22.0),
        indoor=readings(21.0, latest=np.nan),
        outdoor=readings(5.0),
        drivers={"heating": readings(1.0)},
        model=furnace,
    ).outlook

    temperature = 21.0
    for _ in range(2):
        temperature += (0.1 * (5.0 - temperature) + 5.0) / 12
    assert outlook.indoor[0] == pytest.approx(temperature)
    assert outlook.indoor[1] == pytest.approx(temperature + 0.1 * (5.0 - temperature) / 12)


def test_forecast_wind_is_scaled_before_the_model_sees_it(tmp_path):
    draughty = trained(envelope=0.05, wind=0.01)
    home = FakeHome(hourly_forecast(12, temperature=5.0, wind_speed=20.0))

    outlook = advice_for(
        tmp_path,
        home,
        indoor=readings(21.0),
        outdoor=readings(5.0),
        drivers={"wind": readings(20.0)},
        model=draughty,
        settings={"forecast": {"wind_scale": 0.5}},
    ).outlook

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
        forecast = hourly_forecast(8, start=NOON, temperature=5.0, cloud_coverage=cloud_coverage)
        return advice_for(
            tmp_path,
            FakeHome(forecast),
            indoor=readings(21.0),
            outdoor=readings(5.0),
            drivers=drivers,
            model=sunny_house,
            now=NOON,
            settings={"decision": {"horizon_hours": 4.0, "overshoot_minutes": 240.0}},
        ).outlook.indoor[-1]

    clear, overcast = end_of_afternoon(0), end_of_afternoon(100)

    if light_scale is None:
        assert clear == overcast
    else:
        assert clear > overcast + 1.0


def test_refuses_a_prediction_no_house_could_follow(tmp_path):
    runaway = trained(bias=50.0)

    with pytest.raises(DataError, match="leaves the range a house can be in"):
        advice_for(
            tmp_path,
            FakeHome(COLD_FORECAST),
            indoor=readings(21.0),
            outdoor=readings(5.0),
            model=runaway,
        )


@pytest.mark.parametrize(
    ("reads", "unit"), [(21.0, "°C"), (23.9, "°C"), (18.1, "°C"), (69.8, "°F")]
)
def test_accepts_a_thermostat_that_agrees_with_the_history(tmp_path, reads, unit):
    home = FakeHome(COLD_FORECAST, reads=reads, unit=unit)

    advice_for(tmp_path, home, indoor=readings(21.0), outdoor=readings(5.0))


@pytest.mark.parametrize(("reads", "unit"), [(17.5, "°C"), (24.5, "°C"), (60.0, "°F")])
def test_refuses_to_act_when_the_thermostat_and_the_history_disagree(tmp_path, reads, unit):
    # HVP-010
    home = FakeHome(COLD_FORECAST, reads=reads, unit=unit)

    with pytest.raises(DataError, match="not acting on readings that disagree"):
        advice_for(tmp_path, home, indoor=readings(21.0), outdoor=readings(5.0))


def test_a_history_in_fahrenheit_is_allowed_the_same_disagreement(tmp_path):
    # HVP-010. Three degrees Celsius is 5.4 °F: 73.3 is within it of 68, and 73.5 is not.
    fahrenheit = {
        "units": {"temperature": "°F"},
        "comfort": {"differential": 3.0, "targets": {"Home": 68.0}},
    }

    def advice_when_it_reads(reads: float) -> Advice:
        return advice_for(
            tmp_path,
            FakeHome(MILD_FORECAST, unit="°F", reads=reads),
            indoor=readings(68.0),
            outdoor=readings(69.8),
            settings=fahrenheit,
        )

    advice_when_it_reads(73.3)
    with pytest.raises(
        DataError, match=r"puts the house at 68\.0 °F but climate\.house reads 73\.5"
    ):
        advice_when_it_reads(73.5)


@pytest.mark.parametrize("reads", [None, "21", float("inf")])
def test_refuses_to_act_when_the_thermostat_has_no_reading_to_check_against(tmp_path, reads):
    # HVP-010
    home = FakeHome(COLD_FORECAST, reads=reads)

    with pytest.raises(DataError, match="reports no temperature of its own"):
        advice_for(tmp_path, home, indoor=readings(21.0), outdoor=readings(5.0))


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
    home = FakeHome(forecast, reads=float(house.indoor[now_index]))
    thermostat = read_thermostat(config, home)
    assert isinstance(thermostat, Thermostat)
    outlook = advise(config, model, FakeMetrics(known), home, thermostat, None, now=NOW).outlook

    left_alone = house.indoor[now_index:]
    assert outlook.indoor == pytest.approx(left_alone, abs=0.1)
    assert abs(left_alone[-1] - left_alone[0]) > 2.0
