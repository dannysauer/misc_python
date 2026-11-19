# ADR: Predict with a fitted linear model that remembers stored heat

## Record

| Field | Value |
| --- | --- |
| Date | 2026-11-15 |
| Status | Proposed |
| Owner | dannysauer |
| Author | Claude Code, for dannysauer to accept or change |
| Scope | How the tool predicts the indoor temperature. It does not cover what the tool does with the prediction. |
| Related | [Evidence: stored-heat term](../planning/evidence/2026-11-15-stored-heat-term.txt) |
| Revisit when | `train` has run on the real house's history and its check has been read. |

## Business driver

Every decision the tool makes rests on one question: where will the indoor temperature be over the next few hours if nothing runs? The owner already keeps sensor history in VictoriaMetrics and asked for "basic machine learning, maybe linear regression with multiple variables or something else" to answer it from light, rain, wind and temperature.

A wrong answer is not harmless. The tool moves a real thermostat's setpoints on the strength of it.

The owner asked for a regression and called the first version "a good start". The form of the model below was chosen while building that version and has not been put to the owner as a decision, so this record is proposed.

## Constraints

- The model MUST be fitted from the history the owner already has. There is no building survey and no way to run experiments on the house.
- A run of the model MUST stay stable for eight hours. A fit that lets the temperature run away is worse than no model.
- The thermostat reports in coarse steps, so the fit has to cope with rounded readings.
- An operator has to be able to read the fitted model and see whether it is plausible.

## Decision

**Y statement:** We will predict the indoor temperature with a linear model of its rate of change, fitted to VictoriaMetrics history with every weight held to its physical sign, and with three smoothed terms that stand in for heat stored in the walls, because plain regression predicted a heavy-walled house worse than assuming no change, so the tool can trust its own forecast for the hours that matter.

The model is the equation at the top of [`src/hvac_predict/thermal.py`](../../src/hvac_predict/thermal.py). Its boundary:

- The fit MUST hold each weight to its physical sign: rain and the air conditioner cool, sunlight and the furnace warm, and the rest pull the indoor temperature towards what it is compared with. That is what keeps a run from running away.
- The three smoothed terms share one lag time. The fit cannot solve for it, so `train` MUST choose it by trying candidates on history the fit did not see.
- `train` MUST report how a fit to the older history predicts the newest fifth, beside the error of assuming no change, and MUST NOT write a model that does no better than that.
- The model is defined on the time grid of the history. The fit and every run MUST step it the same way, with the conditions at a grid point setting the rate for the step that begins there.

## Consequences

- On a simulated house with heavy walls, assuming no change was off by 0.86 °C eight hours ahead. Without the stored-heat term the model was off by 0.43 °C at its best lag time and 1.26 °C at a short one. With the term and the right lag time it was off by 0.06 °C. With the thermostat rounded to 0.5 °C it sits at about 0.16 °C, which is the rounding itself.
- The model is small enough to read: ten weights and a lag time, printed by `train`.
- It knows one lag time. A house with several very different thermal masses will be approximated.
- It is linear. It cannot learn that an open window or a full house changes the physics.
- The fit learns how far the house moves in one grid step, so the step is part of the model. Changing `training.step_minutes` needs a retrain, which `decide` enforces.
- A driver the history never shows varying gets no weight. A model trained in winter knows nothing about the air conditioner.

## Alternatives considered

### Plain regression on the measured drivers

This was the first prototype, before anything was committed: the rate of change against the indoor-outdoor gap, sunlight, wind and rain, with no memory. It is the simplest reading of the owner's request. No receipt was kept for it.

It lost to "no change" on a simulated heavy-walled house. The nearest rows in the receipt, a model without the stored-heat term at a lag of two or four hours, lose at four and eight hours. The walls hold the house's own recent temperature, and a model with no term for that cannot see it.

### A black-box model, such as gradient boosting or a neural network

It could fit effects a linear model misses. It needs far more data than 60 days to be trusted eight hours ahead, gives no guarantee of a stable run, and cannot be read by the operator. Rejected on all three constraints.

### Fit by minimising the error of whole simulated runs

Fitting to multi-hour runs, instead of to the rate of change, would optimise what the tool uses and would be less sensitive to rounded readings. It turns a bounded least-squares problem into a non-linear one, with starting-point and convergence questions. The chosen fit already reaches the rounding floor in simulation, so this was left out. It is the first thing to try if the real house fits badly.
