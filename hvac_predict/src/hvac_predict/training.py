"""Fit the thermal model to VictoriaMetrics history and keep it as a JSON file."""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np

from .config import Config
from .errors import ConfigError, DataError
from .files import write_atomically
from .grid import TimeGrid
from .history import load_history
from .solar import clear_sky_irradiance, light_per_irradiance
from .thermal import FEATURES, Fit, HorizonError, ThermalModel, backtest, fit
from .victoriametrics import VictoriaMetrics

_FORMAT = 1
# The history is split by age. The oldest part fits a model for each candidate
# lag time, the next part picks the lag time, and the newest part is kept back
# to report how well that choice predicts history it had no hand in.
TUNING_SHARE, HOLDOUT_SHARE = 0.2, 0.2
_LAG_CANDIDATES_HOURS = (1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 32.0, 48.0)
_HORIZONS_HOURS = (1.0, 2.0, 4.0, 8.0)


@dataclass(frozen=True)
class TrainedModel:
    """A fitted model together with what is needed to use and judge it.

    ``model``, ``windows`` and ``rate_error`` describe the fit to all of the
    history. ``holdout`` is how a model fitted the same way to the older part
    of the history predicted the newest part, which the saved model has seen.
    """

    model: ThermalModel
    # What the light sensor reads per W/m² of clear-sky irradiance, to turn a
    # cloud forecast into its units. None when no light sensor is configured.
    light_scale: float | None
    inputs: dict[str, Any]
    trained_at: str
    history_days: float
    windows: int
    rate_error: float
    holdout: list[HorizonError]

    def shortcoming(self) -> str | None:
        """Return why this model should not control a thermostat, if there is a reason."""
        if not self.model.follows_outdoors:
            return "the fit found no link between outdoor and indoor temperature"
        if not self.holdout:
            return "no gap-free stretch of history was long enough to check the model against"
        if _mean_error(self.holdout) >= np.mean([each.no_change_error for each in self.holdout]):
            return "the model predicts no better than assuming the temperature does not change"
        return None


def train(config: Config, metrics: VictoriaMetrics, *, now: float) -> TrainedModel:
    """Fit the model to the configured history and check it against the newest part.

    The lag time of the smoothed terms is not a weight the fit can solve for, so
    each candidate is tried and the one that predicts best is kept.
    """
    settings = config.training
    grid = TimeGrid.covering(now, settings.days * 86400, settings.step_seconds)
    history = load_history(metrics, config, grid)
    readings = np.flatnonzero(np.isfinite(history.indoor))
    if not readings.size:
        raise DataError("history.indoor_temp returned no readings for the training period")
    # The history may be shorter than the period asked for; split what there is.
    span = grid.size - readings[0]
    holdout_start = grid.size - int(span * HOLDOUT_SHARE)
    tuning_start = holdout_start - int(span * TUNING_SHARE)

    fit_history = partial(
        fit,
        history.indoor,
        history.drivers,
        step_seconds=settings.step_seconds,
        window_steps=settings.window_steps,
    )
    replay = partial(
        backtest, indoor=history.indoor, drivers=history.drivers, horizons_hours=_HORIZONS_HOURS
    )
    lag_hours = _best_lag(fit_history, replay, tuning_start, holdout_start)
    older = fit_history(lag_hours=lag_hours, until=holdout_start)
    final = fit_history(lag_hours=lag_hours)

    light_scale = None
    if config.history.solar:
        place = config.location
        clear_sky = clear_sky_irradiance(grid.times, place.latitude, place.longitude)
        light_scale = light_per_irradiance(history.drivers.solar, clear_sky)
    return TrainedModel(
        model=final.model,
        light_scale=light_scale,
        inputs=model_inputs(config),
        trained_at=datetime.fromtimestamp(now, UTC).isoformat(timespec="seconds"),
        history_days=span * settings.step_seconds / 86400,
        windows=final.windows,
        rate_error=final.rate_error,
        holdout=replay(older.model, first=holdout_start),
    )


def model_inputs(config: Config) -> dict[str, Any]:
    """Return the settings a model is only valid for; changing any of them needs a retrain."""
    return {
        "units": asdict(config.units),
        "history": asdict(config.history),
        "location": asdict(config.location),
        "training": asdict(config.training),
    }


def save_model(trained: TrainedModel, path: Path) -> None:
    """Write the model file, replacing any old one in a single step."""
    write_atomically(path, json.dumps({"format": _FORMAT, **asdict(trained)}, indent=2) + "\n")


def load_model(path: Path, config: Config) -> TrainedModel:
    """Read a model file and check that it still matches the configuration."""
    try:
        stored = json.loads(path.read_text())
    except FileNotFoundError as error:
        raise ConfigError(f"no model at {path}; run `hvac-predict train` first") from error
    except (OSError, ValueError) as error:
        raise ConfigError(f"cannot read the model at {path}: {error}") from error
    try:
        if stored.pop("format") != _FORMAT:
            raise ValueError(f"expected format {_FORMAT}")
        stored["model"] = ThermalModel(**stored["model"])
        if set(stored["model"].coefficients) != set(FEATURES):
            raise ValueError("the weights are not the ones this version fits")
        stored["holdout"] = [HorizonError(**entry) for entry in stored["holdout"]]
        trained = TrainedModel(**stored)
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ConfigError(
            f"{path} is not a model this version understands; run `hvac-predict train` again"
        ) from error
    if trained.inputs != model_inputs(config):
        raise ConfigError(
            "the units, history queries, location or training settings changed since "
            f"{path} was trained; run `hvac-predict train` again"
        )
    return trained


def _best_lag(
    fit_history: Callable[..., Fit],
    replay: Callable[..., list[HorizonError]],
    tuning_start: int,
    holdout_start: int,
) -> float:
    """Return the lag time whose fit to the oldest history predicts the next part best."""
    drift = {}
    for lag_hours in _LAG_CANDIDATES_HOURS:
        model = fit_history(lag_hours=lag_hours, until=tuning_start).model
        if errors := replay(model, first=tuning_start, stop=holdout_start):
            drift[lag_hours] = _mean_error(errors)
    if not drift:
        raise DataError(
            f"the history has no gap-free stretch of {max(_HORIZONS_HOURS):g} h to tune the "
            "model on; check the history queries for missing data, or train on more days"
        )
    return min(drift, key=drift.__getitem__)


def _mean_error(errors: list[HorizonError]) -> float:
    return float(np.mean([each.model_error for each in errors]))
