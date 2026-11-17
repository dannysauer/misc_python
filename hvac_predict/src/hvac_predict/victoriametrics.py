"""Read time series from VictoriaMetrics onto a time grid."""

import math

import httpx
import numpy as np

from .errors import DataError, ServiceError
from .grid import TimeGrid
from .http import JsonApi

# VictoriaMetrics refuses a query that yields more than 30,000 points per series
# (-search.maxPointsPerTimeseries). Longer ranges are fetched in pieces well
# under that, so the tool also works where the limit has been lowered.
_MAX_POINTS_PER_REQUEST = 10_000
_TIMEOUT_SECONDS = 60.0


class VictoriaMetrics:
    """The one call this tool makes to the Prometheus querying API."""

    def __init__(self, api: JsonApi) -> None:
        self._api = api

    @classmethod
    def connect(
        cls, url: str, token: str | None = None, *, transport: httpx.BaseTransport | None = None
    ) -> "VictoriaMetrics":
        api = JsonApi("VictoriaMetrics", url, token, timeout=_TIMEOUT_SECONDS, transport=transport)
        return cls(api)

    def query_range(self, query: str, grid: TimeGrid) -> np.ndarray:
        """Evaluate ``query`` at every grid point; a point without a finite sample is NaN."""
        values = np.full(grid.size, np.nan)
        for first in range(0, grid.size, _MAX_POINTS_PER_REQUEST):
            last = min(first + _MAX_POINTS_PER_REQUEST, grid.size) - 1
            start, end = grid.start + first * grid.step, grid.start + last * grid.step
            for timestamp, value in self._samples(query, start, end, grid.step):
                index = round((timestamp - grid.start) / grid.step)
                if 0 <= index < grid.size and math.isfinite(value):
                    values[index] = value
        return values

    def _samples(self, query: str, start: int, end: int, step: int) -> list[tuple[float, float]]:
        body = self._api.get(
            "/api/v1/query_range",
            {"query": query, "start": start, "end": end, "step": f"{step}s"},
        )
        try:
            if body["status"] != "success":
                raise ServiceError(
                    f"VictoriaMetrics rejected the query {query!r}: {body.get('error')!r}"
                )
            series = body["data"]["result"]
            if len(series) > 1:
                raise DataError(
                    f"the query {query!r} returned {len(series)} series; narrow its label "
                    "matchers or wrap it in max(...) so that it returns one"
                )
            return (
                [(float(time), float(value)) for time, value in series[0]["values"]]
                if series
                else []
            )
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise ServiceError(
                f"VictoriaMetrics answered the query {query!r} with a result this tool cannot read"
            ) from error
