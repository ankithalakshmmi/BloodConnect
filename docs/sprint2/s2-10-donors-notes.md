# S2-10 donors side: confirmed collection date and history-stale assessments

Decisions (Arya, 14 Nov): donors owns `last_confirmed_collection_date`; inventory calls a donors-owned
service inside the same transaction as CollectionEvent and the first BloodUnit; ordinary screening reads
it; every new EligibilityResult records the confirmed date it used; older assessments become
history-stale after a later collection; booking rejects history-stale results even if ELIGIBLE and
rules-current.

## What changed (migration donors/0003, two nullable dates, no data rewrite)
- `DonorProfile.last_confirmed_collection_date`: the projection.
- `EligibilityResult.confirmed_collection_date`: the confirmed date used at assessment time.
  `last_donation_date` stays the donor's self-reported answer.
- `donors.services.collections.record_confirmed_collection(*, donor_id, collected_on)`:
  must run inside the caller's `transaction.atomic()` (raises `TransactionRequired` otherwise),
  locks the profile row, never moves the date backwards, rejects future dates, NotFound for no profile.
  Returns the date now stored.
- `screen_donor` locks the profile, then evaluates with the LATER of self-reported and confirmed dates
  using the existing `min_days_between_donations`.
- History-stale rule (dates only, no timestamps): the profile has a confirmed date and the result
  recorded none or an earlier one. Rows saved before this change count as stale once a first collection
  is recorded.
- `read_saved_results` adds `confirmed_collection_date` and `is_history_stale` to its returned dict.
  The public result API serializer is unchanged (declared fields only).

## For callers
- Inventory: call `record_confirmed_collection` after creating the CollectionEvent and before/with the
  first BloodUnit, all in one `transaction.atomic()`. Lock order: donor profile first.
- Booking: reject when `is_stale` OR `is_history_stale`, and lock the donor profile row after the slot lock.

## Open
- `donors.selectors.search` still labels a history-stale ELIGIBLE donor as ELIGIBLE (only `is_stale`
  shows STALE). Yashwitha's file; decide whether search should show STALE for history-stale too.
- Tests for four mocked reader tests were adjusted (fake row gets `confirmed_collection_date=None`,
  profile lookup mocked).
