"""Remember, between runs, what the tool last saw and did.

Home Assistant does not say when the thermostat's target last changed, when
its setpoints were last moved, or who moved them, and all three matter: a
house gets a fixed time to reach a new target, a preference is not changed
again straight away, and a hold somebody else placed is not the tool's to
replace. So each run writes down what it found and did for the next one to
read.
"""

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from .decision import COOL, HEAT, Change, Choice, Pair, Preference
from .files import write_atomically
from .limits import finite

# A target that differs from the one last seen is taken to have changed just
# now only if the last run was this recent. After a longer silence nobody
# knows when it changed, and it is taken to have changed as early as it could
# have, at the last run, which leaves the house the least time to drift.
_MAX_SILENCE_SECONDS = 30 * 60
# A state file is a few hundred characters. A longer one is not this tool's.
_MAX_CHARACTERS = 10_000
# How many pairs the tool keeps on record besides the one it is sending and
# the one the thermostat holds: sends that may or may not have arrived.
_MAX_UNCONFIRMED = 2


# The field names of State, and of the Change and the Choice inside it, are the
# keys of the state file. Renaming one makes every file written before
# unreadable, and a file that cannot be read costs the tool its record of the
# hold it placed.


@dataclass(frozen=True)
class State:
    target: float
    seen_at: float
    # None once the house has been at the target, and when nobody saw the target change.
    change: Change | None = None
    # None until the tool sets a preference for this target.
    choice: Choice | None = None
    # The pairs the thermostat may be holding because the tool sent them,
    # newest first: everything sent since the thermostat was last seen holding
    # one of them, and that one, in case none of the later ones arrived.
    sent: tuple[Pair, ...] = ()

    def having_seen(self, times: np.ndarray, indoor: np.ndarray, tolerance: float) -> "State":
        """Return the state after the indoor readings of a history.

        The house has reached a changed target once a reading since the change
        is on the far side of it from where the house started, or within
        ``tolerance`` of it: a thermostat does not start its equipment for a
        fraction of a degree. From then on the change is behind it.
        """
        if self.change is None:
            return self
        since = indoor[(times >= self.change.at) & np.isfinite(indoor)]
        seen = np.append(since, self.change.started_from)
        if seen.min() - tolerance <= self.target <= seen.max() + tolerance:
            return replace(self, change=None)
        return self

    def sending(self, setpoints: Pair, *, over: Pair | None) -> "State":
        """Return the state for a run that is about to send ``setpoints``.

        ``over`` is the pair on record that the thermostat holds now, or None
        with no hold in force. It stays on record, with the pairs sent since it
        was sent, because nobody knows which of those sends arrived. With no
        hold in force none of the old pairs can be what the thermostat holds.

        A pair that is not on record raises ValueError. Going on would put a
        pair the tool never sent on record as its own.
        """
        if over is None:
            return replace(self, sent=(setpoints,))
        unconfirmed = [pair for pair in self.sent[: self.sent.index(over)] if pair != setpoints]
        return replace(self, sent=(setpoints, *unconfirmed[:_MAX_UNCONFIRMED], over))

    def having_chosen(self, preference: Preference, now: float) -> "State":
        """Return the state after the tool has moved the thermostat to ``preference``."""
        return replace(self, choice=Choice(preference, now))


def observe(previous: State | None, *, target: float, indoor: float, now: float) -> State:
    """Return the state for this run, given the thermostat's target and the house now.

    A first run has nothing to compare the target with, and takes the house to
    have been at it.
    """
    if previous is None:
        return State(target, seen_at=now)
    if previous.target == target:
        return replace(previous, seen_at=now)
    watched = now - previous.seen_at <= _MAX_SILENCE_SECONDS
    changed_at = now if watched else previous.seen_at
    # A new target starts with nothing chosen for it yet. Everything else the
    # tool knows about the thermostat still stands.
    return replace(
        previous, target=target, seen_at=now, change=Change(changed_at, indoor), choice=None
    )


def load_state(path: Path, *, now: float) -> State | None:
    """Read the state file. One that is missing or cannot be believed means starting afresh.

    Afresh has a price: with no record of what it sent, the tool takes a hold
    in force for somebody else's and waits for it to end.
    """
    try:
        with path.open() as file:
            text = file.read(_MAX_CHARACTERS + 1)
        if len(text) > _MAX_CHARACTERS:
            return None
        return _state(json.loads(text), now)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return None


def save_state(state: State, path: Path) -> None:
    write_atomically(path, json.dumps(asdict(state), indent=2) + "\n")


def _state(raw: Any, now: float) -> State:
    """Build a state from what a state file held, or raise if any of it is not believable."""
    change, choice = raw["change"], raw["choice"]
    return State(
        target=_number(raw["target"]),
        seen_at=_time(raw["seen_at"], now),
        change=None
        if change is None
        else Change(_time(change["at"], now), _number(change["started_from"])),
        choice=None
        if choice is None
        else Choice(_preference(choice["preference"]), _time(choice["at"], now)),
        sent=_pairs(raw["sent"]),
    )


def _pairs(value: Any) -> tuple[Pair, ...]:
    """Return the pairs a state file lists as sent, of which the tool keeps a few."""
    if not isinstance(value, list) or len(value) > _MAX_UNCONFIRMED + 2:
        raise ValueError(value)
    pairs = []
    for low, high in value:
        pairs.append((_number(low), _number(high)))
    return tuple(pairs)


def _number(value: Any) -> float:
    number = finite(value)
    if number is None:
        raise ValueError(value)
    return number


def _time(value: Any, now: float) -> float:
    """Return a time written by an earlier run, which cannot be later than now."""
    time = _number(value)
    if time > now:
        raise ValueError(value)
    return time


def _preference(value: Any) -> Preference:
    if value == HEAT:
        return HEAT
    if value == COOL:
        return COOL
    raise ValueError(value)
