"""Choose which pair of setpoints the thermostat should hold.

The thermostat stays in auto mode, with a heating and a cooling setpoint a
fixed differential apart. For a target temperature there are two such pairs:

    heat preference:  heat to the target, cool at the target plus the differential
    cool preference:  cool to the target, heat at the target minus the differential

Either pair keeps the house within the differential of the target. Which pair
is held decides which side of the target the equipment defends, and so on
which side the house may drift without anything running.

The choice is made from a prediction of the house left alone.

1. Settling. The target has changed and the house has not reached it yet. If
   the prediction gets there within the time allowed, hold the pair that lets
   it drift there, with the idle setpoint moved clear of where the house
   started so that nothing runs on the way. Otherwise hold the pair that
   drives it there. Until the house has arrived, nothing below applies.
   Arriving means coming within a tenth of the differential of the target.
2. Overshoot. Within the time looked ahead, the prediction leaves the range
   both pairs cover, the target plus or minus the differential. The equipment
   will have to run, so hold the pair that makes it hold the target and not
   the far edge.
3. Resting. The house stays in that range unaided. Hold the pair whose
   setpoints it sits between, so that nothing runs.

Rule 3 does not reverse a preference the tool set a few minutes ago, so that a
house hovering at the target does not have its setpoints moved on every run.
Rules 1 and 2 are never held up: they are the ones the house needs.

The drift pair of rule 1 is the only one wider than the differential. Its
idle setpoint follows ``Settling.started_from``, so whoever builds a
``Settling`` decides how far it can go.
"""

import math
from dataclasses import dataclass
from typing import Final, Literal

import numpy as np

from .config import DecisionSettings

Preference = Literal["heat", "cool"]  # which of the two setpoints is the target
HEAT: Final[Preference] = "heat"
COOL: Final[Preference] = "cool"
# What decided: the two outcomes of the settling rule, the other two rules, or the hold.
Rule = Literal["drift", "drive", "overshoot", "resting", "hold"]
Pair = tuple[float, float]  # the heating setpoint and the cooling setpoint
# A house this share of the differential from a target has reached it: a
# thermostat does not start its equipment for a fraction of a degree.
_ARRIVED_WITHIN = 0.1


@dataclass(frozen=True)
class Outlook:
    """The temperatures of a house left alone, predicted from now on."""

    minutes: np.ndarray  # from now, starting at zero
    indoor: np.ndarray
    outdoor: np.ndarray

    def within(self, minutes: float) -> "Outlook":
        """Return the part of the outlook no further ahead than ``minutes``."""
        near = self.minutes <= minutes
        return Outlook(self.minutes[near], self.indoor[near], self.outdoor[near])


@dataclass(frozen=True)
class Goal:
    """The temperature the house should be at, and how far apart the setpoints are."""

    target: float
    differential: float

    @property
    def range(self) -> Pair:
        """The temperatures the two pairs keep the house between."""
        return self.target - self.differential, self.target + self.differential

    @property
    def tolerance(self) -> float:
        """How near the target a house has to come to have reached it."""
        return _ARRIVED_WITHIN * self.differential

    def pair(self, preference: Preference) -> Pair:
        """Return the setpoints of one preference: the target and its far side."""
        low, high = self.range
        return (self.target, high) if preference == HEAT else (low, self.target)

    def drift_pair(self, preference: Preference, clear_of: float) -> Pair:
        """Return the pair of one preference with its idle setpoint clear of a temperature.

        A house drifting to the target starts on the idle setpoint's side of
        it. The idle setpoint of the usual pair can lie between the house and
        the target, where it would start the very equipment the drift avoids,
        so it goes a differential beyond ``clear_of``, rounded outwards to a
        whole degree, and never nearer the target than its usual place.
        """
        low, high = self.pair(preference)
        if preference == HEAT:
            return low, max(high, math.ceil(clear_of) + self.differential)
        return min(low, math.floor(clear_of) - self.differential), high


@dataclass(frozen=True)
class Settling:
    """A house on its way to a target that has just changed."""

    minutes_left: float  # of the time it is given to get there unaided; zero once that is up
    started_from: float  # the indoor temperature when the target changed


@dataclass(frozen=True)
class Standing:
    """The preference the tool last set, which the thermostat still holds."""

    preference: Preference
    minutes: float  # since the tool set it


