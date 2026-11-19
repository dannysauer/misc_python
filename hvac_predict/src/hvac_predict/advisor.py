"""Run the tool once: predict the house left alone, choose a pair, and set it.

``steer`` is the whole of a run, and the command line and the simulation both
call it. ``advise`` is the part that changes nothing. Whether the thermostat
is the tool's to steer at all is decided in ``thermostat.read_thermostat``,
the prediction is made in ``outlook.py``, and the pair is chosen in
``decision.py``.
"""

from dataclasses import dataclass

from .config import Config
from .decision import Decision, Goal, Outlook, decide
from .errors import DataError
from .files import held_by_one_run
from .forecast import parse_forecast
from .homeassistant import HomeAssistant
from .outlook import look_ahead, recent_history
from .state import State, load_state, observe, save_state
from .thermostat import LeftAlone, Thermostat, read_thermostat, send_setpoints
from .training import TrainedModel, load_model
from .units import convert_temperature_difference
from .victoriametrics import VictoriaMetrics

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
    weather, comfort = config.homeassistant.weather_entity, config.comfort
    history = recent_history(metrics, config, trained.model, now)
    forecast = parse_forecast(
        home.hourly_forecast(weather), home.state(weather).attributes, config.units
    )
    outlook = look_ahead(config, trained, history, forecast)
    indoor = float(outlook.indoor[0])
    _require_agreement(indoor, thermostat)

    goal = Goal(thermostat.target, comfort.differential, comfort.reach)
    state = observe(previous, target=goal.target, indoor=indoor, now=now).having_seen(
        history.grid.times, history.indoor, goal.tolerance
    )
    decision = decide(
        outlook,
        goal,
        config.decision,
        now=now,
        change=state.change,
        choice=state.choice,
        holds=thermostat.preference,
    )
    return Advice(thermostat, goal, outlook, decision, state)


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
