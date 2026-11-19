"""Command line interface."""

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .advisor import Steered, steer
from .config import Config, load_config
from .decision import Outlook, Pair
from .errors import ConfigError, DataError, HvacPredictError
from .homeassistant import HomeAssistant
from .thermal import FEATURES
from .thermostat import AUTO, LeftAlone
from .training import HOLDOUT_SHARE, TrainedModel, save_model, train
from .victoriametrics import VictoriaMetrics

_HOME_ASSISTANT_TOKEN = "HASS_TOKEN"
_VICTORIAMETRICS_TOKEN = "VICTORIAMETRICS_TOKEN"
_EPILOG = f"""\
Give --config before the command. {_HOME_ASSISTANT_TOKEN} holds a Home Assistant long-lived
access token, which decide needs. {_VICTORIAMETRICS_TOKEN} holds a bearer token for
VictoriaMetrics, if it wants one. The exit status is 0 on success, 1 when the
configuration, the data or a server is at fault, and 2 for a mistake on the command line."""


@dataclass(frozen=True)
class _Report:
    """What --json prints: the same keys whatever happened, empty where nothing was decided."""

    preference: str | None = None
    setpoints: list[float] | None = None
    rule: str | None = None
    reason: str | None = None
    current_setpoints: list[float] | None = None
    comfort: str | None = None
    target: float | None = None
    applied: bool = False
    left_alone: str | None = None
    outlook: list[dict[str, float]] = field(default_factory=list)


def main(argv: list[str] | None = None, *, clock: Callable[[], float] = time.time) -> int:
    """Run one command and return the process exit status."""
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
        metrics = VictoriaMetrics.connect(
            config.victoriametrics.url, _token(_VICTORIAMETRICS_TOKEN)
        )
        if args.command == "train":
            _train(config, metrics, clock())
        else:
            _decide(config, metrics, clock(), apply=args.apply, as_json=args.json)
    except HvacPredictError as error:
        print(f"hvac-predict: {error}", file=sys.stderr)
        return 1
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hvac-predict",
        description="Choose a Home Assistant thermostat's heating and cooling setpoints "
        "ahead of the weather.",
        epilog=_EPILOG,
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=Path("hvac-predict.toml"),
        help="configuration file (default: %(default)s in the current directory)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "train",
        help="fit the thermal model to VictoriaMetrics history",
        description="Fit the thermal model to the history in VictoriaMetrics, print a check of "
        "it, and write it to model_path in place of any model already there. A model that "
        "fails the check is not written. Home Assistant is not contacted.",
    )
    decide = commands.add_parser(
        "decide",
        help="predict the house and choose the thermostat's setpoints",
        description="Predict the indoor temperature with heating and cooling off, and choose "
        "the pair of setpoints the thermostat should hold. The thermostat is not changed "
        f"without --apply. Needs {_HOME_ASSISTANT_TOKEN} and a model written by train. "
        f"Leaves the thermostat alone, and exits 0, when it is not in {AUTO} mode, holds "
        "another preset or a hold this tool did not place, has no target for its comfort "
        "setting, or the enable entity is not on. Records what it saw in state_path "
        "whenever it reaches a decision, with or without --apply.",
    )
    decide.add_argument(
        "--apply", action="store_true", help="set the thermostat instead of only reporting"
    )
    decide.add_argument("--json", action="store_true", help="print the result as JSON")
    return parser


def _token(variable: str) -> str | None:
    return os.environ.get(variable, "").strip() or None


def _train(config: Config, metrics: VictoriaMetrics, now: float) -> None:
    trained = train(config, metrics, now=now)
    print(_training_report(trained, config))
    if shortcoming := trained.shortcoming():
        raise DataError(f"{shortcoming}; the model was not written to {config.model_path}")
    save_model(trained, config.model_path)
    print(f"\nModel written to {config.model_path}")


