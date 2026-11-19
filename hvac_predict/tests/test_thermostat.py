import pytest
from fakes import HOLD, FakeHome, write_config

from hvac_predict.errors import DataError
from hvac_predict.thermostat import LeftAlone, Thermostat, read_thermostat, send_setpoints


def read(tmp_path, home: FakeHome, sent=(), **sections) -> Thermostat | LeftAlone:
    return read_thermostat(write_config(tmp_path, **sections), home, sent)


def steered(tmp_path, home: FakeHome, **sections) -> Thermostat:
    thermostat = read(tmp_path, home, **sections)
    assert isinstance(thermostat, Thermostat)
    return thermostat


def test_reads_what_the_tool_needs_to_know(tmp_path):
    home = FakeHome([], comfort="Sleep", setpoints=(16.0, 18.0), reads=19.5)

    assert steered(tmp_path, home) == Thermostat(
        entity_id="climate.house",
        comfort="Sleep",
        target=18.0,
        setpoints=(16.0, 18.0),
        own_hold=None,
        temperature=19.5,
        unit="°C",
        shown_in="°C",
    )


def test_temperatures_are_read_in_the_unit_home_assistant_shows(tmp_path):
    # The history is in °C and this thermostat is shown in °F.
    thermostat = steered(tmp_path, FakeHome([], unit="°F", setpoints=(66.2, 69.8), reads=68.0))

    assert thermostat.setpoints == pytest.approx((19.0, 21.0))
    assert thermostat.temperature == pytest.approx(20.0)
    assert (thermostat.unit, thermostat.shown_in) == ("°C", "°F")


def test_setpoints_are_sent_in_the_unit_home_assistant_shows(tmp_path):
    home = FakeHome([], unit="°F")

    send_setpoints(home, steered(tmp_path, home), (21.0, 23.0))

    assert home.setpoint_changes == [("climate.house", 69.8, 73.4)]


def test_setpoints_are_rounded_to_what_a_thermostat_can_hold(tmp_path):
    home = FakeHome([], unit="°F")

    send_setpoints(home, steered(tmp_path, home), (20.37, 22.37))

    assert home.setpoint_changes == [("climate.house", 68.7, 72.3)]


@pytest.mark.parametrize(
    ("setpoints", "pair", "held"),
    [
        ((21.0, 23.0), (21.0, 23.0), True),
        ((21.1, 22.9), (21.0, 23.0), True),  # a thermostat rounds what it is sent
        ((21.25, 23.25), (21.0, 23.0), False),
        ((21.0, 23.25), (21.0, 23.0), False),
        ((19.0, 21.0), (21.0, 23.0), False),
        ((None, None), (21.0, 23.0), False),
    ],
)
def test_knows_whether_it_already_holds_a_pair(tmp_path, setpoints, pair, held):
    assert steered(tmp_path, FakeHome([], setpoints=setpoints)).is_set_to(pair) is held


@pytest.mark.parametrize(
    ("setpoints", "preference"),
    [
        ((21.0, 23.0), "heat"),
        ((21.1, 26.0), "heat"),  # the idle setpoint of a drift is further out
        ((19.0, 21.0), "cool"),
        ((15.0, 20.9), "cool"),
        ((17.0, 25.0), None),
        ((20.75, 21.25), None),
        ((None, None), None),
    ],
)
def test_knows_which_of_its_setpoints_is_the_target(tmp_path, setpoints, preference):
    # The Home setting of the test configuration aims for 21.
    assert steered(tmp_path, FakeHome([], setpoints=setpoints)).preference == preference


@pytest.mark.parametrize("missing", [None, "21", True, float("nan"), float("inf"), 10**400])
def test_a_reading_that_is_not_a_number_is_no_reading(tmp_path, missing):
    thermostat = steered(tmp_path, FakeHome([], setpoints=(missing, 23.0), reads=missing))

    assert thermostat.setpoints is None
    assert thermostat.temperature is None


# When the thermostat is not this tool's to steer.


@pytest.mark.parametrize("mode", ["heat", "cool", "off", "fan_only", "unavailable"])
def test_only_a_thermostat_in_auto_mode_is_steered(tmp_path, mode):
    # HVP-001. A thermostat that is unavailable has no comfort setting to name either.
    left = read(tmp_path, FakeHome([], mode=mode, comfort=None))

    assert isinstance(left, LeftAlone)
    assert left.reason.startswith(f"The thermostat is in '{mode}' mode.")


