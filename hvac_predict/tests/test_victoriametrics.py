import httpx
import numpy as np
import pytest

from hvac_predict.errors import DataError, ServiceError
from hvac_predict.grid import TimeGrid
from hvac_predict.victoriametrics import VictoriaMetrics


def serving(handler) -> VictoriaMetrics:
    return VictoriaMetrics.connect("http://vm.test", transport=httpx.MockTransport(handler))


def matrix(*series: list) -> httpx.Response:
    result = [{"metric": {}, "values": values} for values in series]
    return httpx.Response(200, json={"status": "success", "data": {"result": result}})


def test_grid_ends_on_the_latest_step_boundary():
    grid = TimeGrid.covering(end=1000.0, seconds=600.0, step=300)

    assert grid.times.tolist() == [300, 600, 900]
    assert grid.end == 900


def test_query_range_asks_for_the_whole_grid():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return matrix([])

    serving(handler).query_range("indoor", TimeGrid(start=300, step=300, size=3))

    (request,) = requests
    assert request.url.path == "/api/v1/query_range"
    assert dict(request.url.params) == {
        "query": "indoor",
        "start": "300",
        "end": "900",
        "step": "300s",
    }


def test_connect_sends_the_token_when_there_is_one():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization"))
        return matrix()

    transport = httpx.MockTransport(handler)
    grid = TimeGrid(0, 300, 3)
    VictoriaMetrics.connect("http://vm.test", "s3cret", transport=transport).query_range("x", grid)
    VictoriaMetrics.connect("http://vm.test", transport=transport).query_range("x", grid)

    assert seen == ["Bearer s3cret", None]


def test_query_range_places_samples_on_the_grid():
    def handler(request: httpx.Request) -> httpx.Response:
        return matrix([[300, "20.5"], [900, "21"], [1200, "NaN"], [1500, "+Inf"], [9000, "5"]])

    values = serving(handler).query_range("indoor", TimeGrid(start=300, step=300, size=5))

    assert values == pytest.approx([20.5, np.nan, 21.0, np.nan, np.nan], nan_ok=True)


def test_a_sample_slightly_off_the_grid_goes_to_the_nearest_point():
    def handler(request: httpx.Request) -> httpx.Response:
        return matrix([[299, "1"], [599, "2"], [1040, "3"]])

    values = serving(handler).query_range("indoor", TimeGrid(start=300, step=300, size=4))

    assert values == pytest.approx([1.0, 2.0, 3.0, np.nan], nan_ok=True)


def test_query_range_is_all_gaps_when_the_query_matches_nothing():
    values = serving(lambda request: matrix()).query_range("indoor", TimeGrid(0, 300, 3))

    assert np.isnan(values).all()


def test_query_range_fetches_a_long_range_in_pieces():
    spans = []

    def handler(request: httpx.Request) -> httpx.Response:
        start, end = int(request.url.params["start"]), int(request.url.params["end"])
        spans.append((start, end))
        return matrix([[time, str(time)] for time in range(start, end + 60, 60)])

    values = serving(handler).query_range("indoor", TimeGrid(start=0, step=60, size=25_000))

    assert spans == [(0, 599_940), (600_000, 1_199_940), (1_200_000, 1_499_940)]
    assert values.tolist() == list(range(0, 1_500_000, 60))


def test_query_range_refuses_a_query_that_returns_several_series():
    with pytest.raises(DataError, match="returned 2 series"):
        serving(lambda request: matrix([], [])).query_range("indoor", TimeGrid(0, 300, 3))


def test_query_range_passes_on_why_a_query_was_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"status": "error", "error": "unexpected token"})

    with pytest.raises(ServiceError, match="unexpected token"):
        serving(handler).query_range("indoor{", TimeGrid(0, 300, 3))


@pytest.mark.parametrize(
    "body",
    [
        [],
        "text",
        {"status": "error", "error": "\x1b[31mbad"},
        {"status": "success"},
        {"status": "success", "data": {"result": [{"metric": {}}]}},
        {"status": "success", "data": {"result": [{"values": [[1, "one"]]}]}},
        {"status": "success", "data": {"result": [{"values": [[1]]}]}},
        {"status": "success", "data": {"result": 7}},
    ],
)
def test_a_result_that_makes_no_sense_is_reported_and_not_a_crash(body):
    with pytest.raises(ServiceError) as failed:
        serving(lambda request: httpx.Response(200, json=body)).query_range(
            "indoor", TimeGrid(0, 300, 3)
        )

    assert "\x1b" not in str(failed.value)
