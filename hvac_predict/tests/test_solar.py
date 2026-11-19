from datetime import UTC, datetime

import numpy as np
import pytest

from hvac_predict.solar import clear_sky_irradiance, cloud_attenuation, sun_elevation


def at(*moment: int) -> np.ndarray:
    return np.array([datetime(*moment, tzinfo=UTC).timestamp()])


@pytest.mark.parametrize(
    ("moment", "latitude", "longitude", "expected"),
    [
        # At solar noon the sun stands at 90 - latitude + declination.
        pytest.param((2026, 6, 21, 12, 2), 51.48, 0.0, 61.96, id="greenwich-midsummer-noon"),
        pytest.param((2026, 12, 21, 11, 58), 51.48, 0.0, 15.08, id="greenwich-midwinter-noon"),
        pytest.param((2026, 3, 20, 12, 7), 0.0, 0.0, 89.9, id="equator-equinox-noon"),
        pytest.param((2026, 6, 21, 17, 52), 41.88, -87.63, 71.56, id="chicago-midsummer-noon"),
        pytest.param((2026, 6, 21, 0, 2), 51.48, 0.0, -15.08, id="greenwich-midsummer-midnight"),
    ],
)
def test_sun_elevation(moment, latitude, longitude, expected):
    assert sun_elevation(at(*moment), latitude, longitude) == pytest.approx(expected, abs=0.3)


def test_clear_sky_irradiance_is_zero_at_night_and_strong_at_noon():
    midnight, noon = at(2026, 6, 21, 0, 2), at(2026, 6, 21, 12, 2)

    assert clear_sky_irradiance(midnight, 51.48, 0.0) == 0
    assert clear_sky_irradiance(noon, 51.48, 0.0) == pytest.approx(907, abs=5)


def test_cloud_attenuation_runs_from_clear_to_overcast():
    assert cloud_attenuation(np.array([0.0, 0.5, 1.0, 1.7])) == pytest.approx(
        [1.0, 0.929, 0.25, 0.25], abs=0.001
    )
