import dataclasses
import json

import numpy as np
import pytest

from hvac_predict.decision import Change, Choice
from hvac_predict.state import State, load_state, observe, save_state

NOW = 1_780_000_000.0
# The target dropped from 68 to 64 forty minutes ago, with the house at 68.
CHANGED_AT = NOW - 2400
ON_ITS_WAY = State(target=64.0, seen_at=NOW - 600, change=Change(at=CHANGED_AT, started_from=68.0))
SETTLED = State(target=64.0, seen_at=NOW - 600, choice=Choice("heat", at=NOW - 3600))
# The tool sent the heat pair last, and the cool pair before it.
SENT = dataclasses.replace(SETTLED, sent=((64.0, 67.0), (61.0, 64.0)))
# One reading every five minutes over the last hour.
TIMES = NOW - 300 * np.arange(12, -1, -1.0)


def test_a_first_run_knows_nothing_about_when_the_target_changed():
    # HVP-016
    state = observe(None, target=68.0, indoor=67.0, now=NOW)

    assert state == State(target=68.0, seen_at=NOW, change=None, choice=None)


def test_an_unchanged_target_keeps_what_is_known_about_it():
    for known in (ON_ITS_WAY, SETTLED, SENT):
        state = observe(known, target=64.0, indoor=66.0, now=NOW)

        assert state == dataclasses.replace(known, seen_at=NOW)


def test_a_new_target_starts_the_clock_and_forgets_the_old_choice():
    state = observe(SETTLED, target=68.0, indoor=64.5, now=NOW)

    assert state == State(
        target=68.0, seen_at=NOW, change=Change(at=NOW, started_from=64.5), choice=None
    )


def test_a_new_target_keeps_the_record_of_what_the_tool_sent():
    # The thermostat may still be holding it when the schedule moves on.
    assert observe(SENT, target=68.0, indoor=64.5, now=NOW).sent == SENT.sent


@pytest.mark.parametrize(
    ("silence_minutes", "changed_minutes_ago"), [(30, 0), (31, 31), (600, 600)]
)
def test_a_change_seen_only_after_a_long_silence_is_taken_to_be_as_old_as_it_can_be(
    silence_minutes, changed_minutes_ago
):
    quiet = dataclasses.replace(SENT, seen_at=NOW - 60 * silence_minutes)

    state = observe(quiet, target=68.0, indoor=64.5, now=NOW)

    # Nobody knows when it changed, so the house is not given a fresh 90 minutes.
    assert state.change == Change(at=NOW - 60 * changed_minutes_ago, started_from=64.5)
    assert state.choice is None
    assert state.sent == SENT.sent


def readings(*latest: float) -> np.ndarray:
    """Return an hour of indoor readings at 68 that ends in the ones given."""
    series = np.full(len(TIMES), 68.0)
    series[len(series) - len(latest) :] = latest
    return series


@pytest.mark.parametrize(
    ("indoor", "arrived"),
    [
        pytest.param(readings(67.0, 66.0, 65.0), False, id="still-on-its-way"),
        pytest.param(readings(65.0, 64.0), True, id="at-the-target"),
        pytest.param(readings(64.5, 63.5, 63.0), True, id="past-the-target"),
        pytest.param(readings(63.0, 66.0), True, id="past-it-and-back-again"),
        pytest.param(readings(64.25), True, id="a-hair-short-is-close-enough"),
        pytest.param(readings(64.4), False, id="further-than-the-tolerance"),
        pytest.param(readings(np.nan, np.nan), False, id="gaps-are-not-readings"),
    ],
)
def test_a_house_has_arrived_once_a_reading_since_the_change_is_at_the_target(indoor, arrived):
    state = ON_ITS_WAY.having_seen(TIMES, indoor, tolerance=0.3)

    assert (state.change is None) is arrived
    assert dataclasses.replace(state, change=ON_ITS_WAY.change) == ON_ITS_WAY


def test_a_house_warming_to_a_higher_target_arrives_the_same_way():
    warming = State(target=68.0, seen_at=NOW - 600, change=Change(at=NOW - 2400, started_from=64.0))

    def arrived_at(reading: float) -> bool:
        indoor = np.full(len(TIMES), 64.0)
        indoor[-1] = reading
        return warming.having_seen(TIMES, indoor, tolerance=0.3).change is None

    assert not arrived_at(67.6)
    assert arrived_at(67.75)
    assert arrived_at(68.5)


def test_readings_from_before_the_change_do_not_count_as_arriving():
    # At 62 until 45 minutes ago, five minutes before the target changed, and at 68 since.
    indoor = np.where(TIMES < CHANGED_AT, 62.0, 68.0)

    assert ON_ITS_WAY.having_seen(TIMES, indoor, tolerance=0.3) == ON_ITS_WAY


def test_a_house_that_started_at_the_new_target_has_arrived_without_a_reading():
    barely_changed = dataclasses.replace(ON_ITS_WAY, change=Change(at=NOW, started_from=64.2))

    assert barely_changed.having_seen(TIMES[:0], TIMES[:0], tolerance=0.3).change is None


def test_readings_mean_nothing_to_a_house_that_is_not_on_its_way():
    assert SETTLED.having_seen(TIMES, readings(60.0), tolerance=0.3) == SETTLED


def test_choosing_a_preference_records_when():
    assert ON_ITS_WAY.having_chosen("cool", NOW).choice == Choice("cool", at=NOW)
    assert SETTLED.having_chosen("cool", NOW).choice == Choice("cool", at=NOW)
    assert SETTLED.having_chosen("heat", NOW).choice == Choice("heat", at=NOW)


