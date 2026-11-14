"""BB-21 booking service tests, plus the booking checks added to update_camp (real database).

PENDING DECISIONS are labelled: booking a slot that has already started is refused for now.
The reminder intent is not wired (enqueue_intent is not on main), so nothing about it is asserted.
"""
from datetime import timedelta

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied

from scheduling.exceptions import (CapacityConflict, DuplicateBooking, IneligibleDonor,
                                   ScheduleConflict, SlotUnavailable)
from scheduling.models import Appointment, Camp, Slot
from scheduling.selectors.appointments import list_own_appointments
from scheduling.services.appointments import book_appointment
from scheduling.services.camps import update_camp
from scheduling.tests.helpers import (clock_after_lock, make_camp, make_donor, make_slot, make_user,
                                      save_result)


class BookAppointmentTests(TestCase):
    def setUp(self):
        self.camp = make_camp(capacity=10)
        self.slot = make_slot(self.camp, capacity=2)
        self.donor, self.profile = make_donor()

    def book(self, user=None, slot=None, **kwargs):
        return book_appointment(actor=user or self.donor, slot_id=(slot or self.slot).id, **kwargs)

    def test_eligible_donor_books_and_is_linked_to_their_own_profile(self):
        dto = self.book()
        self.assertEqual(set(dto), {'id', 'donor_id', 'slot_id', 'scheduled_time', 'status', 'cancelled_at'})
        self.assertEqual(dto['donor_id'], str(self.profile.id))
        self.assertEqual(dto['status'], 'CONFIRMED')
        self.assertIsNone(dto['cancelled_at'])
        self.assertEqual(dto['scheduled_time'], self.slot.starts_at.isoformat())
        self.assertEqual(Appointment.objects.get().donor_id, self.profile.id)

    def test_walk_in_slot_can_be_booked(self):
        walk_in = make_slot(None, capacity=1)
        self.assertEqual(self.book(slot=walk_in)['status'], 'CONFIRMED')

    def test_only_active_donors_can_book(self):
        denied = [make_user('STAFF'), make_user('ADMIN'), make_user('REQUESTER'),
                  make_user('DONOR', is_active=False), make_user('DONOR', is_superuser=True),
                  AnonymousUser(), None]
        for person in denied:
            with self.subTest(person=repr(person)):
                with self.assertRaises(PermissionDenied):
                    book_appointment(actor=person, slot_id=self.slot.id)
        self.assertEqual(Appointment.objects.count(), 0)

    def test_donor_without_a_profile_cannot_book(self):
        with self.assertRaises(IneligibleDonor):
            self.book(make_user('DONOR'))

    def test_eligibility_must_be_current_and_eligible(self):
        no_result, _ = make_donor(eligible=None)
        ineligible, _ = make_donor(eligible=False)
        stale, _ = make_donor(eligible=True, rules_version='older-rules')
        for person in (no_result, ineligible, stale):
            with self.subTest(person=person.email):
                with self.assertRaises(IneligibleDonor):
                    self.book(person)
        self.assertEqual(Appointment.objects.count(), 0)

    def test_the_latest_result_decides(self):
        save_result(self.profile, 'INELIGIBLE')
        with self.assertRaises(IneligibleDonor):
            self.book()
        save_result(self.profile, 'ELIGIBLE')
        self.assertEqual(self.book()['status'], 'CONFIRMED')

    def test_duplicate_active_booking_refused_but_rebooking_after_cancel_allowed(self):
        self.book()
        with self.assertRaises(DuplicateBooking):
            self.book()
        Appointment.objects.update(status='CANCELLED')
        self.assertEqual(self.book()['status'], 'CONFIRMED')

    def test_full_slot_is_refused(self):
        self.book()
        self.book(make_donor()[0])
        with self.assertRaises(CapacityConflict):
            self.book(make_donor()[0])
        self.assertEqual(Appointment.objects.count(), 2)

    def test_camp_total_capacity_is_enforced_across_slots(self):
        Camp.objects.filter(pk=self.camp.pk).update(capacity=1)
        other = make_slot(self.camp, capacity=2)
        self.book(slot=other)
        with self.assertRaises(CapacityConflict):
            self.book(make_donor()[0])

    def test_unavailable_slots_cannot_be_booked(self):
        cancelled_slot = make_slot(self.camp, status='CANCELLED')
        cancelled_camp = make_camp(status='CANCELLED')
        in_cancelled_camp = make_slot(cancelled_camp)
        for slot in (cancelled_slot, in_cancelled_camp):
            with self.subTest(slot=str(slot.id)):
                with self.assertRaises(SlotUnavailable):
                    self.book(slot=slot)
        # PENDING DECISION: a slot that has already started is currently unavailable.
        with self.assertRaises(SlotUnavailable):
            self.book(now=self.slot.starts_at)
        self.assertEqual(Appointment.objects.count(), 0)

    def test_the_start_check_uses_the_clock_read_after_the_locks(self):
        # A request that waited on the lock across the slot's start must not succeed with a
        # timestamp taken before the wait (Arya, 14 Nov).
        start = self.slot.starts_at
        with clock_after_lock(before=start - timedelta(seconds=1), after=start):
            with self.assertRaises(SlotUnavailable):
                self.book()
        self.assertEqual(Appointment.objects.count(), 0)
        with clock_after_lock(before=start - timedelta(hours=2), after=start - timedelta(hours=1)):
            self.assertEqual(self.book()['status'], 'CONFIRMED')  # still before the start: fine

    def test_unknown_or_invalid_slot_is_not_found(self):
        for bad in ('00000000-0000-4000-8000-000000000009', 'not-a-uuid'):
            with self.subTest(slot=bad):
                with self.assertRaises(NotFound):
                    book_appointment(actor=self.donor, slot_id=bad)

    def test_donor_lists_only_their_own_appointments(self):
        self.book()
        other_donor, _ = make_donor()
        self.book(other_donor)
        page = list_own_appointments(actor=self.donor)
        self.assertEqual(page['count'], 1)
        self.assertEqual(page['results'][0]['donor_id'], str(self.profile.id))
        self.assertEqual(list_own_appointments(actor=make_user('DONOR'))['results'], [])
        with self.assertRaises(PermissionDenied):
            list_own_appointments(actor=make_user('STAFF'))


