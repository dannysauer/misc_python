import dataclasses

import numpy as np
import pytest

from hvac_predict.config import DecisionSettings
from hvac_predict.decision import COOL, HEAT, Goal, Outlook, Settling, Standing, decide

SETTINGS = DecisionSettings(overshoot_minutes=480.0, min_hold_minutes=30.0)
MINUTES = np.arange(0, 481, 5.0)
# The owner's own example: a night-time target of 64 °F and pairs 3 °F wide.
GOAL = Goal(target=64.0, differential=3.0)


def outlook(*points: tuple[float, float]) -> Outlook:
    """Predict an indoor temperature that passes through (minutes, temperature) points."""
    indoor = np.interp(MINUTES, *zip(*points, strict=True))
    return Outlook(MINUTES, indoor, np.zeros(len(MINUTES)))


def settling(prediction: Outlook, minutes_left: float, started_from: float | None = None, **more):
    """Decide for a house on its way to a new target, which it set out for from where it is."""
    start = float(prediction.indoor[0]) if started_from is None else started_from
    return decide(prediction, GOAL, SETTINGS, settling=Settling(minutes_left, start), **more)


def settled(prediction: Outlook, settings: DecisionSettings = SETTINGS, **more):
    """Decide for a house that has been at the target."""
    return decide(prediction, GOAL, settings, **more)


def test_the_two_pairs_sit_on_either_side_of_the_target():
    # HVP-002
    assert GOAL.pair(HEAT) == (64.0, 67.0)
    assert GOAL.pair(COOL) == (61.0, 64.0)
    assert GOAL.range == (61.0, 67.0)


def test_a_tenth_of_the_differential_from_the_target_is_there():
    assert GOAL.tolerance == pytest.approx(0.3)


# Rule 1: a house that has not reached a newly changed target.

# A house has arrived once it is within 0.3 of the target, so these get there at 63.7 or 64.3.
COOLS_TO_TARGET_IN_70_MIN = outlook((0, 68), (70, 64.3), (480, 58))
COOLS_TO_TARGET_IN_150_MIN = outlook((0, 68), (150, 64.3), (480, 62))
WARMS_TO_TARGET_IN_60_MIN = outlook((0, 60), (60, 63.7), (480, 66))
STAYS_COLD = outlook((0, 60), (480, 60))
LEVELS_OFF_JUST_SHORT = outlook((0, 68), (60, 64.2), (480, 64.2))
LEVELS_OFF_TOO_FAR_SHORT = outlook((0, 68), (60, 64.4), (480, 64.4))


@pytest.mark.parametrize(
    ("prediction", "minutes_left", "expected"),
    [
        # HVP-003: at 9 pm the target drops to 64 with the house at 68.
        pytest.param(COOLS_TO_TARGET_IN_70_MIN, 90.0, HEAT, id="cools-itself-in-time-so-drift"),
        pytest.param(COOLS_TO_TARGET_IN_150_MIN, 90.0, COOL, id="too-slow-so-cool-it"),
        pytest.param(COOLS_TO_TARGET_IN_70_MIN, 70.0, HEAT, id="arrives-exactly-on-time"),
        pytest.param(COOLS_TO_TARGET_IN_70_MIN, 65.0, COOL, id="five-minutes-too-slow"),
        pytest.param(COOLS_TO_TARGET_IN_70_MIN, 0.0, COOL, id="time-is-up"),
        pytest.param(WARMS_TO_TARGET_IN_60_MIN, 90.0, COOL, id="warms-itself-in-time-so-drift"),
        pytest.param(WARMS_TO_TARGET_IN_60_MIN, 30.0, HEAT, id="too-slow-so-heat-it"),
        pytest.param(STAYS_COLD, 90.0, HEAT, id="never-gets-there-so-heat-it"),
        pytest.param(LEVELS_OFF_JUST_SHORT, 90.0, HEAT, id="near-enough-counts-as-there"),
        pytest.param(LEVELS_OFF_TOO_FAR_SHORT, 90.0, COOL, id="not-near-enough-so-cool-it"),
    ],
)
def test_a_house_settling_on_a_new_target_drifts_there_only_if_it_arrives_in_time(
    prediction, minutes_left, expected
):
    decision = settling(prediction, minutes_left)
    drifting = (expected == HEAT) == (prediction.indoor[0] > GOAL.target)

    assert decision.preference == expected
    assert decision.rule == ("drift" if drifting else "drive")


def test_settling_comes_before_the_house_being_far_outside_the_range():
    # 70 is twice the differential above 64, yet the house gets there unaided in time.
    far_above = outlook((0, 70), (80, 64), (480, 58))

    assert settling(far_above, 90.0).preference == HEAT


