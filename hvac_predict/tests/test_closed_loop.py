"""The tool steering a simulated house for days, measured against what its rules are for."""

import pytest
from closed_loop import Outcome, simulate

DAYS = 4
# Warm enough outdoors that the house needs cooling by day and warms itself in the morning.
WARM = 18.0
# Cold enough that, given three hours, the house cools to its night-time target unaided,
# from further off than the usual pair would allow.
COLD = 2.0


@pytest.fixture(scope="module")
def steered() -> Outcome:
    return simulate(DAYS, mean_outdoor=WARM)


@pytest.fixture(scope="module")
def cold_night() -> Outcome:
    return simulate(DAYS, mean_outdoor=COLD, coast_minutes=180.0)


def test_every_rule_takes_part_in_steering_the_house(steered):
    for rule in ("drift", "drive", "overshoot", "resting", "hold"):
        assert steered.rules[rule], f"no decision was made by {rule}"


def test_a_house_that_gets_there_unaided_is_spared_the_air_conditioner(cold_night):
    # HVP-003. With no time allowed the house is driven to every new target.
    driven = simulate(DAYS, mean_outdoor=COLD, coast_minutes=0.0)

    assert cold_night.rules["drift"] > 20
    assert driven.rules["drift"] == 0
    assert cold_night.cooling_hours < 0.5 * driven.cooling_hours


def test_nothing_runs_against_a_house_that_is_drifting(cold_night):
    # HVP-015
    assert cold_night.rules["drift"] > 20
    assert cold_night.fought_drifts == 0


def test_keeping_a_fresh_choice_saves_setpoint_changes():
    # HVP-006. Looking only 90 minutes ahead makes the rules change their mind more often.
    kept = simulate(DAYS, mean_outdoor=WARM, overshoot_minutes=90.0)
    not_kept = simulate(DAYS, mean_outdoor=WARM, overshoot_minutes=90.0, min_hold_minutes=0.0)

    assert not_kept.rules["hold"] == 0
    assert kept.changes < 0.7 * not_kept.changes
