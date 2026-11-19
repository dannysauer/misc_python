"""Run the tool against a simulated house and thermostat for days at a time.

The house has heavy walls. The thermostat is in auto mode and follows a
schedule of two comfort settings, the way an ecobee does: at each change of
comfort setting it goes to its own setpoints for the new one, and it keeps a
pair the tool sends until the next change. The tool runs every ten minutes,
through the same ``steer`` the command line calls, with a forecast that knows
the weather to come. Each run can be compared with the same thermostat left on
one fixed kind of pair, with no tool at all.

For a longer look than the tests take, run it by hand from the project
directory:

    uv run python tests/closed_loop.py 10
"""

import copy
import functools
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np
from fakes import (
    ALL_DRIVERS,
    HOLD,
    WEATHER_UNITS,
    FakeMetrics,
    scheduled_preset,
    thermostat_attributes,
    write_config,
)
from house import STEP_SECONDS, step

from hvac_predict.advisor import Steered, steer
from hvac_predict.config import ComfortSettings, Config
from hvac_predict.decision import COOL, HEAT, Decision, Goal, Pair, Preference
from hvac_predict.homeassistant import EntityState
from hvac_predict.solar import clear_sky_irradiance, cloud_attenuation
from hvac_predict.training import TrainedModel, save_model, train

START = 1_774_000_800.0  # 2026-03-20 10:00 UTC
TRAINING_DAYS = 30
MAX_DAYS = 30  # the longest run the weather is made for
STEPS_PER_HOUR = 3600 // STEP_SECONDS
STEPS_PER_DAY = 24 * STEPS_PER_HOUR
STEPS_BETWEEN_RUNS = 2  # the tool runs every ten minutes
FORECAST_HOURS = 12
DIFFERENTIAL = 2.0
TARGETS = {"Home": 20.0, "Sleep": 17.5}
REACH = ComfortSettings(DIFFERENTIAL, TARGETS).reach
HOME_HOURS = range(6, 21)  # the schedule is at Home from 06:00 to 21:00
# The thermostat starts its equipment this far past a setpoint and stops it this far inside.
HYSTERESIS = 0.25
# A house within this many degrees of a new target has reached it, for timing how long that took.
REACHED_WITHIN = 0.25
LATITUDE, LONGITUDE = 45.0, 0.0


@dataclass(frozen=True)
class Outcome:
    """What the days measured did to the house, with the tool or without it."""

    furnace_hours: float
    cooling_hours: float
    mean_miss: float  # mean distance from the target, in degrees
    worst_miss: float
    slowest_arrival: float  # the longest the house took to reach a new target, in minutes
    changes: int  # how many times the tool moved the setpoints
    rules: Counter[str]  # how many of the tool's decisions each rule made
    fought_drifts: int  # steps in which the equipment a drift was meant to keep off ran anyway

    def __str__(self) -> str:
        return (
            f"furnace {self.furnace_hours:5.1f} h, cooling {self.cooling_hours:5.1f} h, "
            f"off target by {self.mean_miss:.2f} on average and {self.worst_miss:.2f} at worst, "
            f"a new target reached in {self.slowest_arrival:3.0f} min at the slowest, "
            f"{self.changes} setpoint changes"
        )


@dataclass
class Weather:
    times: np.ndarray
    outdoor: np.ndarray
    cloud: np.ndarray
    solar: np.ndarray
    wind: np.ndarray
    rain: np.ndarray


