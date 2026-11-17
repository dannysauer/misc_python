"""Load and validate the TOML configuration file."""

import math
import re
import tomllib
from dataclasses import MISSING, dataclass, fields
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints
from urllib.parse import urlsplit

from .errors import ConfigError
from .units import (
    PRECIPITATION_UNITS,
    TEMPERATURE_UNITS,
    WIND_SPEED_UNITS,
    convert_temperature,
    convert_temperature_difference,
)

_ENTITY_ID = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")
# Files the tool keeps beside the configuration file, and their default names.
_FILES = {"model_path": "model.json", "state_path": "state.json"}
_MAX_TRAINING_DAYS = 730
_MAX_HORIZON_HOURS = 48
# What a house can sensibly be asked for. A target outside this is a slip of
# the hand, such as Fahrenheit numbers in a Celsius file.
_TARGET_CELSIUS = (5.0, 35.0)
# Narrower than this and rounding for the thermostat can close the gap between
# its setpoints; wider and the pairs no longer keep a house comfortable.
_DIFFERENTIAL_CELSIUS = (0.5, 6.0)
# The kinds of setting the loader understands, and how to name each to the operator.
_KINDS = {float: "a number", int: "a whole number", str: "text"}


@dataclass(frozen=True)
class VictoriaMetricsSettings:
    url: str


@dataclass(frozen=True)
class HomeAssistantSettings:
    url: str
    climate_entity: str
    weather_entity: str
    enable_entity: str | None = None
    # The attribute of the climate entity that names the comfort setting its
    # schedule has reached. The ecobee integration calls it climate_mode.
    comfort_attribute: str = "climate_mode"


@dataclass(frozen=True)
class Location:
    latitude: float
    longitude: float


@dataclass(frozen=True)
class Units:
    """Units of the values the history queries return."""

    temperature: str
    wind_speed: str = "km/h"
    precipitation: str = "mm"


@dataclass(frozen=True)
class HistoryQueries:
    """MetricsQL expressions, each of which must return a single series."""

    indoor_temp: str
    outdoor_temp: str
    solar: str | None = None
    wind: str | None = None
    rain: str | None = None
    heating: str | None = None
    cooling: str | None = None


@dataclass(frozen=True)
class TrainingSettings:
    days: float = 60.0
    step_minutes: int = 5
    window_minutes: int = 30

    @property
    def step_seconds(self) -> int:
        return self.step_minutes * 60

    @property
    def window_steps(self) -> int:
        return self.window_minutes // self.step_minutes


@dataclass(frozen=True)
class ComfortSettings:
    """What the house should be at under each of the thermostat's comfort settings."""

    # Degrees between the heating and the cooling setpoint of every pair sent.
    differential: float
    # Target temperature by comfort setting name, as the thermostat reports it.
    targets: dict[str, float]

    def target_for(self, comfort: str) -> float | None:
        """Return the target for a comfort setting, ignoring case, or None without one."""
        wanted = comfort.casefold()
        matches = (target for name, target in self.targets.items() if name.casefold() == wanted)
        return next(matches, None)

    @property
    def reach(self) -> tuple[float, float]:
        """The temperatures the pairs of all the targets together keep a house between."""
        return (
            min(self.targets.values()) - self.differential,
            max(self.targets.values()) + self.differential,
        )


@dataclass(frozen=True)
class DecisionSettings:
    # How far ahead the house is predicted.
    horizon_hours: float = 8.0
    # How long after a change of target the house may take to reach it unaided.
    coast_minutes: float = 90.0
    # How far ahead a drift past the differential is acted on.
    overshoot_minutes: float = 480.0
    # How long the tool keeps a preference it chose before reversing it.
    min_hold_minutes: float = 30.0


@dataclass(frozen=True)
class ForecastSettings:
    wind_scale: float = 1.0


@dataclass(frozen=True)
class Config:
    victoriametrics: VictoriaMetricsSettings
    homeassistant: HomeAssistantSettings
    location: Location
    units: Units
    history: HistoryQueries
    comfort: ComfortSettings
    decision: DecisionSettings
    training: TrainingSettings
    forecast: ForecastSettings
    model_path: Path
    state_path: Path


def load_config(path: Path) -> Config:
    """Read the configuration file, rejecting unknown or inconsistent settings."""
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error.strerror}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path} is not valid TOML: {error}") from error

    files = {
        name: path.parent / _checked(raw.pop(name, default), str, name)
        for name, default in _FILES.items()
    }
    sections = {name: cls for name, cls in get_type_hints(Config).items() if name not in _FILES}
    if unknown := raw.keys() - sections.keys():
        raise ConfigError(f"unknown setting: {', '.join(sorted(unknown))}")
    config = Config(
        **{name: _section(cls, raw.get(name, {}), name) for name, cls in sections.items()},
        **files,
    )
    _validate(config)
    return config


def _section(cls: type, table: Any, name: str) -> Any:
    if not isinstance(table, dict):
        raise ConfigError(f"[{name}] must be a table")
    known = {spec.name: spec for spec in fields(cls)}
    if unknown := table.keys() - known.keys():
        raise ConfigError(
            f"unknown setting: {', '.join(f'{name}.{key}' for key in sorted(unknown))}"
        )
    hints = get_type_hints(cls)
    values = {}
    for key, spec in known.items():
        if key in table:
            values[key] = _checked(table[key], hints[key], f"{name}.{key}")
        elif spec.default is MISSING:
            raise ConfigError(f"missing required setting: {name}.{key}")
    return cls(**values)