def test_a_pair_about_to_be_sent_goes_on_record_with_those_the_thermostat_may_still_hold():
    # SENT has the heat pair as sent last, over the cool pair.
    heat, cool, drift = (64.0, 67.0), (61.0, 64.0), (64.0, 71.0)

    # With no hold in force, nothing sent before can be what the thermostat holds.
    assert SENT.sending(drift, over=None).sent == (drift,)
    # It holds the newest pair, so the older one is out of the picture.
    assert SENT.sending(drift, over=heat).sent == (drift, heat)
    # It still holds the older pair: the newer one may yet turn up, and so may this one.
    still_on_cool = SENT.sending(drift, over=cool)
    assert still_on_cool.sent == (drift, heat, cool)
    # Sending again never loses the pair the thermostat holds.
    assert still_on_cool.sending(drift, over=cool).sent == still_on_cool.sent


def test_the_pair_the_thermostat_holds_stays_on_record_however_many_sends_are_lost():
    cool = (61.0, 64.0)
    state = SENT
    for pair in [(64.0, 71.0), (57.0, 64.0), (64.0, 72.0), (58.0, 64.0)]:
        state = state.sending(pair, over=cool)

    assert state.sent == ((58.0, 64.0), (64.0, 72.0), (57.0, 64.0), cool)


def test_a_pair_that_is_not_on_record_cannot_be_the_one_the_thermostat_holds():
    # HVP-024. Going on would put a pair the tool never sent on record as its own.
    with pytest.raises(ValueError, match="not in tuple"):
        SENT.sending((64.0, 71.0), over=(50.0, 80.0))


@pytest.mark.parametrize("state", [ON_ITS_WAY, SETTLED, SENT, State(target=64.0, seen_at=NOW)])
def test_state_survives_being_saved_and_loaded(tmp_path, state):
    path = tmp_path / "state.json"

    save_state(state, path)
    save_state(state, path)

    assert load_state(path, now=NOW) == state
    assert [each.name for each in tmp_path.iterdir()] == ["state.json"]


def stored(**changes) -> str:
    """Spell a state file with every part filled in, and some of it changed."""
    return json.dumps(dataclasses.asdict(FULL) | changes)


FULL = dataclasses.replace(ON_ITS_WAY, choice=SETTLED.choice, sent=SENT.sent)


def test_a_state_file_written_by_this_version_stays_readable(tmp_path):
    # The keys are the format. A change that fails here makes the tool forget the hold it placed.
    path = tmp_path / "state.json"
    path.write_text(
        """{"target": 64.0, "seen_at": 1779999400.0,
            "change": {"at": 1779997600.0, "started_from": 68.0},
            "choice": {"preference": "heat", "at": 1779996400.0},
            "sent": [[64.0, 67.0], [61.0, 64.0]]}"""
    )

    assert load_state(path, now=NOW) == FULL


def test_a_state_file_as_the_tool_writes_it_is_believed(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(stored())

    assert load_state(path, now=NOW) == FULL


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(None, id="missing"),
        pytest.param("not json", id="not-json"),
        pytest.param("[]", id="not-an-object"),
        pytest.param('{"target": 68.0}', id="keys-missing"),
        pytest.param(stored(target="68"), id="text-for-a-number"),
        pytest.param(stored(target=True), id="true-for-a-number"),
        pytest.param(stored(target=float("nan")), id="not-a-number"),
        pytest.param(stored(seen_at=float("inf")), id="infinite"),
        pytest.param(stored(seen_at=10**400), id="too-large-for-a-float"),
        pytest.param(stored(seen_at=NOW + 1), id="seen-in-the-future"),
        pytest.param(
            stored(change={"at": NOW + 1, "started_from": 68.0}), id="changed-in-the-future"
        ),
        pytest.param(stored(change={"at": NOW - 60}), id="change-without-a-temperature"),
        pytest.param(stored(change=0), id="change-of-the-wrong-kind"),
        pytest.param(stored(choice={"preference": "off", "at": NOW - 60}), id="no-such-preference"),
        pytest.param(stored(choice={"preference": "heat", "at": NOW + 1}), id="chosen-in-future"),
        pytest.param(stored(choice="heat"), id="choice-of-the-wrong-kind"),
        pytest.param(stored(sent=None), id="nothing-where-the-pairs-sent-belong"),
        pytest.param(stored(sent=[[64.0, 67.0]] * 5), id="more-pairs-than-the-tool-keeps"),
        pytest.param(stored(sent=[["64", 67.0]]), id="a-pair-that-starts-with-text"),
        pytest.param(stored(sent=[[64.0]]), id="half-a-pair"),
        pytest.param(stored(sent=[[64.0, "67"]]), id="a-pair-that-is-not-numbers"),
        pytest.param(stored(sent=[64.0, 67.0]), id="a-pair-where-a-list-of-pairs-belongs"),
        pytest.param(
            '{"target": 68.0, "seen_at": 0, "change": null, "choice": null}',
            id="written-before-the-tool-kept-what-it-sent",
        ),
        pytest.param("[" * 5_000, id="nested-too-deeply"),
        pytest.param(stored() + " " * 10_000, id="far-too-long"),
        pytest.param(b"\xff\xfe", id="not-text"),
    ],
)
def test_a_state_file_that_cannot_be_believed_means_starting_afresh(tmp_path, content):
    # HVP-016
    path = tmp_path / "state.json"
    if isinstance(content, bytes):
        path.write_bytes(content)
    elif content is not None:
        path.write_text(content)

    assert load_state(path, now=NOW) is None