@dataclass
class Simulation:
    """A house, its auto-mode thermostat and their history, advanced one step at a time."""

    weather: Weather
    air: float = 20.0
    walls: float = 19.5
    setpoints: Pair = (20.0, 22.0)
    preset: str = scheduled_preset("Home")
    heating_on: bool = False
    cooling_on: bool = False
    index: int = 0
    indoor: np.ndarray = field(init=False)
    heating: np.ndarray = field(init=False)
    cooling: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        size = len(self.weather.times)
        self.indoor = np.full(size, np.nan)
        self.heating, self.cooling = np.zeros(size), np.zeros(size)

    @property
    def now(self) -> float:
        return float(self.weather.times[self.index])

    @property
    def comfort(self) -> str:
        return comfort_at(self.now)

    def history(self) -> dict[str, np.ndarray]:
        """Return every series VictoriaMetrics would hold by now."""
        known = slice(0, self.index + 1)
        weather = self.weather
        return {
            "indoor": self.indoor[known],
            "outdoor": weather.outdoor[known],
            "solar": weather.solar[known],
            "wind": weather.wind[known],
            "rain": weather.rain[known],
            "heating": self.heating[known],
            "cooling": self.cooling[known],
        }

    def follow_schedule(self, program: Preference) -> None:
        """At a change of comfort setting, go to the thermostat's own setpoints for the new one."""
        if self.index and self.comfort == comfort_at(self.now - STEP_SECONDS):
            return
        self.setpoints = Goal(TARGETS[self.comfort], DIFFERENTIAL, REACH).pair(program)
        self.preset = scheduled_preset(self.comfort)

    def read(self) -> None:
        """Take this step's indoor reading, which is what the tool sees when it runs now."""
        self.indoor[self.index] = self.air

    def advance(self) -> None:
        """Let the thermostat act on its setpoints, then move the house on one step."""
        low, high = self.setpoints
        cold, warm = self.air < low - HYSTERESIS, self.air > high + HYSTERESIS
        self.heating_on = cold or (self.heating_on and self.air < low + HYSTERESIS)
        self.cooling_on = warm or (self.cooling_on and self.air > high - HYSTERESIS)
        weather, index = self.weather, self.index
        self.heating[index], self.cooling[index] = self.heating_on, self.cooling_on
        self.air, self.walls = step(
            self.air,
            self.walls,
            outdoor=weather.outdoor[index],
            solar=weather.solar[index],
            wind=weather.wind[index],
            rain=weather.rain[index],
            heating=self.heating_on,
            cooling=self.cooling_on,
            has_walls=True,
        )
        self.index += 1

    def ran(self, equipment: Preference | None) -> bool:
        """Say whether the furnace (HEAT) or the air conditioner (COOL) ran in the last step."""
        return {HEAT: self.heating_on, COOL: self.cooling_on, None: False}[equipment]


class SimulatedHome:
    """Home Assistant, as far as the tool can see it, in front of a simulation."""

    def __init__(self, simulation: Simulation) -> None:
        self._simulation = simulation
        self.changes = 0

    def state(self, entity_id: str) -> EntityState:
        if entity_id.startswith("weather"):
            return EntityState(entity_id, "sunny", WEATHER_UNITS)
        house = self._simulation
        attributes = thermostat_attributes(
            comfort=house.comfort, preset=house.preset, setpoints=house.setpoints, reads=house.air
        )
        return EntityState(entity_id, "heat_cool", attributes)

    def temperature_unit(self) -> str:
        return "°C"

    def hourly_forecast(self, entity_id: str) -> list[dict]:
        """Return a forecast that knows the weather to come, as the best case."""
        weather, index = self._simulation.weather, self._simulation.index
        this_hour = index - index % STEPS_PER_HOUR
        hours = range(this_hour, this_hour + (FORECAST_HOURS + 1) * STEPS_PER_HOUR, STEPS_PER_HOUR)
        return [
            {
                "datetime": datetime.fromtimestamp(weather.times[at], UTC).isoformat(),
                "temperature": float(weather.outdoor[at]),
                "wind_speed": float(weather.wind[at]),
                "cloud_coverage": float(100 * weather.cloud[at]),
                "precipitation": 0.0,
            }
            for at in hours
        ]

    def set_temperature_range(self, entity_id: str, low: float, high: float) -> None:
        self._simulation.setpoints, self._simulation.preset = (low, high), HOLD
        self.changes += 1


def comfort_at(time: float) -> str:
    return "Home" if datetime.fromtimestamp(time, UTC).hour in HOME_HOURS else "Sleep"


def simulate(
    days: int,
    *,
    tool: bool = True,
    program: Preference = HEAT,
    mean_outdoor: float = 14.0,
    **decision: float,
) -> Outcome:
    """Run the house for ``days`` after a month of training.

    ``program`` is the kind of pair the thermostat's own schedule sets at each
    change of comfort setting. With the tool, ``decision`` overrides its
    settings. Without it, the thermostat stays on the program's pairs.
    """
    simulation, model = trained_house(mean_outdoor)
    # The measured days begin on the program's own pair, not on the one training ended with.
    simulation.setpoints = Goal(TARGETS[simulation.comfort], DIFFERENTIAL, REACH).pair(program)
    home = SimulatedHome(simulation)
    first = simulation.index
    rules: Counter[str] = Counter()
    fought = 0
    spared: Preference | None = None  # the equipment a drift under way is meant to keep off
    with TemporaryDirectory() as directory:
        config = _config(Path(directory), decision)
        save_model(model, config.model_path)
        for _ in range(days * STEPS_PER_DAY):
            simulation.follow_schedule(program)
            simulation.read()
            if tool and simulation.index % STEPS_BETWEEN_RUNS == 0:
                metrics = FakeMetrics(simulation.history(), end=simulation.now)
                steered = steer(config, metrics, home, now=simulation.now, apply=True)
                assert isinstance(steered, Steered)
                made = steered.advice.decision
                rules[made.rule] += 1
                spared = _spared_by(made)
            simulation.advance()
            fought += simulation.ran(spared)
    measured = slice(first, simulation.index)
    targets = np.array([TARGETS[comfort_at(time)] for time in simulation.weather.times[measured]])
    misses = np.abs(simulation.indoor[measured] - targets)
    return Outcome(
        furnace_hours=float(simulation.heating[measured].sum()) / STEPS_PER_HOUR,
        cooling_hours=float(simulation.cooling[measured].sum()) / STEPS_PER_HOUR,
        mean_miss=float(misses.mean()),
        worst_miss=float(misses.max()),
        slowest_arrival=_slowest_arrival(targets, misses),
        changes=home.changes,
        rules=rules,
        fought_drifts=int(fought),
    )


