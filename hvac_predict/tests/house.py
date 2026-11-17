"""A simulated house with known physics, written without the package's model code."""

from dataclasses import dataclass

import numpy as np

from hvac_predict.thermal import Drivers

STEP_SECONDS = 300
END = 1_780_000_200.0  # on a five-minute boundary, half past the hour
HEAT_BELOW, COOL_ABOVE = 19.5, 25.5
# Degrees per hour for each unit of the driver. A house simulated without walls
# follows these exactly, which is the form of model the package fits.
TRUE_WEIGHTS = {
    "bias": 0.05,
    "envelope": 0.06,
    "wind": 0.002,
    "solar": 0.0015,
    "rain": -0.1,
    "heating": 3.0,
    "cooling": -3.0,
}
# With walls, the air also trades heat with a mass ten times heavier than itself
# that it cannot see, which lags behind the weather by about fourteen hours.
_WALLS_TO_AIR, _AIR_TO_WALLS, _OUTDOOR_TO_WALLS, _SOLAR_TO_WALLS = 0.5, 0.05, 0.02, 0.0003
_SUBSTEPS = 5


@dataclass(frozen=True)
class House:
    times: np.ndarray
    indoor: np.ndarray
    drivers: Drivers


def step(
    air: float,
    walls: float,
    *,
    outdoor: float,
    solar: float,
    wind: float,
    rain: float,
    heating: bool,
    cooling: bool,
    has_walls: bool,
) -> tuple[float, float]:
    """Advance the air and wall temperatures by one five-minute step."""
    weights = TRUE_WEIGHTS
    hours = STEP_SECONDS / 3600 / _SUBSTEPS
    for _ in range(_SUBSTEPS):
        gap = outdoor - air
        rate = (
            weights["bias"]
            + weights["envelope"] * gap
            + weights["wind"] * wind * gap
            + weights["solar"] * solar
            + weights["rain"] * rain
            + weights["heating"] * heating
            + weights["cooling"] * cooling
            + _WALLS_TO_AIR * has_walls * (walls - air)
        )
        walls += hours * (
            _AIR_TO_WALLS * (air - walls)
            + _OUTDOOR_TO_WALLS * (outdoor - walls)
            + _SOLAR_TO_WALLS * solar
        )
        air += hours * rate
    return air, walls


def simulate_house(
    days: float, *, end: float = END, seed: int = 0, walls: bool = False, idle_hours: float = 0.0
) -> House:
    """Simulate a thermostat-controlled house on a five-minute grid ending at ``end``.

    For the last ``idle_hours`` the thermostat is off and the house is left alone.
    """
    random = np.random.default_rng(seed)
    size = int(days * 86400 / STEP_SECONDS) + 1
    times = end - STEP_SECONDS * np.arange(size)[::-1]
    day = times / 86400

    outdoor = 14 + 7 * np.sin(2 * np.pi * (day - 0.4)) + 5 * np.sin(2 * np.pi * day / 6)
    cloud = np.repeat(random.uniform(0.2, 1.0, int(days) + 2), 86400 // STEP_SECONDS)[:size]
    solar = 800 * np.clip(np.sin(2 * np.pi * (day - 0.25)), 0, None) * cloud
    wind = np.abs(np.convolve(random.normal(0, 25, size), np.ones(36) / 36, mode="same"))
    rain = np.where(random.random(size) < 0.03, 2.0, 0.0)

    indoor = np.empty(size)
    heating, cooling = np.zeros(size), np.zeros(size)
    temperature, wall, heat_on, cool_on = 21.0, 20.0, False, False
    idle_from = size - 1 - round(idle_hours * 3600 / STEP_SECONDS)
    for index in range(size):
        indoor[index] = temperature
        heat_on = temperature < HEAT_BELOW or (heat_on and temperature < HEAT_BELOW + 1)
        cool_on = temperature > COOL_ABOVE or (cool_on and temperature > COOL_ABOVE - 1)
        if idle_hours and index >= idle_from:
            heat_on = cool_on = False
        heating[index], cooling[index] = heat_on, cool_on
        temperature, wall = step(
            temperature,
            wall,
            outdoor=outdoor[index],
            solar=solar[index],
            wind=wind[index],
            rain=rain[index],
            heating=heat_on,
            cooling=cool_on,
            has_walls=walls,
        )
    return House(
        times=times,
        indoor=indoor,
        drivers=Drivers(
            outdoor=outdoor, solar=solar, wind=wind, rain=rain, heating=heating, cooling=cooling
        ),
    )
