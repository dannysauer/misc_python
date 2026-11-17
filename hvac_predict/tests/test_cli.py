import dataclasses
import json
import math

import httpx
import numpy as np
import pytest
from fakes import (
    ALL_DRIVERS,
    NOW,
    WEATHER_UNITS,
    FakeMetrics,
    holding,
    hourly_forecast,
    scheduled_preset,
    thermostat_attributes,
    write_settings,
)
from house import simulate_house

from hvac_predict.cli import main
from hvac_predict.grid import TimeGrid
from hvac_predict.homeassistant import HomeAssistant
from hvac_predict.victoriametrics import VictoriaMetrics


class Servers:
    """VictoriaMetrics and Home Assistant as seen over HTTP, backed by a simulated house."""

    def __init__(self) -> None:
        house = simulate_house(days=20, end=NOW, walls=True)
        self.series = {"indoor": house.indoor, **dataclasses.asdict(house.drivers)}
        self.metrics_answer: httpx.Response | None = None
        self.home_answer: httpx.Response | None = None
        self.metrics_tokens: set[str | None] = set()
        self.enable_switch = "on"
        self.thermostat_mode = "heat_cool"
        # What the thermostat reports, as thermostat_attributes takes it.
        self.reports = {
            "comfort": "Home",
            "preset": scheduled_preset("Home"),
            "setpoints": (19.0, 21.0),
            "reads": float(house.indoor[-1]),
        }
        self.setpoint_changes: list[dict] = []

    def metrics(self, request: httpx.Request) -> httpx.Response:
        self.metrics_tokens.add(request.headers.get("Authorization"))
        if self.metrics_answer is not None:
            return self.metrics_answer
        asked = request.url.params
        start, end, step = int(asked["start"]), int(asked["end"]), int(asked["step"][:-1])
        grid = TimeGrid(start=start, step=step, size=(end - start) // step + 1)
        values = FakeMetrics(self.series).query_range(asked["query"], grid)
        samples = [
            [int(time), str(value)]
            for time, value in zip(grid.times, values, strict=True)
            if math.isfinite(value)
        ]
        result = [{"metric": {}, "values": samples}]
        return httpx.Response(200, json={"status": "success", "data": {"result": result}})

    def home(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer home-token"
        if self.home_answer is not None:
            return self.home_answer
        path = request.url.path
        if path == "/api/states/climate.house":
            return self._state(self.thermostat_mode, thermostat_attributes(**self.reports))
        if path == "/api/states/weather.home":
            return self._state("cloudy", WEATHER_UNITS)
        if path == "/api/states/input_boolean.hvac_predict":
            return self._state(self.enable_switch, {})
        if path == "/api/config":
            return httpx.Response(200, json={"unit_system": {"temperature": "°C"}})
        if path == "/api/services/weather/get_forecasts":
            forecast = hourly_forecast(12, temperature=-10.0, wind_speed=5.0, condition="cloudy")
            response = {"weather.home": {"forecast": forecast}}
            return httpx.Response(200, json={"changed_states": [], "service_response": response})
        assert path == "/api/services/climate/set_temperature"
        change = json.loads(request.content)
        self.setpoint_changes.append(change)
        self.reports = holding(self.reports, change["target_temp_low"], change["target_temp_high"])
        return httpx.Response(200, json=[])

    @staticmethod
    def _state(state: str, attributes: dict) -> httpx.Response:
        return httpx.Response(200, json={"state": state, "attributes": attributes})


def reached_through(client: type, handler) -> classmethod:
    """Return the client's own ``connect``, wired to ``handler`` instead of the network."""
    connect = client.connect.__func__

    def connect_to_handler(cls, *args, **kwargs):
        return connect(cls, *args, transport=httpx.MockTransport(handler), **kwargs)

    return classmethod(connect_to_handler)


@pytest.fixture
def servers(monkeypatch) -> Servers:
    servers = Servers()
    monkeypatch.setattr(
        VictoriaMetrics, "connect", reached_through(VictoriaMetrics, servers.metrics)
    )
    monkeypatch.setattr(HomeAssistant, "connect", reached_through(HomeAssistant, servers.home))
    monkeypatch.setenv("HASS_TOKEN", "home-token")
    monkeypatch.delenv("VICTORIAMETRICS_TOKEN", raising=False)
    return servers


@pytest.fixture
def config_path(tmp_path) -> str:
    return str(write_settings(tmp_path, history=ALL_DRIVERS, training={"days": 20}))


def run(config_path: str, *command: str, now: float = NOW) -> int:
    return main(["--config", config_path, *command], clock=lambda: now)


@pytest.fixture
def trained(servers, config_path, capsys) -> str:
    """Return the configuration path once a model has been trained with it."""
    assert run(config_path, "train") == 0
    capsys.readouterr()
    return config_path


def test_train_reports_on_the_model_and_writes_it(servers, config_path, tmp_path, capsys):
    assert run(config_path, "train") == 0

    report = capsys.readouterr().out
    assert "windows from 20 days of history" in report
    assert "Walls and furniture hold on to heat for about" in report
    assert "Check on the newest 20% of the history, predicted by a fit to the rest:" in report
    assert "8 h ahead: off by" in report
    assert report.endswith(f"Model written to {tmp_path / 'model.json'}\n")
    assert (tmp_path / "model.json").exists()


def test_decide_sets_the_thermostat_when_told_to_apply(servers, trained, capsys):
    assert run(trained, "decide", "--apply") == 0
    first = capsys.readouterr().out
    assert run(trained, "decide", "--apply") == 0
    second = capsys.readouterr().out

    # A forecast of -10 degrees for the rest of the day leaves no doubt: heat to the target.
    assert "Comfort setting 'Home' aims for 21 °C." in first
    assert "The thermostat holds 19.0 to 21.0 °C." in first
    assert "Left alone, the house is predicted to be at:" in first
    assert "  +8 h" in first
    assert "Decision: heat preference, 21.0 to 23.0 °C. " in first
    assert "Set the thermostat to 21.0 to 23.0 °C." in first
    assert "The thermostat holds 21.0 to 23.0 °C." in second
    assert "The thermostat already holds those setpoints." in second
    # HVP-001: one change, to the setpoints alone.
    assert servers.setpoint_changes == [
        {"entity_id": "climate.house", "target_temp_low": 21.0, "target_temp_high": 23.0}
    ]


JSON_KEYS = {
    "preference",
    "setpoints",
    "rule",
    "reason",
    "current_setpoints",
    "comfort",
    "target",
    "applied",
    "left_alone",
    "outlook",
}


def test_decide_only_reports_unless_told_to_apply(servers, trained, capsys):
    # HVP-011
    assert run(trained, "decide") == 0

    report = capsys.readouterr().out
    assert "Decision: heat preference, 21.0 to 23.0 °C. " in report
    assert "Dry run: the thermostat was not changed. Pass --apply to set it." in report
    assert servers.setpoint_changes == []


def test_decide_can_print_json(servers, trained, capsys):
    assert run(trained, "decide", "--json") == 0

    result = json.loads(capsys.readouterr().out)
    assert set(result) == JSON_KEYS
    assert result["preference"] == "heat"
    assert result["setpoints"] == [21.0, 23.0]
    assert result["rule"] == "overshoot"
    assert result["current_setpoints"] == [19.0, 21.0]
    assert (result["comfort"], result["target"]) == ("Home", 21.0)
    assert result["applied"] is False
    assert result["left_alone"] is None
    assert result["reason"].startswith("Left alone, the house")
    assert [point["minutes"] for point in result["outlook"]] == [
        60.0 * hour for hour in range(1, 9)
    ]


def test_decide_reports_a_thermostat_that_holds_no_setpoints(servers, trained, capsys):
    servers.reports["setpoints"] = (None, None)

    assert run(trained, "decide") == 0
    report = capsys.readouterr().out
    assert run(trained, "decide", "--json") == 0

    assert "The thermostat holds no pair of setpoints." in report
    assert json.loads(capsys.readouterr().out)["current_setpoints"] is None


def test_decide_says_when_json_reports_a_change(servers, trained, capsys):
    assert run(trained, "decide", "--apply", "--json") == 0

    assert json.loads(capsys.readouterr().out)["applied"] is True
    assert len(servers.setpoint_changes) == 1


def test_each_run_records_what_it_saw_for_the_next(servers, trained, tmp_path, capsys):
    assert run(trained, "decide") == 0
    dry = json.loads((tmp_path / "state.json").read_text())
    assert run(trained, "decide", "--apply", now=NOW + 300) == 0
    applied = json.loads((tmp_path / "state.json").read_text())

    assert dry == {"target": 21.0, "seen_at": NOW, "change": None, "choice": None, "sent": []}
    assert applied == dry | {
        "seen_at": NOW + 300,
        "choice": {"preference": "heat", "at": NOW + 300},
        "sent": [[21.0, 23.0]],
    }


def test_a_change_of_comfort_setting_gives_the_house_time_to_get_there(servers, trained, capsys):
    # HVP-003. The schedule moves from Home (21) to Sleep (18) between two runs.
    assert run(trained, "decide", "--apply") == 0
    servers.reports |= {"comfort": "Sleep", "preset": scheduled_preset("Sleep")}
    capsys.readouterr()

    assert run(trained, "decide", "--apply", "--json", now=NOW + 600) == 0

    # This heavy-walled house cannot shed three degrees in an hour and a half, even at -10 outside.
    result = json.loads(capsys.readouterr().out)
    assert (result["comfort"], result["target"]) == ("Sleep", 18.0)
    assert (result["preference"], result["rule"]) == ("cool", "drive")
    assert result["reason"] == (
        "The target is newly 18, and left alone the house would not cool to it "
        "in the 1 h 30 min it has left."
    )
    assert servers.setpoint_changes[-1] == {
        "entity_id": "climate.house",
        "target_temp_low": 16.0,
        "target_temp_high": 18.0,
    }


@pytest.mark.parametrize("mode", ["heat", "unavailable"])
def test_decide_leaves_a_thermostat_that_is_not_in_auto_mode_alone(
    servers, trained, tmp_path, capsys, mode
):
    servers.thermostat_mode = mode
    servers.metrics_answer = httpx.Response(503)

    assert run(trained, "decide", "--apply") == 0
    report = capsys.readouterr().out
    assert run(trained, "decide", "--apply", "--json") == 0
    result = json.loads(capsys.readouterr().out)

    # HVP-001. VictoriaMetrics is down, and it does not matter: nothing is read for a
    # thermostat left alone.
    assert report.startswith(f"Not steering the thermostat: The thermostat is in '{mode}' mode.")
    assert set(result) == JSON_KEYS
    assert result["left_alone"].startswith("The thermostat is in")
    assert result["applied"] is False
    assert result["outlook"] == []
    assert all(result[key] is None for key in JSON_KEYS - {"left_alone", "applied", "outlook"})
    assert servers.setpoint_changes == []
    assert not (tmp_path / "state.json").exists()


@pytest.mark.parametrize("preset", ["away_indefinitely", "vacation"])
def test_decide_leaves_a_hold_someone_else_placed_alone(servers, trained, tmp_path, capsys, preset):
    # HVP-007. A vacation keeps the setpoints the owner gave it, whatever the forecast says.
    servers.reports |= {"preset": preset, "setpoints": (16.5, 24.5)}

    assert run(trained, "decide", "--apply") == 0

    assert f"holding '{preset}' in place of its scheduled 'Home'" in capsys.readouterr().out
    assert servers.setpoint_changes == []
    assert servers.reports["setpoints"] == (16.5, 24.5)
    assert not (tmp_path / "state.json").exists()


def test_decide_leaves_a_hold_it_did_not_place_alone(servers, trained, capsys):
    # HVP-024. The tool's own hold is still reported, but the setpoints are no longer its own:
    # a vacation the thermostat lists behind that hold, or a change made at the thermostat.
    assert run(trained, "decide", "--apply") == 0
    servers.reports["setpoints"] = (16.5, 24.5)
    capsys.readouterr()

    assert run(trained, "decide", "--apply", now=NOW + 600) == 0

    assert capsys.readouterr().out == (
        "Not steering the thermostat: The thermostat is holding 16.5 to 24.5 °C that this "
        "tool has no record of placing. The tool leaves a hold placed by someone else alone "
        "until that hold ends.\n"
    )
    assert len(servers.setpoint_changes) == 1
    assert servers.reports["setpoints"] == (16.5, 24.5)


def test_decide_leaves_the_thermostat_alone_while_the_enable_switch_is_off(
    servers, tmp_path, capsys
):
    # HVP-019
    path = str(
        write_settings(
            tmp_path,
            history=ALL_DRIVERS,
            training={"days": 20},
            homeassistant={"enable_entity": "input_boolean.hvac_predict"},
        )
    )
    servers.enable_switch = "off"

    assert run(path, "decide", "--apply") == 0

    assert "Not steering the thermostat: input_boolean.hvac_predict is not on." in (
        capsys.readouterr().out
    )
    assert servers.setpoint_changes == []


def test_decide_refuses_when_the_thermostat_disagrees_with_the_history(servers, trained, capsys):
    # HVP-010
    servers.reports["reads"] = 12.0

    assert run(trained, "decide", "--apply") == 1

    assert "not acting on readings that disagree" in capsys.readouterr().err
    assert servers.setpoint_changes == []


def test_decide_stops_before_touching_the_thermostat_when_it_cannot_keep_its_state(
    servers, trained, tmp_path, capsys
):
    # HVP-020
    (tmp_path / "state.json").mkdir()

    assert run(trained, "decide", "--apply") == 1

    error = capsys.readouterr().err
    assert error.startswith(f"hvac-predict: cannot write {tmp_path / 'state.json'}: ")
    assert error.count("\n") == 1
    assert servers.setpoint_changes == []


def test_a_home_assistant_failure_is_one_line_and_not_a_traceback(servers, trained, capsys):
    # HVP-014
    servers.home_answer = httpx.Response(500)

    assert run(trained, "decide", "--apply") == 1

    error = capsys.readouterr().err
    assert error.startswith("hvac-predict: Home Assistant answered")
    assert error.count("\n") == 1


def test_train_does_not_write_a_model_that_failed_its_check(servers, config_path, tmp_path, capsys):
    # HVP-012
    (tmp_path / "model.json").write_text("the model from last week")
    servers.series["indoor"] = np.full(len(servers.series["indoor"]), 21.0)

    assert run(config_path, "train") == 1

    output = capsys.readouterr()
    assert "Weights:" in output.out
    assert "Model written" not in output.out
    assert output.err == (
        "hvac-predict: the fit found no link between outdoor and indoor temperature; "
        f"the model was not written to {tmp_path / 'model.json'}\n"
    )
    assert (tmp_path / "model.json").read_text() == "the model from last week"


def test_the_victoriametrics_token_is_sent_when_there_is_one(servers, config_path, monkeypatch):
    run(config_path, "train")
    assert servers.metrics_tokens == {None}

    monkeypatch.setenv("VICTORIAMETRICS_TOKEN", "metrics-token")
    run(config_path, "train")
    assert servers.metrics_tokens == {None, "Bearer metrics-token"}


def test_a_token_saved_with_a_stray_line_ending_still_works(servers, trained, monkeypatch):
    monkeypatch.setenv("HASS_TOKEN", "home-token\r\n")

    assert run(trained, "decide") == 0


def test_a_token_that_cannot_be_one_is_refused_without_being_printed(
    servers, trained, monkeypatch, capsys
):
    # HVP-013
    monkeypatch.setenv("HASS_TOKEN", "home token")

    assert run(trained, "decide") == 1

    output = capsys.readouterr()
    assert "the token for Home Assistant contains characters a token cannot have" in output.err
    assert "home token" not in output.err + output.out


def test_decide_needs_a_trained_model(servers, config_path, capsys):
    assert run(config_path, "decide") == 1

    assert "run `hvac-predict train` first" in capsys.readouterr().err


def test_decide_needs_a_home_assistant_token(servers, trained, monkeypatch, capsys):
    monkeypatch.delenv("HASS_TOKEN")

    assert run(trained, "decide") == 1

    assert "set HASS_TOKEN" in capsys.readouterr().err


def test_a_missing_configuration_file_is_an_error(tmp_path, capsys):
    assert run(str(tmp_path / "nowhere.toml"), "train") == 1

    assert capsys.readouterr().err.startswith("hvac-predict: cannot read")


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (httpx.Response(503), "503 Service Unavailable"),
        (httpx.Response(200, text="<html>Sign in</html>"), "something other than JSON"),
        (httpx.Response(200, json={"status": "success"}), "a result this tool cannot read"),
    ],
)
def test_a_server_failure_is_one_line_and_not_a_traceback(
    servers, config_path, capsys, answer, message
):
    # HVP-014
    servers.metrics_answer = answer

    assert run(config_path, "train") == 1

    error = capsys.readouterr().err
    assert error.startswith("hvac-predict: VictoriaMetrics answered")
    assert message in error
    assert error.count("\n") == 1


def test_help_names_the_token_variables(capsys):
    with pytest.raises(SystemExit) as stopped:
        main(["--help"])

    assert stopped.value.code == 0
    assert "HASS_TOKEN" in capsys.readouterr().out
