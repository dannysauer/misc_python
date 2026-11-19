"""A linear thermal model of the house: fit it to history, then run it forward.

The indoor temperature changes at a rate that is a weighted sum of what acts on
the house, in degrees per hour:

    rate = bias
         + envelope     * (outdoor - indoor)
         + envelope_lag * (smoothed outdoor - indoor)
         + mass         * (smoothed indoor - indoor)
         + wind         * wind speed * (outdoor - indoor)
         + solar        * sunlight
         + solar_lag    * smoothed sunlight
         + rain         * rain rate
         + heating      * heating on
         + cooling      * cooling on

Walls and furniture store heat and give it back for hours. Their temperature is
not measured, but it follows a smoothed mix of the indoor temperature, the
outdoor temperature and the sunlight, so the three smoothed terms together
stand in for it. All three are smoothed over the same lag time.

Time is a grid of equal steps, and the model is fitted and run on that grid. The
conditions at a grid point set the rate for the whole step that begins there.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, fields

import numpy as np
from scipy.optimize import lsq_linear

from .errors import DataError

# Physics fixes the sign of every weight except the bias. Rain and the air
# conditioner can only cool the house; sunlight and the furnace can only warm
# it; the rest can only pull the indoor temperature towards what it is compared
# with. Holding the fit to those signs keeps a noisy or collinear history from
# producing a model that runs away.
_FREE, _WARMING, _COOLING = (-np.inf, np.inf), (0.0, np.inf), (-np.inf, 0.0)
_SIGNS = {
    "bias": _FREE,
    "envelope": _WARMING,
    "envelope_lag": _WARMING,
    "mass": _WARMING,
    "wind": _WARMING,
    "solar": _WARMING,
    "solar_lag": _WARMING,
    "rain": _COOLING,
    "heating": _WARMING,
    "cooling": _COOLING,
}
FEATURES = tuple(_SIGNS)
_MIN_FIT_WINDOWS = 200
_CONSTANT_TOLERANCE = 1e-9
# A weight the fit pushed against its sign limit comes back as a stray 1e-20, not zero.
_NEGLIGIBLE_WEIGHT = 1e-12


@dataclass(frozen=True)
class Drivers:
    """Everything that moves the indoor temperature, sampled on one time grid."""

    outdoor: np.ndarray
    solar: np.ndarray
    wind: np.ndarray
    rain: np.ndarray
    heating: np.ndarray
    cooling: np.ndarray

    def __len__(self) -> int:
        return len(self.outdoor)

    def series(self) -> dict[str, np.ndarray]:
        """Return every driver's series under its name."""
        return {spec.name: getattr(self, spec.name) for spec in fields(self)}

    def followed_by(self, later: "Drivers") -> "Drivers":
        """Return these drivers with ``later`` appended on the time axis."""
        return self._each(lambda name, values: np.concatenate([values, getattr(later, name)]))

    def gaps_filled(self) -> "Drivers":
        """Return these drivers with every gap filled by the last reading before it."""
        return self._each(lambda name, values: _carried_forward(values))

    def _each(self, change: Callable[[str, np.ndarray], np.ndarray]) -> "Drivers":
        return Drivers(**{name: change(name, values) for name, values in self.series().items()})


@dataclass(frozen=True)
class ThermalModel:
    coefficients: dict[str, float]
    step_seconds: int
    lag_hours: float

    @property
    def step_hours(self) -> float:
        return self.step_seconds / 3600

    @property
    def weights(self) -> np.ndarray:
        return np.array([self.coefficients[name] for name in FEATURES])

    @property
    def follows_outdoors(self) -> bool:
        """Whether the fit found any link between outdoor and indoor temperature."""
        return self.coefficients["envelope"] + self.coefficients["envelope_lag"] > 0


@dataclass(frozen=True)
class Fit:
    model: ThermalModel
    windows: int
    rate_error: float


@dataclass(frozen=True)
class HorizonError:
    """Mean absolute error of predictions made ``hours`` ahead."""

    hours: float
    model_error: float
    no_change_error: float
    runs: int


