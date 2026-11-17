# ADR: Complete the rules where the owner's are silent

## Record

| Field | Value |
| --- | --- |
| Date | 2026-11-15 |
| Status | Proposed |
| Owner | dannysauer |
| Author | Claude Code, for dannysauer to accept or change |
| Scope | The cases the owner's settling rule does not cover: the idle setpoint during a drift, a house that needs nothing, overheating and how far ahead to look for it, how soon a choice may change, and how close to a target counts as there. |
| Related | [Give the house a fixed time to reach a new target unaided](2026-11-15-give-a-new-target-time-to-be-reached-unaided.md), [Evidence: closed-loop simulation](../planning/evidence/2026-11-16-closed-loop-simulation.txt) |
| Revisit when | The owner has watched the tool's dry runs against the real thermostat for a week. |

## Business driver

The owner's rule says what to do when a target drops and the house is warm, and when a cooling house has gone too far. A tool that runs every ten minutes meets other cases every day, and it needs an answer for each. These answers were chosen while implementing the rule. The owner has not confirmed them, so they are proposed, not accepted.

After seeing the first results the owner added that comfort without having to think about it matters more than saving energy. That supports looking a long way ahead for an overshoot, which is what costs the equipment hours. It counts against completion 2, which lets a house rest up to the differential from its target to keep the equipment off.

## Constraints

- Nothing here may contradict the [settling rule](2026-11-15-give-a-new-target-time-to-be-reached-unaided.md) or the [auto-mode boundary](2026-11-15-steer-setpoint-pairs-in-auto-mode.md).
- The thermostat is in auto mode with both setpoints live, so every answer has to say where both setpoints go.
- The tool was first asked to avoid "kicking on the ac or heat" when the house will sort itself out.

## Decision

**Y statement:** We will fill the gaps in the owner's rule with five completions, each the reading that keeps the equipment off when the house needs nothing and holds the target when it needs something, because the tool cannot run without an answer for these cases, so that the owner has a working whole to judge and change.

1. **The idle setpoint stays clear of a drifting house.** With a differential of 3 °F, the heat-preference pair for 64 °F is 64 to 67. A house still at 68 °F would start the air conditioner, which is the run the drift exists to avoid. While the house drifts to a new target, the idle setpoint is the further of its usual place and one differential beyond where the house started, with that starting temperature first rounded away from the target to a whole degree: 64 to 71 in the example. The start is taken to be no further out than a pair for some configured target would have let the house get, which is what limits the idle setpoint to twice the differential plus one degree beyond the highest or lowest target. It returns to its usual place once the house arrives.
2. **A house that needs nothing gets the pair it sits in.** When the prediction stays within the differential of the target, the tool holds the heat preference if the house is at or above the target and the cool preference if it is below. Either way the house is between the setpoints and nothing runs.
3. **The overshoot rule runs both ways, and looks eight hours ahead.** The owner described a house that cools too far. A house predicted to warm past the target plus the differential gets the cool preference in the same way. The first edge the prediction passes decides, and the tool looks `decision.overshoot_minutes` ahead for it, 480 by default.
4. **A resting house keeps a fresh choice for 30 minutes.** The resting rule does not reverse a preference within `decision.min_hold_minutes` of the tool setting it, as long as the thermostat still holds it. A new target starts afresh. The hold never delays the settling rule or the overshoot rule: those are the cases the settling record decides, and the ones in which the house needs the equipment.
5. **A house within a tenth of the differential of a new target has reached it.** A thermostat does not start its equipment for a fraction of a degree, so a house that close would otherwise stay "on its way" for good.

## Consequences

- Completion 1 means the setpoints can be further apart than the differential. While the tool runs that lasts at most `coast_minutes`. A tool that stops mid-drift leaves the wide pair until the thermostat's hold ends. During that time the far side is protected only at the idle setpoint, which can be a differential and a degree beyond where the house was when the target changed, within the limit in completion 1.
- Completion 3 is what uses the equipment. Holding the target as soon as an overshoot is predicted anywhere in eight hours means, on a warm day, cooling to the target from mid-morning instead of holding the far edge in the afternoon. In simulation the tool ran the equipment within 1.5 percent of the hours of the fixed pair that ran it more in the two warmer climates, and 24 percent more in the coldest.
- Completion 3 also acts at once on an overshoot that is hours away. A house under the heat preference that is predicted to pass the upper edge in two hours gets the cool preference now, and the air conditioner starts if the house is above the target. Once it has cooled a little the prediction may no longer pass the edge, and then completion 4 decides how long the equipment ran.
- A shorter look-ahead trades the hours back. At 90 minutes the simulated equipment hours fell by 8 to 19 percent, the house was further from target, and the tool changed setpoints two to three and a half times as often, up to 17 times a day. At 30 minutes it changed them up to 29 times a day.
- Completion 2 leaves a house that stays within the differential where it is. On a mild day that can be 2 or 3 °F from the target for hours, and a house that rested low overnight starts its morning warm-up from further down. The owner's example allows it at night; whether it is wanted by day is an open question.
- Completion 2 changes the pair when the house crosses the target. That relies on the thermostat not starting its equipment until the temperature is some way past a setpoint. It has not been checked on the ecobee, and the margin has to cover `min_hold_minutes`, not only the ten minutes between runs.

## Alternatives considered

### Hold exactly the two pairs, even during a drift

This is the literal reading of "select the heat preference pair or the cool preference pair". It keeps the setpoints the differential apart at all times. It also starts the air conditioner whenever a target drops by more than the differential, including the owner's own example with a 3 °F differential, so the drift would not be one.

### Keep whichever pair is held until the equipment is needed

With no resting rule the tool would leave the pair alone until an overshoot or a new target. A house under the heat preference that drifts below the target would then run the furnace, on a day it would have stayed within the differential by itself.

### Apply the overshoot rule to cooling only, or only when the edge is close

Either would run the equipment less. The owner's wording, "if it'll self-cool to 61F", is a statement about where the house ends up, not about the next half hour, and gives no reason to treat warming differently. The look-ahead is a setting so that this can be changed without code.

### No hold

Without it the tool changes preference whenever a prediction wavers at an edge, and each change can start or stop the equipment. In simulation with a 90-minute look-ahead, removing the hold roughly doubled the setpoint changes.

### Let the hold cover the settling rule and the overshoot rule too

The first drafts did. A drift that turned out too slow kept its preference for up to 30 minutes, and with the usual pair in place of the drift pair, which started the equipment the drift was avoiding. A house the air conditioner had just brought to the target, and which went on cooling by itself, kept the cool preference until the 30 minutes were up. Both broke the settling record, which says what the tool MUST hold in those cases.
