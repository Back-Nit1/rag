# Contract fixtures

Synthetic records only. No real Bloomberg event, venue, agenda or attendee has
been verified, and no entry here reflects the user's actual calendar.

Every fixture validates against `../schemas/<Model>.json`. `make test` asserts
this, so a contract change that breaks a fixture fails CI rather than silently
drifting.

| File | Scenario it pins |
| --- | --- |
| `capture_bloomberg_text.json` | typed capture, source saved before extraction |
| `proposal_incomplete_event.json` | event intent with unknown date/registration, `requires_clarification` |
| `event_after_clarification.json` | one clarification resolves date + reminder time |
| `task_canceled.json` | cancellation preserves source history |
| `task_revised_deadline.json` | deadline change with a user-edited field protected |
| `reminder_pending_schedule.json` | saved entity, scheduler not yet synced |
| `plan_overflow.json` | capacity exceeded, overflow visible, not fitted into invented time |
| `work_packet_cerno.json` | engineering resumption with a failed attempt retained |
| `event_envelope_transcript_finalized.json` | canonical internal event envelope |