def fit(
    indoor: np.ndarray,
    drivers: Drivers,
    *,
    step_seconds: int,
    lag_hours: float,
    window_steps: int,
    until: int | None = None,
) -> Fit:
    """Fit the model to history, optionally using only the first ``until`` grid points.

    Each sample is one window of ``window_steps`` steps: the change in indoor
    temperature across it against the mean of each driver within it. Windows
    overlap, and a window with any missing reading is left out.
    """
    newest_weight = _newest_weight(step_seconds, lag_hours)
    rows = _design(indoor, _smooth(indoor, newest_weight), drivers, newest_weight)
    complete = np.isfinite(rows).all(axis=1)
    kept = np.where(complete[:, None], rows, 0.0)
    totals = np.vstack([np.zeros(len(FEATURES)), np.cumsum(kept, axis=0)])
    counts = np.concatenate([[0], np.cumsum(complete)])

    starts = np.arange(max((len(indoor) if until is None else until) - window_steps, 0))
    ends = starts + window_steps
    usable = (counts[ends] - counts[starts] == window_steps) & np.isfinite(indoor[ends])
    if usable.sum() < _MIN_FIT_WINDOWS:
        raise DataError(
            f"only {usable.sum()} gap-free windows of history to fit, need {_MIN_FIT_WINDOWS}; "
            "check that the history queries return data, or train on more days"
        )
    starts, ends = starts[usable], ends[usable]
    means = (totals[ends] - totals[starts]) / window_steps
    rates = (indoor[ends] - indoor[starts]) / (window_steps * step_seconds / 3600)

    # A driver that never varies (a sensor that is not configured, heating in
    # summer) cannot be told apart from the bias, so its weight stays at zero.
    # Wind is judged on its own readings: its column is the wind times the gap
    # to outdoors, which varies even when the wind does not.
    varying = _varies(means)
    varying[FEATURES.index("wind")] = _varies(drivers.wind[np.isfinite(drivers.wind)])
    active = np.union1d([FEATURES.index("bias")], np.flatnonzero(varying))
    scale = np.sqrt((means[:, active] ** 2).mean(axis=0))
    lower, upper = (np.array(bounds)[active] for bounds in zip(*_SIGNS.values(), strict=True))
    solution = lsq_linear(means[:, active] / scale, rates, bounds=(lower, upper))
    if not solution.success:
        raise DataError(f"the fit did not converge: {solution.message}")
    weights = np.zeros(len(FEATURES))
    weights[active] = np.where(np.abs(solution.x) < _NEGLIGIBLE_WEIGHT, 0.0, solution.x) / scale

    model = ThermalModel(
        dict(zip(FEATURES, weights.tolist(), strict=True)), step_seconds, lag_hours
    )
    rate_error = float(np.sqrt(np.mean((means @ weights - rates) ** 2)))
    return Fit(model=model, windows=len(rates), rate_error=rate_error)


def predict(model: ThermalModel, indoor: np.ndarray, drivers: Drivers) -> np.ndarray:
    """Continue ``indoor`` for as many grid points as ``drivers`` reaches beyond it.

    ``drivers`` covers the grid points ``indoor`` covers and then the future.
    The run starts at the newest indoor reading and ends at the last grid point
    of ``drivers``, so element 0 of the result is that reading and the last
    element is the end of the future. A driver with no reading at a grid point
    is taken not to have changed since its last one.
    """
    start = np.flatnonzero(np.isfinite(indoor))[-1:]
    dynamics = _Dynamics.of(model, indoor, drivers.gaps_filled())
    return dynamics.run(start, len(drivers) - 1 - int(start[0]))[0]


def backtest(
    model: ThermalModel,
    indoor: np.ndarray,
    drivers: Drivers,
    *,
    horizons_hours: tuple[float, ...],
    first: int,
    stop: int | None = None,
) -> list[HorizonError]:
    """Replay the history between grid points ``first`` and ``stop`` and measure the drift.

    A run starts every hour from the measured indoor temperature and is driven by
    the measured weather and equipment state. "No change" is the error from
    assuming the temperature simply stays where it started. Runs that would
    cross a missing reading are left out; with none left, the result is empty.
    """
    dynamics = _Dynamics.of(model, indoor, drivers)
    steps = [round(hours / model.step_hours) for hours in horizons_hours]
    stride = max(round(1 / model.step_hours), 1)
    starts = np.arange(first, (len(indoor) if stop is None else stop) - max(steps), stride)
    known = np.isfinite(dynamics.base) & np.isfinite(dynamics.pull) & np.isfinite(indoor)
    gaps_before = np.concatenate([[0], np.cumsum(~known)])
    starts = starts[gaps_before[starts + max(steps) + 1] == gaps_before[starts]]
    if not starts.size:
        return []

    runs = dynamics.run(starts, max(steps))
    actual = indoor[starts[:, None] + steps]
    model_errors = np.abs(runs[:, steps] - actual).mean(axis=0)
    no_change_errors = np.abs(indoor[starts, None] - actual).mean(axis=0)
    return [
        HorizonError(hours, float(model_error), float(no_change_error), runs=len(starts))
        for hours, model_error, no_change_error in zip(
            horizons_hours, model_errors, no_change_errors, strict=True
        )
    ]


