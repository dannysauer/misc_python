"""Read the thermostat through Home Assistant, and set its setpoints.

Everything the tool assumes about the thermostat is in this module. It was
written for an ecobee, from the source of Home Assistant's ecobee integration,
and has not been checked against a device.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeGuard

from .config import Config
from .decision import COOL, HEAT, Pair, Preference
from .errors import DataError
from .homeassistant import HomeAssistant
from .limits import finite
from .units import convert_temperature, convert_temperature_difference

# The mode in which a thermostat has a heating and a cooling setpoint at once.
AUTO = "heat_cool"
# What the ecobee integration reports as the preset while a hold on the
# setpoints alone is in force, which is the kind of hold this tool makes.
_TEMPERATURE_HOLD = "temp"
# Longer than any name a thermostat gives a comfort setting or a preset.
_MAX_NAME_LENGTH = 64
# Setpoints are sent rounded to this many decimals in the unit Home Assistant
# shows, and come back within a tenth of what was sent. Setpoints this close in
# that unit are the same setpoints. It is less than half the smallest step a
# hand can change one by at the thermostat or in Home Assistant, so a change
# made by hand is never taken for the pair the tool sent.
_SENT_DECIMALS = 1
_SAME_SETPOINT = 0.2


@dataclass(frozen=True)
class Thermostat:
    """A thermostat that is this tool's to steer right now.

    Its temperatures are in ``unit``, the unit of the history, whatever unit
    Home Assistant shows them in.
    """

    entity_id: str
    comfort: str  # the comfort setting its schedule has reached
    target: float  # the temperature configured for that comfort setting
    setpoints: Pair | None  # heating and cooling, when it reports both
    # The pair the tool sent that its hold is on, or None with no hold in force.
    own_hold: Pair | None
    temperature: float | None  # its own reading of the house
    unit: str
    shown_in: str  # the unit Home Assistant shows

    def is_set_to(self, pair: Pair) -> bool:
        """Say whether the thermostat's setpoints are already this pair."""
        return _same(self.setpoints, pair, _tolerance(self.shown_in, self.unit))

    @property
    def preference(self) -> Preference | None:
        """Which of its setpoints is the target, or None if neither is."""
        if self.setpoints is None:
            return None
        low, high = self.setpoints
        near = _tolerance(self.shown_in, self.unit)
        if abs(low - self.target) <= near:
            return HEAT
        return COOL if abs(high - self.target) <= near else None


@dataclass(frozen=True)
class LeftAlone:
    """Why the thermostat is not this tool's to steer right now."""

    reason: str


def read_thermostat(
    config: Config, home: HomeAssistant, sent: Sequence[Pair] = ()
) -> Thermostat | LeftAlone:
    """Read the thermostat, or say why the tool should leave it alone.

    ``sent`` holds the pairs the tool has on record as sent, in the units of
    the history. A hold on any other setpoints is somebody else's.
    """
    settings = config.homeassistant
    if settings.enable_entity and home.state(settings.enable_entity).state != "on":
        return LeftAlone(f"{settings.enable_entity} is not on.")
    entity = home.state(settings.climate_entity)
    if entity.state != AUTO:
        return LeftAlone(
            f"The thermostat is in {entity.state!r} mode. This tool only moves the "
            f"setpoints of a thermostat in {AUTO!r} mode, where both of them are live."
        )
    comfort = entity.attributes.get(settings.comfort_attribute)
    if not _is_name(comfort):
        raise DataError(
            f"{entity.entity_id} has no {settings.comfort_attribute!r} attribute naming its "
            "comfort setting; point homeassistant.comfort_attribute at the one that does"
        )
    # A hold on the setpoints alone is the kind this tool makes, and with no
    # hold at all the preset is the scheduled setting. Anything else is a hold
    # somebody, or the thermostat itself, put there on purpose. Whether a hold
    # on the setpoints is the tool's own is settled further down, by what they are.
    preset = entity.attributes.get("preset_mode")
    if preset is not None and not (
        _is_name(preset)
        and (preset == _TEMPERATURE_HOLD or preset.casefold() == comfort.casefold())
    ):
        return LeftAlone(
            f"The thermostat is holding {_shown(preset)} in place of its scheduled "
            f"{comfort!r} setting."
        )
    target = config.comfort.target_for(comfort)
    if target is None:
        return LeftAlone(f"No target is configured for the {comfort!r} comfort setting.")

    unit, shown_in = config.units.temperature, home.temperature_unit()

    def temperature(attribute: str) -> float | None:
        value = finite(entity.attributes.get(attribute))
        # Converting can take a number that is only just finite past the end.
        return None if value is None else finite(convert_temperature(value, shown_in, unit))

    low, high = temperature("target_temp_low"), temperature("target_temp_high")
    setpoints = None if low is None or high is None else (low, high)
    own_hold = None
    if preset == _TEMPERATURE_HOLD:
        # The same preset is reported for a hold placed at the thermostat, and
        # it may be reported while a vacation's setpoints are the ones in force.
        near = _tolerance(shown_in, unit)
        own_hold = next((pair for pair in sent if _same(setpoints, pair, near)), None)
        if own_hold is None:
            return LeftAlone(_someone_elses_hold(setpoints, unit))
    return Thermostat(
        entity_id=entity.entity_id,
        comfort=comfort,
        target=target,
        setpoints=setpoints,
        own_hold=own_hold,
        temperature=temperature("current_temperature"),
        unit=unit,
        shown_in=shown_in,
    )


def send_setpoints(home: HomeAssistant, thermostat: Thermostat, setpoints: Pair) -> None:
    """Have the thermostat hold a heating and a cooling setpoint, given in its ``unit``."""
    low, high = (
        round(convert_temperature(each, thermostat.unit, thermostat.shown_in), _SENT_DECIMALS)
        for each in setpoints
    )
    home.set_temperature_range(thermostat.entity_id, low, high)


def _tolerance(shown_in: str, unit: str) -> float:
    """Return how far apart the same setpoint can read, in the units of the history."""
    return convert_temperature_difference(_SAME_SETPOINT, shown_in, unit)


def _same(setpoints: Pair | None, pair: Pair, tolerance: float) -> bool:
    """Say whether setpoints a thermostat reports are a pair, as nearly as it keeps one."""
    return setpoints is not None and all(
        abs(held - wanted) <= tolerance for held, wanted in zip(setpoints, pair, strict=True)
    )


def _someone_elses_hold(setpoints: Pair | None, unit: str) -> str:
    held = "is on a hold"
    if setpoints is not None:
        held = f"is holding {setpoints[0]:.1f} to {setpoints[1]:.1f} {unit}"
    return (
        f"The thermostat {held} that this tool has no record of placing. The tool leaves "
        "a hold placed by someone else alone until that hold ends."
    )


def _is_name(value: object) -> TypeGuard[str]:
    """Say whether an attribute is text fit to show an operator as a name."""
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value.isprintable()
        and len(value) <= _MAX_NAME_LENGTH
    )


def _shown(value: object) -> str:
    """Spell out an attribute that may be anything, at a length fit for one line."""
    text = repr(value)
    return text if len(text) <= _MAX_NAME_LENGTH else text[:_MAX_NAME_LENGTH] + "…"
