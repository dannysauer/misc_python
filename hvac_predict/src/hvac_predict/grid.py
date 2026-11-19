"""The evenly spaced time grid that every series in a run is sampled on."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class TimeGrid:
    """Evenly spaced Unix times."""

    start: int
    step: int
    size: int

    @classmethod
    def covering(cls, end: float, seconds: float, step: int) -> "TimeGrid":
        """Build the grid whose last point is the latest step boundary at or before ``end``."""
        last = int(end // step) * step
        size = int(seconds // step) + 1
        return cls(start=last - (size - 1) * step, step=step, size=size)

    @property
    def end(self) -> int:
        return self.start + (self.size - 1) * self.step

    @property
    def times(self) -> np.ndarray:
        return self.start + self.step * np.arange(self.size)
