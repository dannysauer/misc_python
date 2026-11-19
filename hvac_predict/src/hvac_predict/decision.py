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

Rule 3 does not reverse a preference the tool set a few minutes ago and the
thermostat still holds, so that a house hovering at the target does not have
its setpoints moved on every run. Rules 1 and 2 are never held up: they are
the ones the house needs.

The drift pair of rule 1 is the only one wider than the differential. Its
idle setpoint follows where the house started, held to ``Goal.reach``, so no
setpoint goes further than a differential and a degree beyond the usual pairs
of the configured targets.

The rules remember two things from one run to the next, a ``Change`` of target
and the tool's last ``Choice``. ``state.py`` keeps both in the state file.
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
    # The temperatures the usual pairs of all the configured targets keep a house between.
    reach: Pair

    def __post_init__(self) -> None:
        # The reach is what bounds a drift pair, so it has to be a real bound.
        (lowest, highest), (low, high) = self.reach, self.range
        finite = math.isfinite(lowest) and math.isfinite(highest)
        if not (finite and lowest <= low and high <= highest):
            raise ValueError(f"the reach {self.reach} does not take in the pairs of {self.target}")

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

        ``clear_of`` is held to ``reach`` first. The pair of some target held
        the house until the change, so it started within their reach, and
        holding it to that bounds how far the idle setpoint can go, whatever a
        state file or a sensor says.
        """
        low, high = self.pair(preference)
        clear_of = min(max(clear_of, self.reach[0]), self.reach[1])
        if preference == HEAT:
            return low, max(high, math.ceil(clear_of) + self.differential)
        return min(low, math.floor(clear_of) - self.differential), high


# The state file keeps a Change and a Choice under these field names. Renaming
# one makes every file written before unreadable; see state.py.


@dataclass(frozen=True)
class Change:
    """A change of target that the house has not caught up with yet."""

    at: float  # when the target changed, as nearly as the tool can tell
    started_from: float  # the indoor temperature when the tool first saw the new target


@dataclass(frozen=True)
class Choice:
    """A preference the tool set on the thermostat."""

    preference: Preference
    at: float


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
    now: float,
    change: Change | None = None,
    choice: Choice | None = None,
    holds: Preference | None = None,
) -> Decision:
    """Choose the pair of setpoints the thermostat should hold at the time ``now``.

    ``change`` is None once the house has been at the target. ``choice`` is
    the preference the tool last set, if it has set one for this target, and
    ``holds`` is the preference the thermostat holds now, if its setpoints are
    one.
    """
    if change is not None:
        return _settling(outlook, goal, settings, change, now)
    ahead = outlook.within(settings.overshoot_minutes)
    overshoot = _overshoot(ahead, goal)
    if overshoot is not None:
        return overshoot
    resting = _resting(ahead, goal)
    # The hold: a choice the thermostat still holds is not reversed while it is fresh.
    if choice is not None and holds == choice.preference and resting.preference != holds:
        minutes = (now - choice.at) / 60
        if minutes < settings.min_hold_minutes:
            return Decision(
                choice.preference,
                goal.pair(choice.preference),
                "hold",
                f"Keeping the {choice.preference} preference because it was chosen "
                f"{minutes:.0f} min ago. Otherwise {resting.preference}: {resting.reason}",
            )
    return resting


def _settling(
    outlook: Outlook, goal: Goal, settings: DecisionSettings, change: Change, now: float
) -> Decision:
    """Decide how a house that has not reached a new target should get there."""
    # What is left of the time the house is given to get there unaided; zero once that is up.
    left = max(settings.coast_minutes - (now - change.at) / 60, 0.0)
    target = goal.target
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
            goal.drift_pair(drift, clear_of=change.started_from),
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
