import json

import httpx
import pytest

from hvac_predict.errors import ServiceError
from hvac_predict.homeassistant import HomeAssistant

STATE = {
    "entity_id": "climate.house",
    "state": "heat",
    "attributes": {"hvac_modes": ["heat", "cool", "off"]},
}


def serving(handler) -> HomeAssistant:
    return HomeAssistant.connect("http://ha.test", "t0ken", transport=httpx.MockTransport(handler))


def answering(body) -> HomeAssistant:
    return serving(lambda request: httpx.Response(200, json=body))


def test_state_reads_an_entity():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/states/climate.house"
        assert request.headers["Authorization"] == "Bearer t0ken"
        return httpx.Response(200, json=STATE)

    state = serving(handler).state("climate.house")

    assert state.entity_id == "climate.house"
    assert state.state == "heat"
    assert state.attributes == {"hvac_modes": ["heat", "cool", "off"]}


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"state": "heat"},
        {"attributes": {}},
        STATE | {"state": 7},
        STATE | {"state": "he\x1b[31mat"},
        STATE | {"attributes": None},
    ],
)
def test_a_state_that_makes_no_sense_is_reported_and_not_a_crash(body):
    with pytest.raises(ServiceError, match=r"a state for climate.house that this tool cannot read"):
        answering(body).state("climate.house")


def test_temperature_unit_comes_from_the_configuration():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/config"
        return httpx.Response(200, json={"unit_system": {"temperature": "°F"}})

    assert serving(handler).temperature_unit() == "°F"


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"unit_system": {"temperature": 5}},
        {"unit_system": {"temperature": "°" * 9}},
        {"unit_system": {"temperature": "\x1b[2J"}},
    ],
)
def test_a_configuration_without_a_temperature_unit_is_reported(body):
    with pytest.raises(ServiceError, match="a configuration that this tool cannot read"):
        answering(body).temperature_unit()


def test_hourly_forecast_asks_the_weather_service_for_a_response():
    forecast = [{"datetime": "2026-05-28T21:00:00+00:00", "temperature": 12.5}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/services/weather/get_forecasts"
        # Home Assistant only returns the forecast when this parameter is present.
        assert "return_response" in request.url.params
        assert json.loads(request.content) == {"entity_id": "weather.home", "type": "hourly"}
        body = {"changed_states": [], "service_response": {"weather.home": {"forecast": forecast}}}
        return httpx.Response(200, json=body)

    assert serving(handler).hourly_forecast("weather.home") == forecast


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"changed_states": [], "service_response": {}},
        {"service_response": {"weather.home": {}}},
        {"service_response": None},
    ],
)
def test_hourly_forecast_reports_a_response_without_a_forecast(body):
    with pytest.raises(ServiceError, match=r"no hourly forecast for weather\.home"):
        answering(body).hourly_forecast("weather.home")


@pytest.mark.parametrize("forecast", [{"datetime": "x"}, ["sunny"], "sunny"])
def test_hourly_forecast_reports_a_forecast_that_is_not_a_list_of_entries(forecast):
    body = {"service_response": {"weather.home": {"forecast": forecast}}}

    with pytest.raises(ServiceError, match=r"an hourly forecast for weather.home that this tool"):
        answering(body).hourly_forecast("weather.home")


def test_set_temperature_range_sends_both_setpoints_to_the_climate_service():
    # HVP-001: the call that moves the setpoints says nothing about the mode.
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=[])

    serving(handler).set_temperature_range("climate.house", 64.0, 67.0)

    assert calls == [
        (
            "/api/services/climate/set_temperature",
            {"entity_id": "climate.house", "target_temp_low": 64.0, "target_temp_high": 67.0},
        )
    ]


def test_a_rejected_token_is_reported():
    with pytest.raises(ServiceError, match="401 Unauthorized"):
        serving(lambda request: httpx.Response(401)).state("climate.house")