@pytest.mark.parametrize("preset", ["home", "Home", None])
def test_a_thermostat_on_its_schedule_is_steered(tmp_path, preset):
    thermostat = steered(tmp_path, FakeHome([], comfort="Home", preset=preset))

    assert thermostat.own_hold is None


# Whose hold it is. A hold on the setpoints alone is the kind the tool places, and also the kind
# a person places at the thermostat. An ecobee may report it while a vacation's setpoints rule.

SENT = ((21.0, 23.0), (19.0, 21.0))


@pytest.mark.parametrize(("holds", "own"), [((21.0, 23.0), SENT[0]), ((19.1, 20.9), SENT[1])])
def test_a_hold_on_setpoints_the_tool_sent_is_its_own(tmp_path, holds, own):
    thermostat = read(tmp_path, FakeHome([], preset=HOLD, setpoints=holds), SENT)

    assert isinstance(thermostat, Thermostat)
    assert thermostat.own_hold == own


@pytest.mark.parametrize(
    ("holds", "sent", "said"),
    [
        # HVP-024
        pytest.param((16.5, 24.5), SENT, "is holding 16.5 to 24.5 °C", id="a-vacations-setpoints"),
        pytest.param((20.0, 22.0), SENT, "is holding 20.0 to 22.0 °C", id="set-by-hand"),
        pytest.param((21.0, 23.25), SENT, "is holding 21.0 to 23.2 °C", id="one-setpoint-moved"),
        pytest.param((21.0, 23.0), (), "is holding 21.0 to 23.0 °C", id="nothing-on-record"),
        pytest.param((None, None), SENT, "is on a hold", id="no-setpoints"),
    ],
)
def test_a_hold_on_other_setpoints_is_someone_elses(tmp_path, holds, sent, said):
    assert read(tmp_path, FakeHome([], preset=HOLD, setpoints=holds), sent) == LeftAlone(
        f"The thermostat {said} that this tool has no record of placing. The tool leaves "
        "a hold placed by someone else alone until that hold ends."
    )


def test_setpoints_the_tool_did_not_send_are_its_to_set_once_no_hold_is_in_force(tmp_path):
    thermostat = read(tmp_path, FakeHome([], setpoints=(16.5, 24.5)), SENT)

    assert isinstance(thermostat, Thermostat)
    assert thermostat.own_hold is None


def test_whose_hold_it_is_is_judged_in_the_units_of_the_history(tmp_path):
    # The record is in °C, and this thermostat is shown in °F: 69.8 to 73.4 is 21 to 23.
    thermostat = read(tmp_path, FakeHome([], unit="°F", preset=HOLD, setpoints=(69.8, 73.4)), SENT)

    assert isinstance(thermostat, Thermostat)
    assert thermostat.own_hold == SENT[0]


@pytest.mark.parametrize(
    ("holds", "said"),
    [
        # HVP-024. Half a degree Fahrenheit, the smallest step Home Assistant offers, is 0.28 °C.
        ((70.3, 73.9), "is holding 21.3 to 23.3 °C"),
        # Three tenths of a degree Fahrenheit is 0.17 °C, which is close in °C and not in °F.
        ((70.1, 73.7), "is holding 21.2 to 23.2 °C"),
    ],
)
def test_how_near_counts_as_the_same_is_judged_in_the_unit_home_assistant_shows(
    tmp_path, holds, said
):
    left = read(tmp_path, FakeHome([], unit="°F", preset=HOLD, setpoints=holds), SENT)

    assert isinstance(left, LeftAlone)
    assert said in left.reason


def test_a_tenth_of_a_degree_celsius_is_near_enough_under_a_fahrenheit_history(tmp_path):
    # The record is in °F: 68 to 71. Home Assistant shows °C, where 20.1 and 21.8 are 68.2 and
    # 71.2 °F: further off than 0.2 °F, and within the 0.2 °C that counts.
    fahrenheit = {"units": {"temperature": "°F"}, "comfort": {"targets": {"Home": 68.0}}}
    home = FakeHome([], unit="°C", preset=HOLD, setpoints=(20.1, 21.8), reads=20.0)

    thermostat = read(tmp_path, home, ((68.0, 71.0),), **fahrenheit)

    assert isinstance(thermostat, Thermostat)
    assert thermostat.own_hold == (68.0, 71.0)


