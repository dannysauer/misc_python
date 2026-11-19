"""Load the indoor temperature and its drivers from VictoriaMetrics."""

from dataclasses import dataclass

import numpy as np

from .config import Config
from .grid import TimeGrid
from .limits import NOT_NEGATIVE, SHARE, Limits, Range, believable
from .solar import clear_sky_irradiance
from .thermal import Drivers
from .victoriametrics import VictoriaMetrics


@dataclass(frozen=True)
class History:
    grid: TimeGrid
    indoor: np.ndarray
    drivers: Drivers


def load_history(metrics: VictoriaMetrics, config: Config, grid: TimeGrid) -> History:
    """Fetch every configured series on ``grid``, with unbelievable readings left as gaps.

    A driver without a query is taken to be absent, which is zero, except for
    sunlight, which falls back to what a cloudless sky would give.
    """
    queries, place, limits = config.history, config.location, Limits.in_units(config.units)

    def series(query: str | None, within: Range) -> np.ndarray:
        if not query:
            return np.zeros(grid.size)
        return believable(metrics.query_range(query, grid), within)

    solar = (
        series(queries.solar, NOT_NEGATIVE)
        if queries.solar
        else clear_sky_irradiance(grid.times, place.latitude, place.longitude)
    )
    return History(
        grid=grid,
        indoor=series(queries.indoor_temp, limits.indoor),
        drivers=Drivers(
            outdoor=series(queries.outdoor_temp, limits.outdoor),
            solar=solar,
            wind=series(queries.wind, limits.wind),
            rain=series(queries.rain, limits.rain),
            heating=series(queries.heating, SHARE),
            cooling=series(queries.cooling, SHARE),
        ),
    )
