"""A leaky house with steady readings, and ways to ask the tool about it.

Shared by the tests of ``advise``, ``steer`` and the outlook.
"""

import numpy as np
from fakes import NOW, FakeHome, FakeMetrics, hourly_forecast, write_config
from house import STEP_SECONDS

from hvac_predict.advisor import Advice, advise
from hvac_predict.decision import Change, Choice
from hvac_predict.state import State
from hvac_predict.thermal import FEATURES, ThermalModel
from hvac_predict.thermostat import Thermostat, read_thermostat
from hvac_predict.training import TrainedModel

HISTORY_POINTS = 12 * 12 + 1  # twelve hours at five-minute steps
COLD_FORECAST = hourly_forecast(12, temperature=5.0)
MILD_FORECAST = hourly_forecast(12, temperature=21.0)
# Each five-minute step closes this share of the gap to outdoors when the envelope weight is 0.1.
KEPT = 1 - 0.1 / 12


def trained(*, light_scale: float | None = None, **weights: float) -> TrainedModel:
    return TrainedModel(
        model=ThermalModel(dict.fromkeys(FEATURES, 0.0) | weights, STEP_SECONDS, 3.0),
        light_scale=light_scale,
        inputs={},
        trained_at="",
        history_days=0.0,
        windows=0,
        rate_error=0.0,
        holdout=[],
    )


# A house that closes a tenth of its gap to outdoors every hour and feels nothing else.
LEAKY = trained(envelope=0.1)


def readings(value: float, *, latest: float | None = None, latest_count: int = 2) -> np.ndarray:
    """Return a steady series, optionally ending in a few different readings."""
    series = np.full(HISTORY_POINTS, value)
    if latest is not None:
        series[-latest_count:] = latest
    return series


def advice_for(
    tmp_path,
    home: FakeHome,
    *,
    indoor: np.ndarray,
    outdoor: np.ndarray,
    drivers: dict[str, np.ndarray] | None = None,
    settings: dict[str, dict] | None = None,
    model: TrainedModel = LEAKY,
    previous: State | None = None,
    now: float = NOW,
) -> Advice:
    """Ask for advice about a thermostat the tool may steer.

    ``drivers`` holds the series of any drivers besides the outdoor
    temperature, and ``settings`` holds sections of the configuration.
    """
    drivers = drivers or {}
    config = write_config(tmp_path, history={name: name for name in drivers}, **(settings or {}))
    metrics = FakeMetrics({"indoor": indoor, "outdoor": outdoor, **drivers}, end=now)
    thermostat = read_thermostat(config, home)
    assert isinstance(thermostat, Thermostat)
    return advise(config, model, metrics, home, thermostat, previous, now=now)


def seen(
    target: float,
    *,
    changed_minutes_ago: float | None = None,
    started_from: float = 21.0,
    chose: str | None = None,
    chosen_minutes_ago: float = 0.0,
) -> State:
    """Return what a run five minutes ago would have recorded."""
    return State(
        target,
        seen_at=NOW - 300,
        change=None
        if changed_minutes_ago is None
        else Change(NOW - 60 * changed_minutes_ago, started_from),
        choice=None if chose is None else Choice(chose, NOW - 60 * chosen_minutes_ago),
    )