@dataclass(frozen=True)
class _Dynamics:
    """The model as ``rate = base + pull * indoor + mass * stored`` at each grid point.

    ``stored`` is the smoothed indoor temperature. ``gain`` is the length of a
    step in hours, as the fit assumed, cut short where a full step would carry
    the temperature past the point it is heading for. That keeps a run stable
    however quickly the house responds.
    """

    base: np.ndarray
    pull: np.ndarray
    mass: np.ndarray
    gain: np.ndarray
    newest_weight: float
    # The measured history that runs start from, and its smoothed counterpart.
    indoor: np.ndarray
    stored: np.ndarray

    @classmethod
    def of(cls, model: ThermalModel, indoor: np.ndarray, drivers: Drivers) -> "_Dynamics":
        """Build the dynamics over the grid of ``drivers``, which may run on past ``indoor``."""
        newest_weight = _newest_weight(model.step_seconds, model.lag_hours)
        zeros, ones = np.zeros(len(drivers)), np.ones(len(drivers))

        def rate(probe: np.ndarray, stored: np.ndarray) -> np.ndarray:
            return _design(probe, stored, drivers, newest_weight) @ model.weights

        # The rate is linear in both temperatures, so three probes recover its parts.
        base = rate(zeros, zeros)
        pull, mass = rate(ones, zeros) - base, rate(zeros, ones) - base
        overshoots = pull * model.step_hours < -1
        gain = np.where(overshoots, -1 / np.where(overshoots, pull, 1.0), model.step_hours)
        return cls(base, pull, mass, gain, newest_weight, indoor, _smooth(indoor, newest_weight))

    def run(self, starts: np.ndarray, steps: int) -> np.ndarray:
        """Run forward ``steps`` grid points from each of ``starts``, one row per start.

        Column 0 holds the starting temperatures, which are the measured ones.
        """
        indoor, stored = self.indoor[starts], self.stored[starts]
        temperatures = np.empty((len(starts), steps + 1))
        temperatures[:, 0] = indoor
        for step in range(steps):
            at = starts + step
            rate = self.base[at] + self.pull[at] * indoor + self.mass[at] * stored
            indoor = indoor + self.gain[at] * rate
            stored = stored + self.newest_weight * (indoor - stored)
            temperatures[:, step + 1] = indoor
        return temperatures


def _design(
    indoor: np.ndarray, stored: np.ndarray, drivers: Drivers, newest_weight: float
) -> np.ndarray:
    """Return one row per grid point and one column per entry in FEATURES."""
    gap = drivers.outdoor - indoor
    columns = {
        "bias": np.ones(len(drivers)),
        "envelope": gap,
        "envelope_lag": _smooth(drivers.outdoor, newest_weight) - indoor,
        "mass": stored - indoor,
        "wind": drivers.wind * gap,
        "solar": drivers.solar,
        "solar_lag": _smooth(drivers.solar, newest_weight),
        "rain": drivers.rain,
        "heating": drivers.heating,
        "cooling": drivers.cooling,
    }
    return np.column_stack([columns[name] for name in FEATURES])


def _varies(values: np.ndarray) -> np.ndarray:
    """Return whether each column of ``values`` changes by more than rounding error."""
    if not len(values):
        return np.array(False)
    return np.ptp(values, axis=0) > _CONSTANT_TOLERANCE * np.abs(values).max(axis=0)


def _newest_weight(step_seconds: int, lag_hours: float) -> float:
    """Return the share of a smoothed value that comes from the newest sample."""
    return 1.0 - math.exp(-step_seconds / (lag_hours * 3600))


def _carried_forward(values: np.ndarray) -> np.ndarray:
    """Return a series with every gap filled by the last reading before it."""
    last_reading = np.where(np.isfinite(values), np.arange(len(values)), 0)
    return values[np.maximum.accumulate(last_reading)]


def _smooth(values: np.ndarray, newest_weight: float) -> np.ndarray:
    """Exponentially smooth a series, in which a gap counts as its last reading repeated."""
    smoothed = np.empty(len(values))
    state = math.nan
    for index, value in enumerate(_carried_forward(values).tolist()):
        if math.isfinite(value):
            state = value if math.isnan(state) else state + newest_weight * (value - state)
        smoothed[index] = state
    return smoothed