@pytest.mark.parametrize(
    ("preference", "clear_of", "pair"),
    [
        # HVP-015: from 68 the usual pair, 64 to 67, would start the air conditioner.
        pytest.param(HEAT, 68.0, (64.0, 71.0), id="cooling-from-68"),
        pytest.param(HEAT, 68.4, (64.0, 72.0), id="cooling-from-68.4-rounds-outwards"),
        pytest.param(HEAT, 64.5, (64.0, 68.0), id="cooling-from-just-above"),
        pytest.param(HEAT, 63.0, (64.0, 67.0), id="never-nearer-than-the-usual-pair"),
        pytest.param(COOL, 60.0, (57.0, 64.0), id="warming-from-60"),
        pytest.param(COOL, 60.6, (57.0, 64.0), id="warming-from-60.6-rounds-outwards"),
        pytest.param(COOL, 65.0, (61.0, 64.0), id="never-nearer-than-the-usual-pair-below"),
    ],
)
def test_a_drift_pair_keeps_its_idle_setpoint_clear_of_a_temperature(preference, clear_of, pair):
    assert GOAL.drift_pair(preference, clear_of=clear_of) == pair


def test_a_drifting_house_has_the_idle_setpoint_kept_clear_of_where_it_started():
    # HVP-015. The house started from 68 and has cooled to 66 on its way to 64.
    partway = outlook((0, 66), (40, 64), (480, 58))

    assert settling(partway, 60.0, started_from=68.0).setpoints == (64.0, 71.0)
    assert settling(WARMS_TO_TARGET_IN_60_MIN, 90.0, started_from=58.0).setpoints == (55.0, 64.0)


def test_a_driven_house_gets_the_usual_pair():
    assert settling(COOLS_TO_TARGET_IN_150_MIN, 90.0).setpoints == (61.0, 64.0)
    assert settling(STAYS_COLD, 90.0).setpoints == (64.0, 67.0)


@pytest.mark.parametrize(
    ("prediction", "standing", "expected"),
    [
        # HVP-003. The tool let the house drift ten minutes ago, and the prediction has slipped.
        pytest.param(
            COOLS_TO_TARGET_IN_150_MIN, HEAT, (COOL, (61.0, 64.0), "drive"), id="to-drive"
        ),
        pytest.param(COOLS_TO_TARGET_IN_70_MIN, COOL, (HEAT, (64.0, 71.0), "drift"), id="to-drift"),
    ],
)
def test_a_choice_made_minutes_ago_does_not_hold_up_a_house_that_is_settling(
    prediction, standing, expected
):
    decision = settling(prediction, 80.0, standing=Standing(standing, minutes=10.0))

    assert (decision.preference, decision.setpoints, decision.rule) == expected


# Rule 2: a house that will leave the range the two pairs cover, 61 to 67.

FALLS_TO_60 = outlook((0, 63.5), (240, 60), (480, 60))
FALLS_TO_62 = outlook((0, 63.5), (240, 62), (480, 62))
RISES_TO_68 = outlook((0, 64.5), (240, 68), (480, 68))
RISES_TO_66 = outlook((0, 64.5), (240, 66), (480, 66))
HOT_AFTERNOON_THEN_COLD_NIGHT = outlook((0, 64.5), (120, 68), (240, 64), (480, 59))
COLD_MORNING_THEN_HOT_DAY = outlook((0, 63.5), (120, 60), (240, 64), (480, 69))
ALREADY_TOO_WARM = outlook((0, 68), (480, 68))
TOUCHES_THE_EDGES = outlook((0, 64), (120, 67), (360, 61), (480, 64))


@pytest.mark.parametrize(
    ("prediction", "preference", "rule"),
    [
        # HVP-004: it will cool itself more than 3 °F past the target, so heat to the target.
        pytest.param(FALLS_TO_60, HEAT, "overshoot", id="overcools-so-heat"),
        pytest.param(RISES_TO_68, COOL, "overshoot", id="overheats-so-cool"),
        pytest.param(HOT_AFTERNOON_THEN_COLD_NIGHT, COOL, "overshoot", id="first-edge-wins"),
        pytest.param(COLD_MORNING_THEN_HOT_DAY, HEAT, "overshoot", id="first-edge-wins-cold"),
        pytest.param(ALREADY_TOO_WARM, COOL, "overshoot", id="already-past-the-upper-edge"),
        # HVP-005: within 3 °F of the target on its own, so the pair it sits in.
        pytest.param(FALLS_TO_62, COOL, "resting", id="stays-in-range-below-target"),
        pytest.param(RISES_TO_66, HEAT, "resting", id="stays-in-range-above-target"),
        pytest.param(TOUCHES_THE_EDGES, HEAT, "resting", id="the-edges-themselves-are-in-range"),
    ],
)
def test_a_settled_house_gets_the_pair_that_matches_where_it_is_heading(
    prediction, preference, rule
):
    decision = settled(prediction)

    assert (decision.preference, decision.rule) == (preference, rule)


