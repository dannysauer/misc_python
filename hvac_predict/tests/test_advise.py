import dataclasses

import numpy as np
import pytest
from advising import COLD_FORECAST, KEPT, MILD_FORECAST, advice_for, readings, seen
from fakes import COOL_PAIR, HEAT_PAIR, NOW, FakeHome, hourly_forecast

from hvac_predict.advisor import Advice
from hvac_predict.decision import Change
from hvac_predict.errors import DataError
from hvac_predict.state import State


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


# Readings that are missing or cannot be believed.


def test_no_advice_is_given_on_a_reading_more_than_twenty_minutes_old(tmp_path):
    # HVP-008. The outdoor sensor last reported 25 minutes ago.
    outdoor = readings(5.0, latest=np.nan, latest_count=5)

    with pytest.raises(DataError, match="no believable outdoor reading from the last 20 min"):
        advice_for(tmp_path, FakeHome(COLD_FORECAST), indoor=readings(21.0), outdoor=outdoor)


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


# The thermostat's own reading, as a check on the history.


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
