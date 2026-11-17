"""Run the tool once: predict the house left alone, choose a pair, and set it.

``steer`` is the whole of a run, and the command line and the simulation both
call it. ``advise`` is the part that changes nothing. Whether the thermostat
is the tool's to steer at all is decided in ``thermostat.read_thermostat``.
"""

import dataclasses
from dataclasses import dataclass

import numpy as np

from .config import Config
from .decision import Decision, Goal, Outlook, Settling, Standing, decide
from .errors import DataError
from .files import held_by_one_run
from .forecast import Conditions, Forecast, future_drivers, parse_forecast
from .grid import TimeGrid
from .history import History, load_history
from .homeassistant import HomeAssistant
from .limits import Limits
from .state import State, load_state, observe, save_state
from .thermal import Drivers, predict
from .thermostat import LeftAlone, Thermostat, read_thermostat, send_setpoints
from .training import TrainedModel, load_model
from .units import convert_temperature_difference
from .victoriametrics import VictoriaMetrics

# The smoothed terms need this many lag times of history to settle.
_WARMUP_LAGS = 4
_MAX_READING_AGE_MINUTES = 20
_MIN_OUTLOOK_HOURS = 1
# How far the history's indoor temperature may be from the thermostat's own
# reading before one of them has to be wrong.
_MAX_DISAGREEMENT_CELSIUS = 3.0


@dataclass(frozen=True)
class Advice:
    """What the thermostat should hold and why, with temperatures in the units of the history."""

    thermostat: Thermostat  # as this run found it
    goal: Goal
    outlook: Outlook
    decision: Decision
    state: State  # what this run saw, before anything it goes on to set

    @property
    def calls_for_a_change(self) -> bool:
        return not self.thermostat.is_set_to(self.decision.setpoints)


@dataclass(frozen=True)
class Steered:
    advice: Advice
    applied: bool  # whether the thermostat's setpoints were changed


def steer(
    config: Config, metrics: VictoriaMetrics, home: HomeAssistant, *, now: float, apply: bool
) -> Steered | LeftAlone:
    """Run once: read the thermostat, decide, and with ``apply`` set its setpoints."""
    with held_by_one_run(config.state_path):
        previous = load_state(config.state_path, now=now)
        thermostat = read_thermostat(config, home, previous.sent if previous else ())
        if isinstance(thermostat, LeftAlone):
            return thermostat
        trained = load_model(config.model_path, config)
        advice = advise(config, trained, metrics, home, thermostat, previous, now=now)
        decision = advice.decision
        if not (apply and advice.calls_for_a_change):
            save_state(advice.state, config.state_path)
            return Steered(advice, applied=False)
        # Written before anything is sent. A state file that cannot be written
        # stops the run while the thermostat is still untouched, and the pair
        # about to be sent is on record as the tool's own before it can be seen.
        state = advice.state.sending(decision.setpoints, over=thermostat.own_hold)
        save_state(state, config.state_path)
        send_setpoints(home, thermostat, decision.setpoints)
        # Only a preference the thermostat was really moved to counts as chosen now.
        if thermostat.preference != decision.preference:
            save_state(state.having_chosen(decision.preference, now), config.state_path)
        return Steered(advice, applied=True)


def advise(
    config: Config,
    trained: TrainedModel,
    metrics: VictoriaMetrics,
    home: HomeAssistant,
    thermostat: Thermostat,
    previous: State | None,
    *,
    now: float,
) -> Advice:
    """Work out which setpoints the thermostat should hold, without changing anything."""
    model, weather = trained.model, config.homeassistant.weather_entity
    warmup_seconds = _WARMUP_LAGS * model.lag_hours * 3600
    history = load_history(
        metrics, config, TimeGrid.covering(now, warmup_seconds, model.step_seconds)
    )
    forecast = parse_forecast(
        home.hourly_forecast(weather), home.state(weather).attributes, config.units
    )
    outlook = _outlook(config, trained, history, forecast)
    indoor = float(outlook.indoor[0])
    _require_agreement(indoor, thermostat)

    goal = Goal(thermostat.target, config.comfort.differential)
    state = observe(previous, target=goal.target, indoor=indoor, now=now).having_seen(
        history.grid.times, history.indoor, goal.tolerance
    )
    decision = decide(
        outlook,
        goal,
        config.decision,
        settling=_settling(state, config, now),
        standing=_standing(state, thermostat, now),
    )
    return Advice(thermostat, goal, outlook, decision, state)