def test_an_overshoot_further_off_than_the_look_ahead_is_left_for_later():
    # FALLS_TO_60 passes 61 after 2 h 55 min.
    three_hours = dataclasses.replace(SETTINGS, overshoot_minutes=180.0)
    two_hours = dataclasses.replace(SETTINGS, overshoot_minutes=120.0)

    assert settled(FALLS_TO_60, three_hours).preference == HEAT
    assert settled(FALLS_TO_60, two_hours).preference == COOL
    assert settled(FALLS_TO_60, two_hours).reason.startswith(
        "Left alone, the house stays within 3 of 64 for the next 2 h 00 min."
    )


def test_with_no_look_ahead_only_a_house_already_out_of_range_counts():
    right_now = dataclasses.replace(SETTINGS, overshoot_minutes=0.0)

    assert settled(FALLS_TO_60, right_now).rule == "resting"
    assert settled(ALREADY_TOO_WARM, right_now).rule == "overshoot"


def test_a_house_exactly_on_target_counts_as_above_it():
    assert settled(outlook((0, 64), (480, 64))).preference == HEAT


def test_a_settled_house_always_gets_one_of_the_two_usual_pairs():
    # HVP-002
    assert settled(FALLS_TO_60).setpoints == (64.0, 67.0)
    assert settled(RISES_TO_68).setpoints == (61.0, 64.0)
    assert settled(RISES_TO_66).setpoints == (64.0, 67.0)
    assert settled(FALLS_TO_62).setpoints == (61.0, 64.0)


# The hold between changes of preference.


def test_a_fresh_choice_is_kept_for_the_hold_time():
    # HVP-006
    kept = settled(FALLS_TO_62, standing=Standing(HEAT, minutes=10.0))

    assert (kept.preference, kept.setpoints, kept.rule) == (HEAT, (64.0, 67.0), "hold")
    assert kept.reason.startswith(
        "Keeping the heat preference because it was chosen 10 min ago. Otherwise cool: "
    )


def test_the_hold_ends_after_the_hold_time():
    assert settled(FALLS_TO_62, standing=Standing(HEAT, minutes=30.0)).preference == COOL


def test_there_is_nothing_to_hold_when_the_thermostat_holds_no_choice_of_the_tool():
    assert settled(FALLS_TO_62, standing=None).preference == COOL


def test_agreeing_with_the_standing_choice_is_not_a_hold():
    assert settled(FALLS_TO_62, standing=Standing(COOL, minutes=1.0)).rule == "resting"


@pytest.mark.parametrize(
    ("prediction", "standing", "expected"),
    [
        # HVP-004. The air conditioner brought the house to 64 ten minutes ago, and it goes on
        # cooling by itself: the furnace has to hold 64, and waiting would let it reach 61.
        pytest.param(FALLS_TO_60, COOL, HEAT, id="will-overcool"),
        pytest.param(RISES_TO_68, HEAT, COOL, id="will-overheat"),
        pytest.param(ALREADY_TOO_WARM, HEAT, COOL, id="already-past-the-edge"),
    ],
)
def test_a_choice_made_minutes_ago_does_not_hold_up_a_house_that_will_overshoot(
    prediction, standing, expected
):
    decision = settled(prediction, standing=Standing(standing, minutes=10.0))

    assert (decision.preference, decision.rule) == (expected, "overshoot")


# What the operator is told.


def test_reasons_say_what_the_house_will_do():
    assert settling(COOLS_TO_TARGET_IN_70_MIN, 90.0).reason == (
        "The target is newly 64, and left alone the house cools to it in 1 h 10 min, "
        "inside the 1 h 30 min it has left."
    )
    assert settling(COOLS_TO_TARGET_IN_70_MIN, 0.0).reason == (
        "The house has had its time to cool to the new target of 64 unaided, and is not there."
    )
    # The prediction covers eight hours, and the house has been given ten.
    assert settling(STAYS_COLD, 600.0).reason == (
        "The target is newly 64, and left alone the house would not warm to it "
        "in the 8 h 00 min the prediction reaches."
    )
    assert settling(COOLS_TO_TARGET_IN_150_MIN, 45.0).reason == (
        "The target is newly 64, and left alone the house would not cool to it "
        "in the 45 min it has left."
    )
    assert settling(WARMS_TO_TARGET_IN_60_MIN, 30.0).reason == (
        "The target is newly 64, and left alone the house would not warm to it "
        "in the 30 min it has left."
    )
    assert settled(FALLS_TO_60).reason == (
        "Left alone, the house drops below 61 in 2 h 55 min, so the equipment will have "
        "to run, and with this pair it holds 64."
    )
    assert settled(ALREADY_TOO_WARM).reason == (
        "Left alone, the house is already past 67, so the equipment will have to run, "
        "and with this pair it holds 64."
    )
    assert settled(FALLS_TO_62).reason == (
        "Left alone, the house stays within 3 of 64 for the next 8 h 00 min. "
        "It is below 64 now, so this pair runs nothing."
    )
