# ADR: Steer setpoint pairs with the thermostat in auto mode

## Record

| Field | Value |
| --- | --- |
| Date | 2026-11-15 |
| Status | Accepted |
| Owner | dannysauer |
| Author | dannysauer, drafted with Claude Code |
| Scope | What the tool changes on the thermostat, and what it leaves alone. It does not cover how the tool chooses between pairs. |
| Related | [Give the house a fixed time to reach a new target unaided](2026-11-15-give-a-new-target-time-to-be-reached-unaided.md) |

## Business driver

The first version of the tool switched the thermostat between its heat, cool and off modes. That left the house exposed. If the tool stopped, or chose wrongly, the thermostat stayed in whatever mode it was last put in: cool mode on a night that turned cold, or off. The README had to tell the owner to build a separate Home Assistant automation as a safety net.

The owner runs an ecobee with a few comfort settings and wants the house to "remain fairly comfortable if the script stops or gets it wrong".

## Constraints

- The thermostat MUST keep the house near the target with no help from the tool.
- The ecobee's own schedule decides which comfort setting is in force. The tool does not own the schedule.
- An ecobee in auto mode takes a heating and a cooling setpoint, and enforces a minimum gap between them.
- The owner wants a differential of 3 to 4 °F between the two setpoints.

## Decision

**Y statement:** We will leave the thermostat in auto mode and have the tool choose between two pairs of setpoints for the target of the comfort setting in force, because both setpoints of either pair stay live when the tool is not running, so a stopped or mistaken tool costs at most the differential. The one wider pair, held while a house drifts to a new target, has a limit of its own, below.

For a target T and a differential d, the pairs are:

| Pair | Heating setpoint | Cooling setpoint | The equipment holds |
| --- | --- | --- | --- |
| Heat preference | T | T + d | T from below; T + d from above |
| Cool preference | T − d | T | T from above; T − d from below |

The boundary a later change must respect:

- The tool MUST NOT change the thermostat's HVAC mode, and MUST NOT act on a thermostat that is not in `heat_cool` mode.
- Every change the tool makes MUST set both setpoints, and one of them MUST be the target.
- For a house that has reached its target, the other setpoint MUST be exactly the differential away. While a house drifts to a new target it may be further, never nearer; see the [proposed completions](2026-11-15-complete-the-rules-where-the-owners-are-silent.md).
- The tool MUST NOT send a setpoint more than twice the differential plus one degree beyond the highest or lowest configured target, whatever a sensor or a file says.
- The target for each comfort setting comes from the tool's configuration, `[comfort.targets]`. The tool reads which comfort setting is in force from the thermostat, and MUST leave the thermostat alone when that setting has no configured target.
- The tool MUST leave the thermostat alone while it holds a preset that is neither the tool's own kind of hold nor the scheduled comfort setting, such as a vacation or an away hold.

## Consequences

- The Home Assistant safety-net automation is no longer needed. The thermostat is its own safety net. A tool that stops, is paused or starts refusing its data leaves the last pair in place until the thermostat's hold ends, and that includes the wider pair of a drift under way.
- Between a change of comfort setting and the tool's next run, up to ten minutes as scheduled, the thermostat holds the setpoints of its own comfort setting. The owner sets those on the ecobee, and they need to be temperatures the house can sit at.
- Each comfort setting has a single target. The air conditioner will run to reach a lower night-time target on a warm evening, where a thermostat with separate heating and cooling setpoints per comfort setting would not.
- The tool's setpoints are a hold on the ecobee. What ends that hold, and what the thermostat falls back to, is the thermostat's own "hold action" setting, which the tool neither reads nor sets.
- A hold the owner places by hand on the setpoints alone looks the same to the tool as its own, and the next run overwrites it. The enable switch is the way to stop that.
- `comfort.differential` has to be at least the thermostat's minimum gap, or the thermostat will not hold the pair as sent and the tool keeps resending it. Whether an ecobee refuses such a pair or moves a setpoint has not been checked.
- The cool preference puts the cooling setpoint on the target, and the heat preference puts the heating setpoint on it. A thermostat that limits how low it can be told to cool, or how high to heat, may not accept every pair. That has not been checked on the ecobee either.
- The tool reads the comfort setting from the `climate_mode` attribute, which is what Home Assistant's ecobee integration provides. Another thermostat needs `homeassistant.comfort_attribute` pointed elsewhere, and its presets may not mean what the ecobee's do.

## Alternatives considered

### Keep switching the HVAC mode

This is what the first version did, and it worked in simulation. It was rejected because a single-mode thermostat defends only one side, so the tool had to be right and running for the house to be safe.

### Switch between comfort settings defined on the thermostat

The pairs could live on the ecobee as extra comfort settings, such as "Home, heat" and "Home, cool", with the tool selecting a preset. That keeps the temperatures on the device. It doubles the comfort settings the owner maintains, and a hold on a comfort setting replaces the schedule's own choice, so the tool would end up owning the schedule. Rejected.

### Give each comfort setting two explicit pairs in the configuration

Four numbers per comfort setting would allow a cooling target different from the heating target. The owner described one target and one differential, so the simpler form was chosen. This can be added without changing the boundary above.

## Corrections

- 2026-11-16: The consequence that a hold placed by hand is overwritten no longer describes the code. The tool now leaves alone a hold it has no record of placing; see the proposed [hold record](2026-11-16-leave-a-hold-the-tool-did-not-place-alone.md).
- 2026-11-16: The consequence that the tool keeps resending a pair the thermostat will not hold as sent now describes only a pair the thermostat refuses outright. A pair it holds with a setpoint moved is taken for somebody else's hold and left alone; see the [hold record](2026-11-16-leave-a-hold-the-tool-did-not-place-alone.md).