class UpdateCampWithBookingsTests(TestCase):
    def setUp(self):
        self.admin = make_user('ADMIN')
        self.camp = make_camp(capacity=3)
        self.slot = make_slot(self.camp, capacity=3)
        for _ in range(2):
            book_appointment(actor=make_donor()[0], slot_id=self.slot.id)

    def update(self, **data):
        return update_camp(actor=self.admin, camp_id=self.camp.id, data=data)

    def test_capacity_cannot_go_below_active_bookings(self):
        with self.assertRaises(CapacityConflict):
            self.update(capacity=1)
        self.assertEqual(self.update(capacity=2)['capacity'], 2)  # equal to the bookings is fine
        self.assertEqual(Camp.objects.get().capacity, 2)

    def test_cancelled_bookings_do_not_count(self):
        Appointment.objects.update(status='CANCELLED')
        self.assertEqual(self.update(capacity=1)['capacity'], 1)

    def test_moving_the_camp_cannot_strand_a_booked_slot(self):
        with self.assertRaises(ScheduleConflict):
            self.update(starts_at=self.slot.starts_at + timedelta(minutes=1))
        with self.assertRaises(ScheduleConflict):
            self.update(ends_at=self.slot.ends_at - timedelta(minutes=1))
        later_end = self.camp.ends_at + timedelta(hours=1)
        self.assertIn('ends_at', self.update(ends_at=later_end))

    def test_empty_slots_must_also_stay_inside_the_camp_interval(self):
        empty_camp = make_camp(capacity=3)
        empty_slot = make_slot(empty_camp, capacity=1)  # no bookings
        for change in ({'starts_at': empty_slot.starts_at + timedelta(minutes=1)},
                       {'ends_at': empty_slot.ends_at - timedelta(minutes=1)}):
            with self.subTest(change=list(change)):
                with self.assertRaises(ScheduleConflict):
                    update_camp(actor=self.admin, camp_id=empty_camp.id, data=change)
        # Rejected, never silently moved.
        before = (empty_slot.starts_at, empty_slot.ends_at, empty_camp.starts_at, empty_camp.ends_at)
        empty_slot.refresh_from_db()
        empty_camp.refresh_from_db()
        self.assertEqual((empty_slot.starts_at, empty_slot.ends_at,
                          empty_camp.starts_at, empty_camp.ends_at), before)

    def test_widening_the_camp_is_fine_and_cancelled_slots_are_ignored(self):
        camp = make_camp(capacity=3)
        make_slot(camp, capacity=1)
        make_slot(camp, capacity=1, status='CANCELLED',
                  starts_at=camp.starts_at, ends_at=camp.ends_at)
        dto = update_camp(actor=self.admin, camp_id=camp.id, data={
            'starts_at': camp.starts_at - timedelta(hours=1),
            'ends_at': camp.ends_at - timedelta(hours=3), 'capacity': 1})
        self.assertEqual(dto['capacity'], 1)

    def test_a_camp_without_slots_can_be_moved_and_shrunk(self):
        empty = make_camp(capacity=3)
        dto = update_camp(actor=self.admin, camp_id=empty.id, data={
            'capacity': 1, 'starts_at': empty.starts_at + timedelta(days=1),
            'ends_at': empty.ends_at + timedelta(days=1)})
        self.assertEqual(dto['capacity'], 1)
