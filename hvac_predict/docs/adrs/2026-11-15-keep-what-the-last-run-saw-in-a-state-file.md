# ADR: Keep what the last run saw in a state file

## Record

| Field | Value |
| --- | --- |
| Date | 2026-11-15 |
| Status | Proposed |
| Owner | dannysauer |
| Author | Claude Code, for dannysauer to accept or change |
| Scope | How the tool knows when the target changed, where the house was then, whether it has reached the target since, and when the tool last changed preference. |
| Related | [Give the house a fixed time to reach a new target unaided](2026-11-15-give-a-new-target-time-to-be-reached-unaided.md) |
| Revisit when | The tool has run against the real Home Assistant, and the history API has been tried as a source for the same facts. |

## Business driver

The settling rule gives the house 90 minutes from the change of target. The hold keeps a choice for 30 minutes from when it was made. Both need a time that Home Assistant's state for the thermostat does not carry: its `last_changed` moves only when the HVAC mode changes, and the mode stays `heat_cool`.

The tool runs once and exits, so it has no memory of its own.

## Constraints

- The first version kept nothing between runs but the model. Anything added has to survive being lost or damaged: a file that is missing, or that holds something other than what the tool wrote, MUST NOT stop the tool or make it unsafe.
- A dry run has to show the same decision an applied run would make, or dry runs are no guide.
- Runs must not overlap, or one overwrites what the other recorded. The tool takes a lock beside the file and a second run stops at once.

## Decision

**Y statement:** We will have each run write what it saw to a small JSON file beside the configuration file, and the next run read it, because Home Assistant does not report when the target or the setpoints last changed, so that the settling time and the hold can be measured from the right moment.

The file is `state_path`, `state.json` by default. It holds the target, when the tool last ran, the preference the tool last set and when, the pairs it has sent that the thermostat may still be holding, and, while the house is on its way to a target that changed, when it changed and where the house was then. What the pairs are for is in the [hold record](2026-11-16-leave-a-hold-the-tool-did-not-place-alone.md).

The boundary:

- Every `decide` run that reaches a decision writes the file, with or without `--apply`. A run that leaves the thermostat alone, or fails before it has decided, writes nothing. The preference and its time change only when the tool has moved the thermostat to that preference.
- The file is written before the thermostat is touched. A run that cannot write it MUST NOT change the thermostat, or every later run would start afresh without knowing it.
- A file that is missing, or whose contents cannot be believed, means starting afresh: the house is taken to have been at the target, so there is no settling and no hold, and there is no record of a pair the tool sent, so a hold on the setpoints that is in force is taken for somebody else's until it ends. A time later than now is not believed.
- A target that differs from the one in the file is taken to have changed at this run only if the last run was within 30 minutes. After a longer silence nobody knows when it changed. It is then taken to have changed at the last run, the earliest it could have, which leaves the house the least time to drift.
- Where the house was at the change is the indoor temperature at the run that saw it. What the tool does with that temperature is limited in the [proposed completions](2026-11-15-complete-the-rules-where-the-owners-are-silent.md), so that neither a sensor nor an edited file can move a setpoint far.

## Consequences

- The time of a change is known to within one run interval, ten minutes as scheduled.
- A dry run writes a file. It changes nothing on the thermostat, which is what "dry" promises, but it is not read-only.
- Deleting `state.json` never makes the tool unsafe, and is the way to reset it. While the tool's own hold is in force the reset costs the time until that hold ends, which with holds that last until changed is until someone cancels it.
- A pause longer than 30 minutes counts as silence, because a run that leaves the thermostat alone writes nothing.
- Two schedulers running the tool against one configuration share the file and its lock, so whichever run starts second fails.
- Whoever can write the file can make the tool start or end a settling, replace a hold it did not place, or disown its own. The directory that holds it has to be writable by the operator alone.

## Alternatives considered

### Keep nothing, as before

Without the time of the change, the 90 minutes can only be measured from each run, which the settling record rejects.

### Ask Home Assistant's history API

`/api/history/period` returns the thermostat's past states with their attributes, so the last change of comfort setting and of the setpoints could be read from it, with nothing kept locally. It is the more accurate source. It was not chosen yet because its behaviour for attribute-only changes of a climate entity could not be checked without a real Home Assistant, and a wrong guess would silently disable the settling rule.

### Read the times from VictoriaMetrics

The Prometheus integration exports the thermostat's setpoints and its preset. It does not export the scheduled comfort setting while a hold is in force, which is exactly when the tool needs it.

### Put the schedule in the tool's configuration

The tool would then know every change in advance. The schedule would live in two places, and the ecobee's is the one that counts.