def _outlook(
    config: Config, trained: TrainedModel, history: History, forecast: Forecast
) -> Outlook:
    """Predict the house left alone, from now to the end of the forecast."""
    model = trained.model
    max_age_steps = round(_MAX_READING_AGE_MINUTES * 60 / model.step_seconds)
    _newest(history.indoor, "indoor", max_age_steps)
    newest = {
        spec.name: _newest(getattr(history.drivers, spec.name), spec.name, max_age_steps)
        for spec in dataclasses.fields(Drivers)
    }
    current = Conditions(time=history.grid.end, outdoor=newest["outdoor"], wind=newest["wind"])

    covered = min(config.decision.horizon_hours * 3600, forecast.end - current.time)
    if covered < _MIN_OUTLOOK_HOURS * 3600:
        raise DataError(f"the hourly forecast reaches less than {_MIN_OUTLOOK_HOURS} h ahead")
    steps = int(covered // model.step_seconds)
    future = future_drivers(
        forecast,
        current.time + model.step_seconds * np.arange(1, steps + 1),
        current,
        location=config.location,
        light_scale=trained.light_scale,
        wind_scale=config.forecast.wind_scale,
    )

    # The run begins at the newest indoor reading, which can be a few steps old,
    # and its last points are the ones from now on.
    run = predict(model, history.indoor, _idle_from_now(history.drivers).followed_by(future))
    indoor = run[-(steps + 1) :]
    low, high = Limits.in_units(config.units).indoor
    if not ((indoor >= low) & (indoor <= high)).all():
        raise DataError(
            "the prediction leaves the range a house can be in; check the model and the forecast"
        )
    return Outlook(
        minutes=model.step_seconds / 60 * np.arange(steps + 1),
        indoor=indoor,
        outdoor=np.concatenate([[current.outdoor], future.outdoor]),
    )


def _newest(values: np.ndarray, name: str, max_age_steps: int) -> float:
    """Return a series' newest reading, refusing one too old to act on."""
    recent = values[-(max_age_steps + 1) :]
    readings = recent[np.isfinite(recent)]
    if not readings.size:
        raise DataError(
            f"VictoriaMetrics has no believable {name} reading from the last "
            f"{_MAX_READING_AGE_MINUTES} min"
        )
    return float(readings[-1])


def _idle_from_now(drivers: Drivers) -> Drivers:
    """Return the history with the equipment off at its last point, which is now.

    The prediction is for a house left alone, and each step is driven by the
    conditions at its start, so what the equipment is doing this minute must
    not carry into the first step.
    """
    heating, cooling = drivers.heating.copy(), drivers.cooling.copy()
    heating[-1] = cooling[-1] = 0.0
    return dataclasses.replace(drivers, heating=heating, cooling=cooling)


def _require_agreement(indoor: float, thermostat: Thermostat) -> None:
    """Refuse to go on unless the history and the thermostat agree about the house.

    Every decision starts from the history's indoor temperature, and a wrong
    query, a wrong unit or a dead sensor would have the tool steer a house
    that is not there.
    """
    own, unit = thermostat.temperature, thermostat.unit
    if own is None:
        raise DataError(
            f"{thermostat.entity_id} reports no temperature of its own to check "
            "history.indoor_temp against"
        )
    allowed = convert_temperature_difference(_MAX_DISAGREEMENT_CELSIUS, "°C", unit)
    if abs(own - indoor) > allowed:
        raise DataError(
            f"history.indoor_temp puts the house at {indoor:.1f} {unit} but "
            f"{thermostat.entity_id} reads {own:.1f} {unit}; not acting on readings that disagree"
        )


def _settling(state: State, config: Config, now: float) -> Settling | None:
    """Return how a house on its way to a changed target stands, or None if it is not."""
    change = state.change
    if change is None:
        return None
    elapsed = (now - change.at) / 60
    # The pair of some target held the house until the change, so it started
    # within their reach. Holding the start to that bounds how far the idle
    # setpoint of a drift can be moved, whatever a state file or a sensor says.
    low, high = config.comfort.reach
    return Settling(
        minutes_left=max(config.decision.coast_minutes - elapsed, 0.0),
        started_from=min(max(change.started_from, low), high),
    )


def _standing(state: State, thermostat: Thermostat, now: float) -> Standing | None:
    """Return the tool's last choice of preference, if the thermostat still holds it."""
    choice = state.choice
    if choice is None or thermostat.preference != choice.preference:
        return None
    return Standing(choice.preference, (now - choice.at) / 60)