@dataclass(frozen=True)
class Decision:
    preference: Preference
    setpoints: Pair
    rule: Rule  # which of the rules decided
    reason: str  # a sentence for the operator


def decide(
    outlook: Outlook,
    goal: Goal,
    settings: DecisionSettings,
    *,
    settling: Settling | None = None,
    standing: Standing | None = None,
) -> Decision:
    """Choose the pair of setpoints the thermostat should hold.

    ``settling`` is None once the house has been at the target. ``standing``
    is None when the thermostat holds nothing the tool chose.
    """
    if settling is not None:
        return _settling(outlook, goal, settling)
    ahead = outlook.within(settings.overshoot_minutes)
    choice = _overshoot(ahead, goal)
    if choice is not None:
        return choice
    choice = _resting(ahead, goal)
    if (
        standing is not None
        and standing.preference != choice.preference
        and standing.minutes < settings.min_hold_minutes
    ):
        return Decision(
            standing.preference,
            goal.pair(standing.preference),
            "hold",
            f"Keeping the {standing.preference} preference because it was chosen "
            f"{standing.minutes:.0f} min ago. Otherwise {choice.preference}: {choice.reason}",
        )
    return choice


def _settling(outlook: Outlook, goal: Goal, settling: Settling) -> Decision:
    """Decide how a house that has not reached a new target should get there."""
    target, left = goal.target, settling.minutes_left
    too_warm = outlook.indoor[0] > target
    # Above the target, the heat preference only heats, so the house is free to
    # cool down to it; the cool preference would run the air conditioner.
    drift, drive, verb = (HEAT, COOL, "cool") if too_warm else (COOL, HEAT, "warm")
    there = (
        outlook.indoor <= target + goal.tolerance
        if too_warm
        else outlook.indoor >= target - goal.tolerance
    )
    arrival = float(outlook.minutes[np.argmax(there)]) if there.any() else math.inf
    if arrival <= left:
        return Decision(
            drift,
            goal.drift_pair(drift, clear_of=settling.started_from),
            "drift",
            f"The target is newly {target:g}, and left alone the house {verb}s to it "
            f"in {_span(arrival)}, inside the {_span(left)} it has left.",
        )
    reach = float(outlook.minutes[-1])
    if left > reach:
        reason = (
            f"The target is newly {target:g}, and left alone the house would not {verb} "
            f"to it in the {_span(reach)} the prediction reaches."
        )
    elif left > 0:
        reason = (
            f"The target is newly {target:g}, and left alone the house would not {verb} "
            f"to it in the {_span(left)} it has left."
        )
    else:
        reason = (
            f"The house has had its time to {verb} to the new target of {target:g} unaided, "
            "and is not there."
        )
    return Decision(drive, goal.pair(drive), "drive", reason)


def _overshoot(ahead: Outlook, goal: Goal) -> Decision | None:
    """Decide for a house that will soon leave the range the two pairs cover."""
    low, high = goal.range
    outside = (ahead.indoor < low) | (ahead.indoor > high)
    if not outside.any():
        return None
    # Whichever edge the house passes first is the side that needs the equipment.
    first = int(np.argmax(outside))
    preference, edge, verb = (
        (COOL, high, "rises above") if ahead.indoor[first] > high else (HEAT, low, "drops below")
    )
    passing = (
        f"{verb} {edge:g} in {_span(float(ahead.minutes[first]))}"
        if first
        else f"is already past {edge:g}"
    )
    return Decision(
        preference,
        goal.pair(preference),
        "overshoot",
        f"Left alone, the house {passing}, so the equipment will have to run, "
        f"and with this pair it holds {goal.target:g}.",
    )


def _resting(ahead: Outlook, goal: Goal) -> Decision:
    """Decide for a house that stays within the differential of the target unaided."""
    preference, side = (HEAT, "at or above") if ahead.indoor[0] >= goal.target else (COOL, "below")
    return Decision(
        preference,
        goal.pair(preference),
        "resting",
        f"Left alone, the house stays within {goal.differential:g} of {goal.target:g} for "
        f"the next {_span(float(ahead.minutes[-1]))}. It is {side} {goal.target:g} now, "
        "so this pair runs nothing.",
    )


def _span(minutes: float) -> str:
    hours, rest = divmod(round(minutes), 60)
    return f"{hours} h {rest:02d} min" if hours else f"{rest} min"