def _checked(value: Any, annotation: Any, where: str) -> Any:
    """Return ``value`` as the kind of setting ``annotation`` asks for."""
    if get_origin(annotation) is dict:
        if not isinstance(value, dict):
            raise ConfigError(f"[{where}] must be a table")
        of = get_args(annotation)[1]
        return {key: _checked(item, of, f"{where}.{key}") for key, item in value.items()}
    kinds = [kind for kind in get_args(annotation) or (annotation,) if kind is not type(None)]
    if len(kinds) != 1 or kinds[0] not in _KINDS:
        raise NotImplementedError(f"{where} has a type the configuration loader cannot check")
    (kind,) = kinds
    # TOML has no number that is both, but an operator who writes 20 means 20.0.
    accepted = (int, float) if kind is float else kind
    if isinstance(value, bool) or not isinstance(value, accepted):
        raise ConfigError(f"{where} must be {_KINDS[kind]}")
    if kind is float and not math.isfinite(value):
        raise ConfigError(f"{where} must be a finite number")
    if isinstance(value, str) and not value.strip():
        raise ConfigError(f"{where} must not be empty")
    return kind(value)


def _validate(config: Config) -> None:
    home, training, decision = config.homeassistant, config.training, config.decision
    _require(
        _resolved(config.model_path) != _resolved(config.state_path),
        "model_path and state_path must be two different files",
    )
    _require_url(config.victoriametrics.url, "victoriametrics.url")
    _require_url(home.url, "homeassistant.url")
    for name in ("climate_entity", "weather_entity", "enable_entity"):
        entity = getattr(home, name)
        _require(
            entity is None or _ENTITY_ID.fullmatch(entity) is not None,
            f"homeassistant.{name} must look like 'domain.object_id'",
        )

    _require(-90 <= config.location.latitude <= 90, "location.latitude must be within ±90")
    _require(-180 <= config.location.longitude <= 180, "location.longitude must be within ±180")
    for name, known in (
        ("temperature", TEMPERATURE_UNITS),
        ("wind_speed", WIND_SPEED_UNITS),
        ("precipitation", PRECIPITATION_UNITS),
    ):
        _require(
            getattr(config.units, name) in known, f"units.{name} must be one of {', '.join(known)}"
        )
    _validate_comfort(config.comfort, config.units.temperature)

    _require(
        0 < training.days <= _MAX_TRAINING_DAYS,
        f"training.days must be positive and at most {_MAX_TRAINING_DAYS}",
    )
    # Predictions are checked and reported at whole hours, which have to fall on the grid.
    _require(
        training.step_minutes > 0 and 60 % training.step_minutes == 0,
        "training.step_minutes must divide 60 evenly",
    )
    _require(
        training.window_minutes > 0 and training.window_minutes % training.step_minutes == 0,
        "training.window_minutes must be a multiple of training.step_minutes",
    )

    _require(
        1 <= decision.horizon_hours <= _MAX_HORIZON_HOURS,
        f"decision.horizon_hours must be between 1 and {_MAX_HORIZON_HOURS}",
    )
    # The first two may be longer than the prediction reaches, and then have no
    # more effect than the reach itself. The limit only keeps a slip of the hand out.
    longest = _MAX_HORIZON_HOURS * 60
    for name in ("coast_minutes", "overshoot_minutes", "min_hold_minutes"):
        _require(
            0 <= getattr(decision, name) <= longest,
            f"decision.{name} must be between 0 and {longest}",
        )
    _require(config.forecast.wind_scale > 0, "forecast.wind_scale must be positive")


def _validate_comfort(comfort: ComfortSettings, unit: str) -> None:
    # Rounded, so that the limits an operator reads are the ones that are applied.
    narrowest, widest = (
        round(convert_temperature_difference(bound, "°C", unit), 6)
        for bound in _DIFFERENTIAL_CELSIUS
    )
    _require(
        narrowest <= comfort.differential <= widest,
        f"comfort.differential must be between {narrowest:g} and {widest:g} {unit}",
    )
    names = [name.casefold() for name in comfort.targets]
    _require(bool(names), "[comfort.targets] must give a target for at least one comfort setting")
    _require(
        all(name and name == name.strip() for name in names),
        "[comfort.targets] names must not be empty or begin or end with a space",
    )
    _require(
        len(set(names)) == len(names),
        "[comfort.targets] names must differ by more than upper and lower case",
    )
    lowest, highest = (convert_temperature(bound, "°C", unit) for bound in _TARGET_CELSIUS)
    for name, target in comfort.targets.items():
        _require(
            lowest <= target <= highest,
            f"comfort.targets.{name} must be between {lowest:g} and {highest:g} {unit}",
        )


def _resolved(path: Path) -> Path:
    """Return where a path really leads, or the path itself when that cannot be worked out."""
    try:
        return path.resolve()
    except (OSError, RuntimeError):  # a link that leads back to itself
        return path


def _require(holds: bool, message: str) -> None:
    if not holds:
        raise ConfigError(message)


def _require_url(url: str, where: str) -> None:
    try:
        parts = urlsplit(url)
        port = parts.port  # reading it raises when the port is not a number
        usable = parts.scheme in ("http", "https") and bool(parts.hostname) and port != 0
    except ValueError:
        usable = False
    _require(usable, f"{where} must be an http:// or https:// address")
    # Error messages quote the address, and would quote a password inside it too.
    _require(
        parts.username is None and parts.password is None,
        f"{where} must not contain a user name or password; use the token variable instead",
    )
