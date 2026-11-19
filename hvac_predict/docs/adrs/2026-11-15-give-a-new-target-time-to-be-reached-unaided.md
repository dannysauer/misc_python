# ADR: Give the house a fixed time to reach a new target unaided

## Record

| Field | Value |
| --- | --- |
| Date | 2026-11-15 |
| Status | Accepted |
| Owner | dannysauer |
| Author | dannysauer, drafted with Claude Code |
| Scope | How the tool chooses between the two pairs when the target changes, and when a drifting house has gone far enough. Cases the owner's rule does not mention are in a separate, proposed record. |
| Related | [Steer setpoint pairs in auto mode](2026-11-15-steer-setpoint-pairs-in-auto-mode.md), [Complete the rules where the owner's are silent](2026-11-15-complete-the-rules-where-the-owners-are-silent.md), [Evidence: closed-loop simulation](../planning/evidence/2026-11-15-closed-loop-simulation.txt) |

## Business driver

When a comfort setting changes the target, the house is usually on the wrong side of it. The equipment can take it there, or the weather can. The owner wants the weather to do it when it will do it soon enough, and gave the rule in these words:

> if I want the house heated to 68F during the day, but 64F at 9 pm, only switch to cooling if the house won't cool itself off by 10:30pm. But if it's going to keep cooling itself, switch back to heating preference if it'll self-overcool more than 3F beyond (so, if it'll self-cool to 61F).

## Constraints

- The choice is between the two pairs of the [auto-mode decision](2026-11-15-steer-setpoint-pairs-in-auto-mode.md). The thermostat stays in auto mode throughout.
- The house MUST be at a new target on time. Comfort comes before saving a run of the equipment.
- The only forecast of the house is the tool's own model.

## Decision

**Y statement:** After the target changes, we will hold the pair that lets the house drift to it when the model predicts it arrives within `decision.coast_minutes` of the change, 90 minutes by default, and otherwise the pair that drives it there, because the owner wants the target met on time without running equipment the weather was about to make unnecessary. Once the house has arrived, a drift predicted to pass the differential switches the preference, so that the equipment holds the target and not the far edge.

In the owner's example the target drops from 68 to 64 °F at 9 pm with a differential of 3 °F:

- If the house is predicted to reach 64 °F by 10:30 pm, the tool holds the heat preference. The furnace catches the house at 64 °F and the air conditioner stays off.
- If it is not, the tool holds the cool preference, 61 to 64 °F, and the air conditioner takes the house to 64 °F.
- Once the house is at 64 °F and is predicted to go on cooling past 61 °F, the tool holds the heat preference, 64 to 67 °F, so the furnace holds 64 °F and not 61 °F.

The boundary:

- The time allowed MUST run from the change of target, not from the moment of each run. A house that is always "half an hour away" has to be driven when the time is up.
- While the house has not reached the new target, the tool MUST hold the driving pair unless the prediction arrives within the time left. Nothing else the tool does, such as keeping a choice it made a few minutes ago, may delay the driving pair.
- The house has reached the target once a reading since the change is at it, or on the far side of it. After that this rule no longer applies.
- For a house that has arrived and is predicted to cool more than the differential below the target, the tool MUST select the heat preference, so that the furnace holds the target. The warming mirror of this, and how far ahead the prediction is consulted, are proposed in the [completions record](2026-11-15-complete-the-rules-where-the-owners-are-silent.md), not decided here.

## Consequences

- The air conditioner can run on an evening that will turn cold. If the house would reach 64 °F unaided in two hours and the limit is 90 minutes, the tool cools it, and may be heating it an hour later. That is the rule as given.
- The tool has to know when the target changed. Home Assistant does not say, so the tool keeps that between runs; see the [state file record](2026-11-15-keep-what-the-last-run-saw-in-a-state-file.md).
- The tool does not know the schedule. It learns of a new target only when the comfort setting changes, so it cannot start early, and it judges a drift against the target in force even when a change is minutes away.
- In simulation a new target was reached within about two and a half to four hours, where a thermostat left on one fixed pair sometimes had not reached it when the next change came. The rules stay stable, at five or six setpoint changes a day. They do not save equipment hours: the hours come out 2 to 24 percent above those of the fixed pair that ran the equipment more. The receipt has the numbers.

## Alternatives considered

### Keep the first version's rule

The first version let the house ride out any excursion outside a comfort band that was predicted to end within 90 minutes and stay within a margin. It measured from each run, with the time already spent outside counted against it. It was written for a thermostat switched between modes and has no notion of a target, so it cannot express "reach 64 °F by 10:30 pm".

### Measure the time from each run

Asking "will the house get there in the next 90 minutes" needs no memory of when the target changed. A prediction that keeps sliding would hold the equipment off indefinitely. Rejected for that reason.

### Always drive to a new target

Simplest, and the house is always on time. It runs the air conditioner at 9 pm on a night the house would have cooled in twenty minutes, which is the waste the owner set out to avoid.

## Corrections

- 2026-11-16: The simulation was run again after the code changed, and the figures above moved a little. Equipment hours now come out within 1.5 percent of the fixed pair that ran the equipment more in two climates and 24 percent above it in the third. The current numbers are in the [receipt of 2026-11-16](../planning/evidence/2026-11-16-closed-loop-simulation.txt).
- 2026-11-16: The code reads "at it" in the third boundary line, and "arrives" in the decision and the second boundary line, as within a tenth of the differential of the target. That reading is completion 5 of the proposed [completions record](2026-11-15-complete-the-rules-where-the-owners-are-silent.md), and the owner has not confirmed it.
