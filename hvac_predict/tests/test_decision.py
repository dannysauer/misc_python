import dataclasses

import numpy as np
import pytest

from hvac_predict.config import DecisionSettings
from hvac_predict.decision import COOL, HEAT, Change, Choice, Goal, Outlook, decide

NOW = 1_780_000_000.0
SETTINGS = DecisionSettings(coast_minutes=90.0, overshoot_minutes=480.0, min_hold_minutes=30.0)
MINUTES = np.arange(0, 481, 5.0)
# The owner's own example: a night-time target of 64 °F and pairs 3 °F wide. The other
# targets run from 58 to 72, so the usual pairs keep the house between 55 and 75.
GOAL = Goal(target=64.0, differential=3.0, reach=(55.0, 75.0))


def outlook(*points: tuple[float, float]) -> Outlook:
    """Predict an indoor temperature that passes through (minutes, temperature) points."""
    indoor = np.interp(MINUTES, *zip(*points, strict=True))
    return Outlook(MINUTES, indoor, np.zeros(len(MINUTES)))


def settling(prediction: Outlook, minutes_left: float, started_from: float | None = None, **more):
    """Decide for a house on its way to a new target, which it set out for from where it is.

    The target changed long enough ago to leave ``minutes_left`` of the 90 the house is given.
    """
    start = float(prediction.indoor[0]) if started_from is None else started_from
    change = Change(at=NOW - 60 * (SETTINGS.coast_minutes - minutes_left), started_from=start)
    return decide(prediction, GOAL, SETTINGS, now=NOW, change=change, **more)


def settled(
    prediction: Outlook,
    settings: DecisionSettings = SETTINGS,
    *,
    chose: str | None = None,
    minutes_ago: float = 10.0,
    holds: str | None = None,
):
    """Decide for a house that has been at the target.

    ``chose`` is the preference the tool set ``minutes_ago``, which the thermostat still
    holds unless ``holds`` says otherwise.
    """
    choice = None if chose is None else Choice(chose, at=NOW - 60 * minutes_ago)
    return decide(prediction, GOAL, settings, now=NOW, choice=choice, holds=holds or chose)


def test_the_two_pairs_sit_on_either_side_of_the_target():
    # HVP-002
    assert GOAL.pair(HEAT) == (64.0, 67.0)
    assert GOAL.pair(COOL) == (61.0, 64.0)
    assert GOAL.range == (61.0, 67.0)


@pytest.mark.parametrize(
    "reach",
    [(62.0, 75.0), (55.0, 66.0), (float("-inf"), float("inf")), (float("nan"), float("nan"))],
)
def test_a_goal_needs_a_reach_that_bounds_its_own_pairs(reach):
    # HVP-018. The reach is the limit on a drift pair, so one that limits nothing is refused.
    with pytest.raises(ValueError, match="does not take in the pairs of 64"):
        Goal(target=64.0, differential=3.0, reach=reach)


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


@pytest.mark.parametrize(
    ("started_from", "pair"),
    [
        # HVP-018. The usual pairs of all the targets keep a house between 55 and 75.
        pytest.param(120.0, (64.0, 78.0), id="a-start-no-pair-would-allow"),
        pytest.param(75.4, (64.0, 78.0), id="just-outside"),
        pytest.param(74.9, (64.0, 78.0), id="just-inside"),
        pytest.param(73.9, (64.0, 77.0), id="well-inside"),
    ],
)
def test_the_idle_setpoint_of_a_drift_goes_no_further_than_the_usual_pairs_reach(
    started_from, pair
):
    decision = settling(COOLS_TO_TARGET_IN_70_MIN, 90.0, started_from=started_from)

    assert (decision.rule, decision.setpoints) == ("drift", pair)


def test_a_warming_drift_is_bounded_at_the_cold_end_of_the_reach():
    # HVP-018
    def pair_from(started_from: float):
        return settling(WARMS_TO_TARGET_IN_60_MIN, 90.0, started_from=started_from).setpoints

    assert pair_from(-40.0) == (52.0, 64.0)
    assert pair_from(54.6) == (52.0, 64.0)
    assert pair_from(56.1) == (53.0, 64.0)


def test_a_start_is_held_to_the_reach_before_it_is_rounded_outwards():
    # HVP-018. Rounding first would let 74.5 stand, and put the idle setpoint at 77.5.
    halves = Goal(target=64.0, differential=3.0, reach=(55.5, 74.5))

    assert halves.drift_pair(HEAT, clear_of=120.0) == (64.0, 78.0)
    assert halves.drift_pair(COOL, clear_of=-40.0) == (52.0, 64.0)


