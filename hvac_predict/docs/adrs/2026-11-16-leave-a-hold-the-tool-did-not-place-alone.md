# ADR: Leave a hold the tool did not place alone

## Record

| Field | Value |
| --- | --- |
| Date | 2026-11-16 |
| Status | Proposed |
| Owner | dannysauer |
| Author | Claude Code, for dannysauer to accept or change |
| Scope | How the tool tells its own hold on the setpoints from anybody else's, and what it does about the others. It does not cover presets Home Assistant reports by name, which the auto-mode record already leaves alone. |
| Related | [Steer setpoint pairs in auto mode](2026-11-15-steer-setpoint-pairs-in-auto-mode.md), [Keep what the last run saw in a state file](2026-11-15-keep-what-the-last-run-saw-in-a-state-file.md) |
| Revisit when | Check 3 in the [overview](../architecture/overview.md#to-check-against-the-real-system-first) has shown how the ecobee reports a vacation that starts under a hold. |

## Business driver

The owner needs a vacation set on the thermostat to keep its own setpoints. The tool already leaves the thermostat alone when Home Assistant reports a vacation.

That is not enough by itself. Home Assistant's ecobee integration reports only the first running event in the thermostat's list. The tool's own hold is nearly always running, and nobody has checked whether an ecobee lists a running vacation ahead of it. If it does not, Home Assistant goes on reporting the tool's hold, and the tool would steer through the vacation.

The same blindness had a second cost. A hold placed by hand on the setpoints looks exactly like the tool's own, and the next run overwrote it.

## Constraints

- The tool sees the thermostat only through Home Assistant's climate entity: a preset, two setpoints and a temperature. Nothing there says who placed a hold.
- The tool runs once and exits. Anything it knows about its own hold has to come from the state file.
- A tool that cannot tell MUST err towards leaving the thermostat alone. Both setpoints of whatever it leaves are live, so the house is protected either way.

## Decision

**Y statement:** We will have the tool remember which of its own pairs the thermostat was last seen holding and every pair it has sent since, and leave the thermostat alone whenever it is on a hold of setpoints that are none of them, because the setpoints are the only sign of whose hold it is, so that a vacation or a hold placed by hand keeps its setpoints whichever event Home Assistant reports, as long as the thermostat itself is on them.

The boundary:

- While the thermostat reports a hold on its setpoints alone, the tool MUST NOT send setpoints unless the ones the thermostat holds match a pair the tool has on record as sent.
- With no record, a hold on the setpoints is somebody else's. That includes a first run, a deleted state file, and one the tool could not believe.
- The pair about to be sent goes on record before it is sent. The pair of the tool's own that the thermostat holds at that moment stays on record, and so does every pair sent since that one, because nobody knows which of those sends arrived. A hold on any of them is then still the tool's own, however many sends fail in a row.
- Only one run at a time may read and write the record. A second run started meanwhile MUST stop without changing anything, or the record of one of them is lost and the tool disowns its own hold.
- Once no hold is in force the setpoints are the tool's to set again, whatever they are.

## Consequences

- A hold placed by hand stops the tool until it ends. With holds that last until changed, that is until the owner cancels it.
- A vacation is protected when Home Assistant reports it, and when the thermostat is on the vacation's setpoints while Home Assistant reports the tool's hold. It is not protected if the thermostat itself keeps to the tool's hold in spite of the vacation: the tool goes on steering, and the vacation starts only when the last hold it placed ends. Switching the enable entity off stops new holds and leaves the last one in place, so the only sure way is to switch it off and cancel that hold.
- A pair the thermostat changes before holding it, because of its minimum gap or its setpoint ranges, reads as somebody else's hold. The tool stops and says so, where it used to send the pair again every ten minutes. A pair the thermostat refuses outright, so that no hold is placed, is still sent again on every run.
- Setpoints count as a pair the tool sent when both are within 0.2 degrees of it in the unit Home Assistant shows. That is under half the smallest step a hand can make, so one step by hand makes a hold somebody else's. A thermostat that gives back setpoints further than that from what it was sent would make the tool disown its own hold, which is one of the checks for the real system.
- The record does not expire when the tool's hold ends. A later hold on exactly the setpoints the tool last sent would be taken for its own.
- Losing the state file while the tool's hold is in force costs the time until that hold ends.
- The tool learns of a hold from Home Assistant, which can be three minutes behind the thermostat. A hold or a vacation that starts inside that gap can still be overridden by a run that changes pair.
- The tool stops with exit status 0 and one line in its report. Nobody is told unless they read the log.

## Alternatives considered

### Keep trusting the preset

This is what the previous version did. It is right if the ecobee lists a vacation ahead of a hold, and silently wrong if it does not, in the one case the owner named as a requirement.

### Cancel the tool's hold whenever a vacation might be running

The `ecobee.resume_program` service would remove the hold so that the vacation shows. The tool cannot see the vacation to know when, and cancelling its own hold on every run would defeat the hold.

### Ask ecobee directly

The ecobee API lists every event with its type. That needs a second set of credentials and a second service to trust, for one fact.

### Compare with every pair the rules could produce

Any pair that fits a configured target could be taken for the tool's own, with nothing kept between runs. A person who sets the thermostat to exactly such a pair would then be overridden, and the drift pair depends on where the house was, so the set is not small.
