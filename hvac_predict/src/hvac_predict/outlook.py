"""Predict the house left alone, from the newest readings to the end of the forecast."""

import dataclasses

import numpy as np

from .config import Config
from .decision import Outlook
from .errors import DataError
from .forecast import Conditions, Forecast, future_drivers
from .grid import TimeGrid
from .history import History, load_history
from .limits import Limits
from .thermal import Drivers, ThermalModel, predict
from .training import TrainedModel
from .victoriametrics import VictoriaMetrics

# The smoothed terms need this many lag times of history to settle.
_WARMUP_LAGS = 4
_MAX_READING_AGE_MINUTES = 20
_MIN_OUTLOOK_HOURS = 1


def recent_history(
    metrics: VictoriaMetrics, config: Config, model: ThermalModel, now: float
) -> History:
    """Load the history a prediction made at ``now`` starts from."""
    warmup_seconds = _WARMUP_LAGS * model.lag_hours * 3600
    return load_history(metrics, config, TimeGrid.covering(now, warmup_seconds, model.step_seconds))


def look_ahead(
    config: Config, trained: TrainedModel, history: History, forecast: Forecast
) -> Outlook:
    """Predict the house with its equipment off, from now to the end of the forecast."""
    model = trained.model
    _require_fresh(history, round(_MAX_READING_AGE_MINUTES * 60 / model.step_seconds))
    newest = history.drivers.gaps_filled()
    current = Conditions(
        time=history.grid.end, outdoor=float(newest.outdoor[-1]), wind=float(newest.wind[-1])
    )

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


def _require_fresh(history: History, max_age_steps: int) -> None:
    """Refuse a history in which some series has no reading new enough to act on."""
    for name, values in {"indoor": history.indoor, **history.drivers.series()}.items():
        if not np.isfinite(values[-(max_age_steps + 1) :]).any():
            raise DataError(
                f"VictoriaMetrics has no believable {name} reading from the last "
                f"{_MAX_READING_AGE_MINUTES} min"
            )


def _idle_from_now(drivers: Drivers) -> Drivers:
    """Return the history with the equipment off at its last point, which is now.

    The prediction is for a house left alone, and each step is driven by the
    conditions at its start, so what the equipment is doing this minute must
    not carry into the first step.
    """
    heating, cooling = drivers.heating.copy(), drivers.cooling.copy()
    heating[-1] = cooling[-1] = 0.0
    return dataclasses.replace(drivers, heating=heating, cooling=cooling)