def test_the_time_a_house_is_given_runs_from_the_change():
    # HVP-003. The house needs 70 minutes: enough when 20 of the 90 have gone, not when 25 have.
    def decided(minutes_ago: float):
        change = Change(at=NOW - 60 * minutes_ago, started_from=68.0)
        return decide(COOLS_TO_TARGET_IN_70_MIN, GOAL, SETTINGS, now=NOW, change=change)

    assert decided(20).rule == "drift"
    assert decided(25).rule == "drive"
    assert decided(25).reason.endswith("in the 1 h 05 min it has left.")
    assert decided(500).reason.startswith("The house has had its time to cool to")


def test_time_that_ran_out_long_ago_counts_as_none_left_and_not_as_less_than_none():
    # A house that is already there arrives in no time, which is within no time left.
    already_there = outlook((0, 64.2), (480, 64.2))
    long_ago = Change(at=NOW - 60 * 500, started_from=68.0)

    decision = decide(already_there, GOAL, SETTINGS, now=NOW, change=long_ago)

    assert (decision.rule, decision.setpoints) == ("drift", (64.0, 71.0))


def test_a_driven_house_gets_the_usual_pair():
    assert settling(COOLS_TO_TARGET_IN_150_MIN, 90.0).setpoints == (61.0, 64.0)
    assert settling(STAYS_COLD, 90.0).setpoints == (64.0, 67.0)


@pytest.mark.parametrize(
    ("prediction", "chose", "expected"),
    [
        # HVP-003. The tool let the house drift ten minutes ago, and the prediction has slipped.
        pytest.param(
            COOLS_TO_TARGET_IN_150_MIN, HEAT, (COOL, (61.0, 64.0), "drive"), id="to-drive"
        ),
        pytest.param(COOLS_TO_TARGET_IN_70_MIN, COOL, (HEAT, (64.0, 71.0), "drift"), id="to-drift"),
    ],
)
def test_a_choice_made_minutes_ago_does_not_hold_up_a_house_that_is_settling(
    prediction, chose, expected
):
    chosen = Choice(chose, at=NOW - 600)
    decision = settling(prediction, 80.0, choice=chosen, holds=chose)

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
    kept = settled(FALLS_TO_62, chose=HEAT, minutes_ago=10.0)

    assert (kept.preference, kept.setpoints, kept.rule) == (HEAT, (64.0, 67.0), "hold")
    assert kept.reason.startswith(
        "Keeping the heat preference because it was chosen 10 min ago. Otherwise cool: "
    )


def test_the_hold_ends_after_the_hold_time():
    assert settled(FALLS_TO_62, chose=HEAT, minutes_ago=29.0).rule == "hold"
    assert settled(FALLS_TO_62, chose=HEAT, minutes_ago=30.0).preference == COOL


def test_there_is_nothing_to_hold_when_the_tool_has_chosen_nothing():
    assert settled(FALLS_TO_62).preference == COOL
    assert settled(FALLS_TO_62, holds=HEAT).preference == COOL


def test_a_fresh_choice_the_thermostat_has_lost_is_not_held():
    # HVP-006. Somebody, or the schedule, has moved the setpoints since the tool chose heat.
    for holds in (COOL, None):
        decision = decide(
            FALLS_TO_62, GOAL, SETTINGS, now=NOW, choice=Choice(HEAT, at=NOW - 600), holds=holds
        )

        assert (decision.preference, decision.rule) == (COOL, "resting")


def test_agreeing_with_the_tools_own_choice_is_not_a_hold():
    assert settled(FALLS_TO_62, chose=COOL, minutes_ago=1.0).rule == "resting"


@pytest.mark.parametrize(
    ("prediction", "chose", "expected"),
    [
        # HVP-004. The air conditioner brought the house to 64 ten minutes ago, and it goes on
        # cooling by itself: the furnace has to hold 64, and waiting would let it reach 61.
        pytest.param(FALLS_TO_60, COOL, HEAT, id="will-overcool"),
        pytest.param(RISES_TO_68, HEAT, COOL, id="will-overheat"),
        pytest.param(ALREADY_TOO_WARM, HEAT, COOL, id="already-past-the-edge"),
    ],
)
def test_a_choice_made_minutes_ago_does_not_hold_up_a_house_that_will_overshoot(
    prediction, chose, expected
):
    decision = settled(prediction, chose=chose, minutes_ago=10.0)

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
    ten_hours = dataclasses.replace(SETTINGS, coast_minutes=600.0)
    just_changed = Change(at=NOW, started_from=60.0)
    assert decide(STAYS_COLD, GOAL, ten_hours, now=NOW, change=just_changed).reason == (
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
