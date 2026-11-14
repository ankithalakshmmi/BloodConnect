# BB-21 temporary deviations and open items (draft PR)

## Appointment responses omit `reminder_state` (TEMPORARY DEVIATION)
The proposed appointment schema requires `reminder_state`. It is **omitted** until the real
notification provider exists. `notification_adapter.services.intents.enqueue_intent` is only in
PR #44 and is not on main, so booking does not write a reminder intent and no state is invented
or hard-coded. **Draft blocker:** wire the provider, then add `reminder_state` and the reminder
intent inside the booking transaction. Cancellation must invalidate pending reminders atomically
(Arya, 14 Nov).

## Decisions applied (Arya, 14 Nov)
- Booking a slot that has started (including exactly at its start) is refused: 409 `slot_unavailable`, rechecked after the locks are taken.
- A camp time change must keep every linked slot inside the camp interval, empty slots included. Conflicting changes are rejected (409 `schedule_conflict`); slots are never moved silently.
- ASSUMPTION to confirm: CANCELLED slots are not counted in that check.
- Donor self-cancellation (POST /api/v1/appointments/<id>/cancel): own confirmed appointment only, before the slot starts (422 `invalid_transition` at or after start), idempotent, history kept (status CANCELLED + cancelled_at), capacity never released twice. Invalidating pending reminders in the same transaction is NOT wired (no provider on main); draft blocker with the reminder.
- Migration `0002` is included with the matching models for draft review; its contents and deletion rules (PROTECT) still need review.
