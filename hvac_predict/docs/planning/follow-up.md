# Follow-up concerns and simulation testing

> **Summary:** What still needs checking before `hvac-predict` steers a real thermostat, and what the simulations did and did not show. This is a snapshot written on 2026-11-17 for the review that comes before first use. Nobody will keep it current; the [open questions](../architecture/overview.md#open-questions) in the architecture overview are the list that stays up to date.

## Where things stand

The tool has run only against simulated houses and stand-ins for Home Assistant and VictoriaMetrics. Everything it assumes about the ecobee was read from the source of Home Assistant's ecobee integration, not learned from a device.

AI agents wrote the code and reviewed it, in several rounds. Every round found real defects, some of them in the fixes for the round before. So treat this as a draft that passes its own tests. It has proved nothing about the owner's house yet.

## Concerns

### The thermostat may not behave as the code assumes

Only a real Home Assistant can settle these. Each has a numbered check in the overview, under [To check against the real system first](../architecture/overview.md#to-check-against-the-real-system-first). Checks 2 and 3 change the thermostat and need the owner's go-ahead, and so does the first `decide --apply`. `train` and a dry `decide` only read from the two services; a dry `decide` writes `state.json`.

| The code assumes | If it's wrong | Check |
| --- | --- | --- |
| `climate_mode` names the scheduled comfort setting, `preset_mode` reads `temp` under the tool's hold, and the setpoints and the temperature are numbers | `decide` fails, or after its first change leaves the thermostat alone until that hold ends | 1, 2 |
| A running vacation is reported ahead of the tool's hold, or at least its setpoints are the ones in force | The tool can't tell, goes on steering, and every pair it sends is a new hold. The vacation's setpoints take effect only when the last of those holds ends: at the next scheduled activity if holds end there, and never if holds last until changed | 3 |
| The thermostat accepts every pair: the differential is at least its minimum gap, and each setpoint is inside its range | The tool stops if the thermostat alters a pair. It sends a pair again every ten minutes if the thermostat refuses it outright | 4 |
| The thermostat waits a fraction of a degree past a setpoint before it starts its equipment | Changing pair as the house crosses the target starts the furnace or the air conditioner | 5 |
| Each example query returns one series, in the unit the configuration names | `train` or `decide` fails, or the model is fitted to the wrong numbers | 6 |
| The model fits the real house, and the heating and cooling series are configured | A model trained without those two series passes its own check and still predicts the idle house wrongly by degrees | 7 |
| Smart Home/Away is reported as `away` or `home`, and no other event runs | The tool steers through an event that Home Assistant does not report as a preset, such as a demand-response event, and through a Smart Home event while Home is also the scheduled setting | 8 |
| Home Assistant's picture of the thermostat is fresh | A hold or a vacation that starts within about three minutes of a run, or longer while ecobee's service is unreachable, can be overridden | 9 |
| The thermostat gives back the setpoints it was sent, within 0.2 degrees in Home Assistant's unit | After its first change the tool takes its own hold for somebody else's and stops steering until that hold ends | 10 |

The vacation row is the one the owner named as a requirement. Until check 3 has been done, the only routine that protects a vacation is the one in the README: switch `enable_entity` off before the vacation starts, then cancel the tool's hold. That needs `homeassistant.enable_entity` set, and the example configuration leaves it commented out, so make it a condition of the first `--apply`. Without one, remove the cron line before a vacation instead.

### A tool that has stopped says so only in its log

The tool leaves the thermostat alone in five situations, and each ends with exit status 0 and one line of output. Any of them can last. The one nobody chooses is a hold the tool has no record of placing: a hold placed by hand, a pair the thermostat altered, and a lost state file all look like that, and leave the tool waiting for the hold in force to end. With holds that last until changed, that's until somebody cancels one.

Nothing tells anyone. The `left_alone` value in `decisions.jsonl` is the only sign, so before the tool goes on a timer, decide who reads that log or put an alert on it.

A stopped tool also leaves its last pair behind. When it is paused, stops, or starts refusing its data, the thermostat keeps that pair until its hold ends, and it can be the wide pair of a drift: up to twice the differential plus one degree beyond the highest or lowest target. With holds that last until changed, that is until somebody cancels the hold. The tool never releases a hold itself (open question 20).

### Behaviour that follows the rules and may not be what the owner wants

- The air conditioner runs on winter nights. The owner's rule drives the house to a lower night-time target whenever it won't get there unaided within `coast_minutes`, and a house that holds its heat won't. In simulation at a mean of 2 °C outdoors and the default 90 minutes, the air conditioner ran 1.3 hours in four days. In the coldest of the three ten-day climates the tool chose to drive 163 times and to drift 9 times, though those drives include the furnace's on the way up in the morning.
- A resting house can sit up to the differential from its target. The tool runs nothing while the house stays within the differential by itself, which on a mild day can mean 2 or 3 °F off for hours. It also lets a stepped morning warm-up start from below the night-time target. The owner has been asked whether to hold the target more closely and hasn't answered yet.
- With holds that end at the next scheduled activity, the thermostat's own comfort setpoints are in charge for up to about thirteen minutes after each schedule change. If they are a summer set in winter, the air conditioner starts at bedtime before the tool has run. The README says how to choose them, and none of that advice has been tried.
- Each comfort setting has one target and shares one differential. The owner's own settings use a wider pair at night, and a Morning step that matters only when the furnace is working.
- A target that rises waits for the weather the same way one that drops does. On a mild morning the furnace is held off for up to `coast_minutes` at each step.
- An overshoot predicted hours away switches the pair now, and the equipment can start at once.
- A drift holds a third, wider pair. The owner asked for two pairs and hasn't confirmed this one.
- An Accepted record is not met as written. The settling record says the house MUST be at a new target on time, and in simulation the tool's slowest arrival was 155 to 235 minutes.

The first seven are open questions 13, 11, 19, 14, 16, 12 and 15 in the overview, in that order. The last is noted beside the settling record in the overview's list of decisions. Four of the six decision records are still Proposed: the owner stated the auto-mode decision and the settling rule, and the rest are the implementer's choices.

### Weak spots in the code

- **The state file matters more than it looks.** Without it the tool has no record of the hold it placed, and waits for that hold to end. A lock keeps two runs from overwriting each other's record; it uses `fcntl`, so the tool runs on Linux and macOS only.
- **The record of pairs sent never expires.** After the tool's hold has ended, a later hold on the same setpoints, to within 0.2 degrees, would be taken for its own.
- **Some staleness goes unchecked.** `decide` doesn't look at the age of the model, of the thermostat state it reads, or of the outdoor sensor beyond "a sample arrived in the last 20 minutes". With the example queries, the check of the indoor temperature against the thermostat compares one Home Assistant attribute with itself.
- **A settling house can change between drifting and driving on every run.** The hold doesn't apply while a house settles. Once it has settled, the resting rule can reverse the pair when the hold expires and the overshoot rule can reverse it back ten minutes later. [Reversals of preference](#reversals-of-preference) has the counts.
- **A failed second write of the state file loses the hold's clock.** One early reversal is the cost.
- **The tests share one description of the ecobee.** The three stand-ins are built on `thermostat_attributes` in `tests/fakes.py`, which is the same reading of the integration's source that the code is. They can't catch a wrong reading.
- **Only Python 3.13 has run the tests.** `pyproject.toml` allows 3.12.

### What was and was not reviewed

Four kinds of review ran over the code as it grew: correctness, security, maintainability, and documentation. AI agents that had not written the code did them, in four rounds. Each round's findings were fixed and the next round checked the fixes, except for the last: the fixes from the fourth round have tests and seeded-fault checks behind them and no independent review. Those fixes are the record of pairs sent in `state.py`, the lock in `files.py`, the rewrite of `steer` in `advisor.py` that takes the lock and writes the record before it sends, in `thermostat.py` the matching of setpoints and the decision about whose hold the thermostat is on, a check for looping links in `config.py`, and the wording of one reason in `decision.py`.

The owner's `thermo-nuclear-code-quality-review` is reserved for the owner to run, and hasn't been run. A strict maintainability reviewer stood in for it.

Maintainability reviews left two things alone on purpose: `advise` takes seven parameters, and the weather generator in `tests/closed_loop.py` repeats part of the one in `tests/house.py`.

## Simulation testing

### What is simulated

`tests/house.py` is a house with known physics, written without the package's model code. Its air warms and cools with the gap to outdoors, wind, sunlight, rain and the equipment, and it trades heat with walls ten times heavier than the air, which lag the weather by about fourteen hours.

`tests/closed_loop.py` puts an auto-mode thermostat in that house and runs the tool against it for days, through the same `steer` function the command line calls:

- The schedule has two comfort settings, Home at 20 °C from 06:00 to 21:00 and Sleep at 17.5 °C otherwise, with a differential of 2 °C.
- The thermostat starts its equipment 0.25 °C past a setpoint and stops it 0.25 °C inside.
- At each change of comfort setting the thermostat goes to its own setpoints for the new one, and the tool runs in that same five-minute step and every ten minutes after.
- A month on a fixed heat-preference pair trains the model. The days after are measured.
- The forecast is the weather that then happens.

### Ten days in three climates

Each row is ten measured days. "Fixed" rows have no tool: the thermostat keeps one kind of pair. The slowest arrival is the longest the house took to come within 0.25 °C of a new target; "not reached" means some target had not been reached when the next change came, 540 or 900 minutes later. Total is the two rounded columns added, and can be 0.1 off the true total.

| Mean outdoors | Run | Furnace (h) | Cooling (h) | Total (h) | Mean distance from target (°C) | Slowest arrival (min) | Setpoint changes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 10 °C | Fixed heat-preference pair | 32.6 | 9.9 | 42.5 | 1.01 | not reached (540) | 0 |
| 10 °C | Fixed cool-preference pair | 17.7 | 26.6 | 44.3 | 0.93 | not reached (900) | 0 |
| 10 °C | Tool, defaults | 33.3 | 21.7 | 55.0 | 0.72 | 160 | 51 |
| 10 °C | Tool, looking 90 min ahead | 27.8 | 16.9 | 44.7 | 0.93 | 195 | 103 |
| 10 °C | Tool, 90 min ahead, no hold | 26.4 | 16.0 | 42.4 | 0.97 | 195 | 208 |
| 10 °C | Tool, looking 30 min ahead | 24.2 | 15.7 | 39.9 | 1.03 | 220 | 90 |
| 14 °C | Fixed heat-preference pair | 9.4 | 44.5 | 53.9 | 1.49 | not reached (540) | 0 |
| 14 °C | Fixed cool-preference pair | 5.2 | 72.4 | 77.6 | 0.50 | not reached (900) | 0 |
| 14 °C | Tool, defaults | 11.2 | 67.4 | 78.6 | 0.63 | 155 | 60 |
| 14 °C | Tool, looking 90 min ahead | 7.7 | 59.2 | 66.9 | 0.83 | 160 | 129 |
| 14 °C | Tool, 90 min ahead, no hold | 7.0 | 56.8 | 63.8 | 0.92 | 170 | 251 |
| 14 °C | Tool, looking 30 min ahead | 6.7 | 53.9 | 60.6 | 1.05 | 180 | 179 |
| 18 °C | Fixed heat-preference pair | 3.3 | 98.4 | 101.7 | 1.90 | not reached (540) | 0 |
| 18 °C | Fixed cool-preference pair | 2.3 | 132.9 | 135.2 | 0.39 | 310 | 0 |
| 18 °C | Tool, defaults | 5.8 | 128.8 | 134.6 | 0.57 | 235 | 49 |
| 18 °C | Tool, looking 90 min ahead | 3.0 | 120.4 | 123.4 | 0.79 | 250 | 168 |
| 18 °C | Tool, 90 min ahead, no hold | 3.4 | 118.9 | 122.3 | 0.86 | 255 | 326 |
| 18 °C | Tool, looking 30 min ahead | 1.8 | 110.2 | 112.0 | 1.23 | 310 | 285 |

Reading it:

- **The tool does not save energy.** It ran the equipment 29 to 46 percent more hours than the fixed pair that ran it less. Against the pair that ran it more, it came within 1.5 percent in the two warmer climates and ran 24 percent more in the coldest.
- **It reaches a new target sooner.** Its slowest arrival was 155 to 235 minutes. A fixed pair took 310 minutes in one run and missed at least one target in the other five.
- **It doesn't always hold the target more closely.** It was closest of the three in the coldest climate. In the other two the fixed cool-preference pair was closer, for equipment hours within 1.5 percent of the tool's. What that pair gave up was arrival: at 14 °C it left a target unreached, and at 18 °C its slowest arrival was 310 minutes against the tool's 235.
- **It stays stable.** At the defaults it moved the setpoints five or six times a day.
- **Looking less far ahead trades comfort for hours.** At 90 minutes the equipment ran 8 to 19 percent less, the house was further from target, and the setpoints moved two to three and a half times as often.

The receipt is [`evidence/2026-11-16-closed-loop-simulation.txt`](evidence/2026-11-16-closed-loop-simulation.txt). A fresh run of the final code reproduces its 33 lines of results exactly.

### What the settling rule buys on a cold night

Four days at a mean of 2 °C outdoors, with different times allowed for the house to reach a new target unaided:

| `coast_minutes` | Decisions to drift | Furnace (h) | Cooling (h) | Steps in which the equipment a drift was avoiding ran |
| --- | --- | --- | --- | --- |
| 180 | 48 | 43.2 | 0.0 | 0 |
| 90 (default) | 20 | 44.2 | 1.3 | 0 |
| 0 | 0 | 45.1 | 2.5 | 0 |

Given three hours the house cools to its night-time target without the air conditioner. Given none, it is driven there every evening. The last column is the check on the widened pair of a drift: with the usual pair in its place, the air conditioner ran in 10 steps of the first row, for 0.8 hours.

`tests/test_closed_loop.py` checks the first and last rows in outline: more than 20 decisions to drift against none, less than half the cooling hours, and no step in the first row in which the air conditioner ran against a drift.

### What the hold buys

The hold keeps the resting rule from reversing a preference the tool set less than 30 minutes ago. Its effect shows when the rules change their mind often, which they do with a short look-ahead. In the ten-day runs at 90 minutes, removing the hold roughly doubled the setpoint changes: 103 to 208, 129 to 251, and 168 to 326.

### Outages

A reviewing agent wrote this scenario after finding that two failed sends in a row made the tool disown its own hold. Every five hours, starting 35 minutes into the measured days, Home Assistant accepts `climate.set_temperature` for the length of the outage and nothing reaches the thermostat, which is what the integration does when ecobee's service is down. Only sends are lost: the simulated Home Assistant goes on reporting the thermostat's true state, where a real outage would freeze it. Six days at a mean of 14 °C, 29 outages per run, 864 runs of the tool:

| Holds | Outage length (min) | Sends lost | Times the tool disowned its own hold |
| --- | --- | --- | --- |
| End at the next comfort change | 10 | 1 | 0 |
| End at the next comfort change | 20 | 4 | 0 |
| End at the next comfort change | 30 | 9 | 0 |
| End at the next comfort change | 60 | 26 | 0 |
| Last until changed | 10 | 1 | 0 |
| Last until changed | 20 | 4 | 0 |
| Last until changed | 30 | 10 | 0 |
| Last until changed | 60 | 35 | 0 |

Before the fix the same scenario went wrong: once with 30-minute outages, three times with 60-minute ones, and with holds that last until changed the tool never recovered from a 20-minute outage.

### Reversals of preference

A reversal is a run that sends the opposite preference from the run before, within one comfort setting. Six days per row, 864 runs:

| Mean outdoors | Settings changed | Setpoint changes | Reversals of preference | Longest streak of reversals | Most reversals in one hour |
| --- | --- | --- | --- | --- | --- |
| 2 °C | none (defaults) | 25 | 14 | 1 | 2 |
| 2 °C | `overshoot_minutes = 90` | 79 | 67 | 2 | 3 |
| 2 °C | `overshoot_minutes = 30` | 140 | 130 | 2 | 4 |
| 2 °C | `min_hold_minutes = 120` | 27 | 15 | 1 | 2 |
| 10 °C | none (defaults) | 31 | 24 | 1 | 2 |
| 10 °C | `overshoot_minutes = 90` | 69 | 63 | 2 | 3 |
| 10 °C | `overshoot_minutes = 30` | 59 | 52 | 1 | 2 |
| 10 °C | `min_hold_minutes = 120` | 29 | 22 | 1 | 1 |
| 14 °C | none (defaults) | 36 | 29 | 1 | 3 |
| 14 °C | `overshoot_minutes = 90` | 76 | 68 | 2 | 3 |
| 14 °C | `overshoot_minutes = 30` | 105 | 96 | 2 | 4 |
| 14 °C | `min_hold_minutes = 120` | 33 | 26 | 1 | 2 |
| 18 °C | none (defaults) | 26 | 16 | 1 | 2 |
| 18 °C | `overshoot_minutes = 90` | 97 | 86 | 2 | 3 |
| 18 °C | `overshoot_minutes = 30` | 155 | 140 | 2 | 4 |
| 18 °C | `min_hold_minutes = 120` | 28 | 18 | 1 | 1 |
| 22 °C | none (defaults) | 27 | 12 | 2 | 3 |
| 22 °C | `overshoot_minutes = 90` | 59 | 45 | 2 | 3 |
| 22 °C | `overshoot_minutes = 30` | 132 | 117 | 2 | 4 |
| 22 °C | `min_hold_minutes = 120` | 27 | 12 | 2 | 3 |

At the defaults the preference reversed two to five times a day and never more than three times in an hour. A two-hour hold made little difference at the defaults. A shorter look-ahead multiplied the reversals.

### The model on the simulated house

`train` checks a model by fitting it to the older four fifths of the history and predicting the newest fifth. On 20 days of the simulated house it was off by 0.03 °C one hour ahead and 0.08 °C eight hours ahead, against 0.34 and 0.94 °C for assuming no change. That is a best case: each run is given the weather that was measured, and a real house is noisier.

One result cuts the other way. A reviewing agent trained a model without the heating and cooling series on a house whose furnace ran 7.7 percent of the time. The model passed its check and then predicted a six-hour idle stretch 3.2 °C too warm, because the check replays history with the equipment running and can't see the error.

[`evidence/2026-11-15-stored-heat-term.txt`](evidence/2026-11-15-stored-heat-term.txt) has the numbers behind the model's stored-heat term. They come from a prototype that wasn't kept.

### Faults seeded on purpose

To see whether the tests would notice a broken rule, about eighty single-line faults were seeded into copies of the rules, the state handling and the thermostat module, one at a time, and the tests were run against each. The reviewing agents seeded about as many again. Every fault the tests missed became a new test, with two exceptions that no test can catch. One changes nothing: rounding the limits on the differential. The other needs a case that can't arise: a house that sits on its target and is still counted as on its way there.

The closed-loop tests were checked the same way. With the settling rule, the overshoot rule, the hold or the widened drift pair removed, at least one of them fails.

### The limit on setpoints

A reviewing agent fuzzed whole runs of the tool with hostile histories, thermostat attributes, state files and unit combinations: about 39,000 runs on an earlier version. Nothing crashed, and no run sent a pair that was inverted, not a number, or without the target in it. Two runs passed the limit of requirement HVP-018 by 0.06 °F or less, because the limit is applied before the setpoint is rounded for the thermostat. The requirement now says so. The fuzzing has not been repeated on the final code.

### What the simulations cannot show

- **The real house.** The simulated one has the kind of physics the model was built for.
- **Forecast error.** The tool is given the weather that then happens.
- **The ecobee.** The simulated thermostat obeys at once, reports at once, and gives back what it was sent.
- **Sensor trouble.** A few tests round the thermostat's reading or drop samples; none models a sensor that drifts or freezes.
- **The thermostat's own setpoints and hold length.** The simulated thermostat's comfort setpoints are the tool's own heat-preference pair, its holds end at the next comfort change, and the tool runs in the same step as the change, so the minutes on other setpoints never happen. Holds that last until changed appear only in the outage scenario, and two- or four-hour holds nowhere.
- **Fahrenheit.** The simulations run in Celsius. The unit tests cover the conversions.

### Run it yourself

From the project directory:

```sh
uv run pytest                           # every test, including four days of closed loop
uv run python tests/closed_loop.py 10   # the ten-day table, about a minute
```

The outage and reversal scenarios were scripts written during review. They are not part of the test suite; their text and output are in [`evidence/2026-11-17-outage-and-reversal-simulations.txt`](evidence/2026-11-17-outage-and-reversal-simulations.txt), with the cold-night runs. The fuzzing scripts are not in the repository, and the description above is not enough to rebuild them.