def _spared_by(decision: Decision) -> Preference | None:
    """Return the equipment a decision means to keep off, if it lets the house drift."""
    if decision.rule != "drift":
        return None
    # A house drifting down holds the heat preference, and it is the air conditioner that waits.
    return COOL if decision.preference == HEAT else HEAT


def _slowest_arrival(targets: np.ndarray, misses: np.ndarray) -> float:
    """Return the longest the house took to reach a changed target, in minutes.

    A target the house had not reached when the next one came counts for as
    long as it was in force. This is reported for the receipt; no test rests on it.
    """
    slowest, changed_at = 0, None
    for index in range(1, len(targets)):
        if targets[index] != targets[index - 1]:
            if changed_at is not None:
                slowest = max(slowest, index - changed_at)
            changed_at = index
        if changed_at is not None and misses[index] <= REACHED_WITHIN:
            slowest = max(slowest, index - changed_at)
            changed_at = None
    return slowest * STEP_SECONDS / 60


def trained_house(mean_outdoor: float) -> tuple[Simulation, TrainedModel]:
    """Return a house a month into its life, and a model fitted to that month."""
    simulation, model = _trained_house(mean_outdoor)
    return copy.deepcopy(simulation), model


@functools.cache
def _trained_house(mean_outdoor: float) -> tuple[Simulation, TrainedModel]:
    simulation = Simulation(_weather((TRAINING_DAYS + MAX_DAYS + 1) * STEPS_PER_DAY, mean_outdoor))
    for _ in range(TRAINING_DAYS * STEPS_PER_DAY):
        simulation.follow_schedule(HEAT)
        simulation.read()
        simulation.advance()
    simulation.read()  # the reading training ends on, which the first measured step takes again
    with TemporaryDirectory() as directory:
        config = _config(Path(directory), {})
    model = train(config, FakeMetrics(simulation.history(), end=simulation.now), now=simulation.now)
    return simulation, model


def _config(directory: Path, decision: dict[str, Any]) -> Config:
    return write_config(
        directory,
        history=ALL_DRIVERS,
        training={"days": float(TRAINING_DAYS)},
        comfort={"differential": DIFFERENTIAL, "targets": TARGETS},
        decision=decision,
    )


def _weather(size: int, mean_outdoor: float) -> Weather:
    random = np.random.default_rng(4)
    times = START + STEP_SECONDS * np.arange(size)
    day = times / 86400
    outdoor = (
        mean_outdoor
        + 8 * np.sin(2 * np.pi * (day - 0.4))
        + 4 * np.sin(2 * np.pi * day / 6)
        + np.cumsum(random.normal(0, 0.02, size))
    )
    cloud = np.repeat(random.uniform(0, 1, size // STEPS_PER_DAY + 2), STEPS_PER_DAY)[:size]
    solar = clear_sky_irradiance(times, LATITUDE, LONGITUDE) * cloud_attenuation(cloud)
    wind = np.abs(np.convolve(random.normal(0, 25, size), np.ones(36) / 36, mode="same"))
    return Weather(times, outdoor, cloud, solar, wind, np.zeros(size))


if __name__ == "__main__":
    length = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    for mean in (10.0, 14.0, 18.0):
        print(f"Mean outdoor temperature {mean:g} °C, {length} days:")
        for fixed in (HEAT, COOL):
            without = simulate(length, tool=False, program=fixed, mean_outdoor=mean)
            print(f"  fixed {fixed} pair          {without}")
        for minutes, hold in ((480.0, 30.0), (90.0, 30.0), (90.0, 0.0), (30.0, 30.0)):
            steered = simulate(
                length, mean_outdoor=mean, overshoot_minutes=minutes, min_hold_minutes=hold
            )
            kept = "" if hold else ", no hold"
            print(f"  the tool, {minutes:3.0f} min ahead{kept}  {steered}")
            print(
                f"      decisions: {dict(sorted(steered.rules.items()))}; "
                f"equipment ran against a drift in {steered.fought_drifts} steps"
            )
