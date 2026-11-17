# hvac-predict: notes for agents

`hvac-predict` is a command line tool that chooses the heating and cooling setpoints of a Home Assistant thermostat from a weather forecast and a thermal model of the house. It lives in one directory of a repository of otherwise unrelated scripts. Treat this directory as the project root: nothing outside it belongs to the project, and every path here is relative to it.

Read [`README.md`](README.md) first for what the tool does and how an operator uses it.

## Where things stand

The tool has only run against simulated houses and stand-ins for VictoriaMetrics and Home Assistant. The owner's thermostat is an ecobee, and nothing has been checked against it or against a real Home Assistant.

The owner wants comfort without having to think about it, ahead of saving energy, and needs a vacation set on the thermostat left alone. "Purpose" in [`docs/architecture/overview.md`](docs/architecture/overview.md) says what that rules in and out.

[`docs/planning/follow-up.md`](docs/planning/follow-up.md) is a snapshot of what still needs checking before the tool steers a real thermostat, with the results of the simulation testing. Start there if you are reviewing.

The work still open is listed under "Open questions" in [`docs/architecture/overview.md`](docs/architecture/overview.md). Its first group says what to check as soon as a real Home Assistant is reachable, and the table above it, "What the code assumes about Home Assistant", is what those checks compare against.

Two of the decision records are Accepted, because the owner stated them: [steer setpoint pairs in auto mode](docs/adrs/2026-11-15-steer-setpoint-pairs-in-auto-mode.md) and [give the house a fixed time to reach a new target](docs/adrs/2026-11-15-give-a-new-target-time-to-be-reached-unaided.md). Four are Proposed. They describe choices made while implementing, which the owner has not confirmed, so expect them to change:

- [Complete the rules where the owner's are silent](docs/adrs/2026-11-15-complete-the-rules-where-the-owners-are-silent.md)
- [Keep what the last run saw in a state file](docs/adrs/2026-11-15-keep-what-the-last-run-saw-in-a-state-file.md)
- [Leave a hold the tool did not place alone](docs/adrs/2026-11-16-leave-a-hold-the-tool-did-not-place-alone.md)
- [Predict with a fitted linear model that remembers stored heat](docs/adrs/2026-11-15-predict-with-a-model-that-remembers-stored-heat.md)

## Before you touch the real system

- Do not run `decide --apply`, or call `climate.set_temperature` or any other service that changes the thermostat, until the owner has said so in the conversation. `train` and a dry `decide` only read from the two services. A dry `decide` writes `state.json`.
- The token comes from the owner in `HASS_TOKEN`. Never print it, commit it, or put it in a receipt. `hvac-predict.toml`, `model.json` and `state.json` are the owner's and are ignored by git.
- Keep what you find as a dated receipt in [`docs/planning/evidence/`](docs/planning/evidence/), with addresses, entity names and tokens left out unless the owner wants them in.

## Design documents

The project keeps its design with [trellis](.trellis.yaml), rooted in this directory. Read these before changing the tool's behaviour:

1. [`docs/architecture/overview.md`](docs/architecture/overview.md) for what the system is and what is still open
2. [`docs/architecture/quality-attributes.md`](docs/architecture/quality-attributes.md) for the requirements it must keep
3. Accepted ADRs in [`docs/adrs/`](docs/adrs/) for decisions a change must respect

When documents disagree, an Accepted ADR wins over a living page, and a living page wins over a Proposed ADR or an open design doc. Report a conflict instead of silently resolving it.

Living pages describe the current state. When a change alters behaviour, update everything that describes it in the same change: the affected living pages, the README, `config.example.toml`, the `--help` text in `cli.py`, and the module docstring of `decision.py`. Put the history in an ADR, an evidence receipt, or the commit message. A changed decision gets a new ADR that supersedes the old one. An Accepted ADR is not edited, except to append a dated line under "Corrections" when a statement of fact in it no longer describes the code or the evidence.

Tests that verify a requirement name its ID, such as `HVP-003`, in a comment, so the requirement can be traced to its proof. Evidence receipts go in [`docs/planning/evidence/`](docs/planning/evidence/), dated, and are not edited once committed.

## Working here

```sh
uv sync                                 # install, with the development tools
uv run pytest                           # all tests, about a minute
uv run pytest tests/test_decision.py    # one file
uv run pytest -k settling               # tests whose names match
uv run mypy
uv run ruff check . && uv run ruff format --check .
uv run python tests/closed_loop.py 10   # the tool steering a simulated house for ten days
```

`pytest`, `mypy`, `ruff check` and `ruff format --check` pass on every commit. Run them before you hand work back.

Conventions the code keeps:

- Every temperature inside the code is in `units.temperature`, the unit the history queries return. Temperatures change unit at the edges only: `thermostat.py` for the thermostat's setpoints and reading, and `forecast.py` for the forecast. Limits written in Celsius, in `limits.py`, `config.py` and `advisor.py`, are converted where they are applied.
- `thermal.py` and `decision.py` do no I/O. Keep the model and the rules testable without fakes.
- What the code takes an ecobee to report is in `thermostat.py`, apart from the default name of the comfort attribute in `config.py`. In the tests, `thermostat_attributes` in `tests/fakes.py` is the one place that names the attributes a thermostat reports, and the stand-ins in `tests/test_cli.py` and `tests/closed_loop.py` build on it. Change the two together.
- An error an operator can fix is an `HvacPredictError`, and the command line prints it on one line. Anything else is a bug and may show a traceback.
- Data from VictoriaMetrics and Home Assistant is untrusted, and so is `state.json`. Check its form where it is read, and treat an unbelievable reading as missing.
- `tests/house.py` simulates a house with known physics. It shares no model code with `thermal.py`, so that it can check it.
- Dependencies are pinned to exact versions in `pyproject.toml`, and `uv.lock` is committed.

For prose, the owner uses the `green-thumb-doc-generator` skill while writing and `scorched-earth-doc-review` as the final review, when they are installed.