def _decide(
    config: Config, metrics: VictoriaMetrics, now: float, *, apply: bool, as_json: bool
) -> None:
    token = _token(_HOME_ASSISTANT_TOKEN)
    if not token:
        raise ConfigError(
            f"set {_HOME_ASSISTANT_TOKEN} to a Home Assistant long-lived access token"
        )
    home = HomeAssistant.connect(config.homeassistant.url, token)
    outcome = steer(config, metrics, home, now=now, apply=apply)
    if as_json:
        print(json.dumps(asdict(_report(outcome))))
    elif isinstance(outcome, LeftAlone):
        print(f"Not steering the thermostat: {outcome.reason}")
    else:
        print(_decision_report(outcome, config.units.temperature))


def _training_report(trained: TrainedModel, config: Config) -> str:
    unit = config.units.temperature
    lines = [
        f"Fitted {trained.windows} windows from {trained.history_days:.0f} days of history; "
        f"typical error in the rate of change is {trained.rate_error:.2f} {unit}/h.",
        "",
        "Weights:",
        *(f"  {name:<13}{trained.model.coefficients[name]:+.4g}" for name in FEATURES),
        "",
        f"Walls and furniture hold on to heat for about {trained.model.lag_hours:g} h, "
        "the lag time that predicted best.",
        "",
        f"Check on the newest {HOLDOUT_SHARE:.0%} of the history, predicted by a fit to the rest:",
    ]
    if not trained.holdout:
        lines.append("  no gap-free stretch was long enough to check against")
    for horizon in trained.holdout:
        lines.append(
            f"  {horizon.hours:g} h ahead: off by {horizon.model_error:.2f} {unit} on average, "
            f"against {horizon.no_change_error:.2f} {unit} for assuming no change "
            f"({horizon.runs} runs)"
        )
    return "\n".join(lines)


def _decision_report(outcome: Steered, unit: str) -> str:
    advice = outcome.advice
    outlook, decision, thermostat = advice.outlook, advice.decision, advice.thermostat
    holds = _pair(thermostat.setpoints, unit) if thermostat.setpoints else "no pair of setpoints"
    chosen = _pair(decision.setpoints, unit)
    lines = [
        f"Indoor {outlook.indoor[0]:.1f} {unit}, outdoor {outlook.outdoor[0]:.1f} {unit}. "
        f"Comfort setting {thermostat.comfort!r} aims for {advice.goal.target:g} {unit}.",
        f"The thermostat holds {holds}.",
        "",
        "Left alone, the house is predicted to be at:",
        *(
            f"  +{minutes / 60:.0f} h  {indoor:5.1f} {unit}  (outdoor {outdoor:.1f})"
            for minutes, indoor, outdoor in _hourly(outlook)
        ),
        "",
        f"Decision: {decision.preference} preference, {chosen}. {decision.reason}",
    ]
    if outcome.applied:
        lines.append(f"Set the thermostat to {chosen}.")
    elif advice.calls_for_a_change:
        lines.append("Dry run: the thermostat was not changed. Pass --apply to set it.")
    else:
        lines.append("The thermostat already holds those setpoints.")
    return "\n".join(lines)


def _pair(setpoints: Pair, unit: str) -> str:
    return f"{setpoints[0]:.1f} to {setpoints[1]:.1f} {unit}"


def _report(outcome: Steered | LeftAlone) -> _Report:
    if isinstance(outcome, LeftAlone):
        return _Report(left_alone=outcome.reason)
    advice = outcome.advice
    thermostat = advice.thermostat
    return _Report(
        preference=advice.decision.preference,
        setpoints=list(advice.decision.setpoints),
        rule=advice.decision.rule,
        reason=advice.decision.reason,
        current_setpoints=list(thermostat.setpoints) if thermostat.setpoints else None,
        comfort=thermostat.comfort,
        target=advice.goal.target,
        applied=outcome.applied,
        outlook=[
            {"minutes": minutes, "indoor": round(indoor, 2), "outdoor": round(outdoor, 2)}
            for minutes, indoor, outdoor in _hourly(advice.outlook)
        ],
    )


def _hourly(outlook: Outlook) -> list[tuple[float, float, float]]:
    """Return the outlook at each whole hour after now."""
    whole_hours = np.flatnonzero(outlook.minutes % 60 == 0)[1:]
    return [
        (float(outlook.minutes[i]), float(outlook.indoor[i]), float(outlook.outdoor[i]))
        for i in whole_hours
    ]