@pytest.mark.parametrize("preset", ["away", "away_indefinitely", "vacation", "sleep", "Temp", ""])
def test_a_hold_someone_else_placed_is_left_alone(tmp_path, preset):
    # HVP-007
    assert read(tmp_path, FakeHome([], comfort="Home", preset=preset)) == LeftAlone(
        f"The thermostat is holding '{preset}' in place of its scheduled 'Home' setting."
    )


@pytest.mark.parametrize(
    ("preset", "shown"),
    [
        (5, "5"),
        (["vacation"], "['vacation']"),
        (False, "False"),
        ("va\x1bcation", r"'va\x1bcation'"),
        ("a" * 200, "'" + "a" * 63 + "…"),
    ],
)
def test_a_preset_the_tool_cannot_make_sense_of_is_someone_elses_hold(tmp_path, preset, shown):
    # HVP-007
    assert read(tmp_path, FakeHome([], comfort="Home", preset=preset)) == LeftAlone(
        f"The thermostat is holding {shown} in place of its scheduled 'Home' setting."
    )


def test_a_comfort_setting_without_a_target_is_left_alone(tmp_path):
    # HVP-017
    assert read(tmp_path, FakeHome([], comfort="Away", preset="away")) == LeftAlone(
        "No target is configured for the 'Away' comfort setting."
    )


def test_a_number_too_large_to_convert_is_no_reading(tmp_path):
    # The history is in °F and Home Assistant shows °C: 1e308 °C is a number, and in °F it is not.
    fahrenheit = {"units": {"temperature": "°F"}, "comfort": {"targets": {"Home": 68.0}}}
    home = FakeHome([], setpoints=(1e308, 1e308), reads=1e308)

    thermostat = steered(tmp_path, home, **fahrenheit)

    assert (thermostat.setpoints, thermostat.temperature) == (None, None)


def test_the_longest_name_a_comfort_setting_may_have_is_accepted(tmp_path):
    name = "H" * 64
    named = {"comfort": {"targets": {name: 21.0}}}

    assert steered(tmp_path, FakeHome([], comfort=name), **named).comfort == name


def test_the_enable_switch_is_asked_before_anything_else(tmp_path):
    # HVP-019. Off, and the thermostat in a mode the tool would also leave alone.
    switched = {"homeassistant": {"enable_entity": "input_boolean.hvac_predict"}}

    assert read(tmp_path, FakeHome([], switch="off", mode="off"), **switched) == LeftAlone(
        "input_boolean.hvac_predict is not on."
    )


def test_comfort_settings_are_matched_whatever_their_case(tmp_path):
    assert steered(tmp_path, FakeHome([], comfort="SLEEP", preset="sleep")).target == 18.0


@pytest.mark.parametrize("switch", ["off", "unavailable", "unknown"])
def test_the_enable_switch_pauses_the_tool_unless_it_is_on(tmp_path, switch):
    # HVP-019
    switched = {"homeassistant": {"enable_entity": "input_boolean.hvac_predict"}}

    assert read(tmp_path, FakeHome([], switch=switch), **switched) == LeftAlone(
        "input_boolean.hvac_predict is not on."
    )
    assert isinstance(read(tmp_path, FakeHome([], switch="on"), **switched), Thermostat)


@pytest.mark.parametrize("comfort", [None, "", "  ", 7, "Ho\x1bme", "H" * 65])
def test_a_thermostat_that_does_not_name_its_comfort_setting_is_an_error(tmp_path, comfort):
    with pytest.raises(DataError, match="has no 'climate_mode' attribute naming its comfort"):
        read(tmp_path, FakeHome([], comfort=comfort))


def test_the_comfort_setting_can_be_read_from_another_attribute(tmp_path):
    home = FakeHome([], comfort=None, preset="Home")
    elsewhere = {"homeassistant": {"comfort_attribute": "preset_mode"}}

    assert steered(tmp_path, home, **elsewhere).comfort == "Home"
