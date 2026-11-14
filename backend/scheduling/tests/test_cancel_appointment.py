"""Donor self-cancellation (Arya, 14 Nov): own confirmed appointment, before the slot starts,
idempotent, history kept. The reminder invalidation is INTEGRATION_PENDING (no provider on main)."""
from datetime import timedelta

from django.contrib.auth.models import AnonymousUser
from django.test import Client, TestCase
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied

from scheduling.exceptions import AppointmentNotCancellable, CapacityConflict
from scheduling.models import Appointment
from scheduling.services.appointments import book_appointment, cancel_appointment
from scheduling.tests.helpers import clock_after_lock, make_camp, make_donor, make_slot, make_user


def client_for(user):
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    return client, client.get('/api/v1/auth/csrf').json()['csrf_token']


class CancelServiceTests(TestCase):
    def setUp(self):
        self.camp = make_camp(capacity=5)
        self.slot = make_slot(self.camp, capacity=1)
        self.donor, self.profile = make_donor()
        self.appt = book_appointment(actor=self.donor, slot_id=self.slot.id)

    def cancel(self, user=None, appt_id=None, **kwargs):
        return cancel_appointment(actor=user or self.donor,
                                  appointment_id=appt_id or self.appt['id'], **kwargs)

    def test_donor_cancels_their_own_appointment_and_history_is_kept(self):
        dto = self.cancel()
        self.assertEqual(dto['status'], 'CANCELLED')
        self.assertIsNotNone(dto['cancelled_at'])
        row = Appointment.objects.get(pk=self.appt['id'])
        self.assertEqual((row.status, row.donor_id), ('CANCELLED', self.profile.id))
        self.assertEqual(Appointment.objects.count(), 1)  # not deleted

    def test_cancelling_frees_the_place_exactly_once(self):
        self.cancel()
        again = self.cancel()  # harmless repeat
        self.assertEqual(again['status'], 'CANCELLED')
        other, _ = make_donor()
        book_appointment(actor=other, slot_id=self.slot.id)  # the single place is free again
        third, _ = make_donor()
        with self.assertRaises(CapacityConflict):  # and was not released twice
            book_appointment(actor=third, slot_id=self.slot.id)
        self.cancel()  # a third cancel still changes nothing
        self.assertEqual(Appointment.objects.filter(status='CONFIRMED').count(), 1)

    def test_repeat_keeps_the_original_cancelled_at(self):
        first = self.cancel()['cancelled_at']
        self.assertEqual(self.cancel()['cancelled_at'], first)

    def test_cannot_cancel_once_the_slot_has_started_including_exactly_at_start(self):
        for now in (self.slot.starts_at, self.slot.starts_at + timedelta(minutes=1)):
            with self.subTest(now=now.isoformat()):
                with self.assertRaises(AppointmentNotCancellable):
                    self.cancel(now=now)
        self.assertEqual(Appointment.objects.get().status, 'CONFIRMED')
        self.assertEqual(self.cancel(now=self.slot.starts_at - timedelta(seconds=1))['status'], 'CANCELLED')

    def test_the_start_check_uses_the_clock_read_after_the_locks(self):
        start = self.slot.starts_at
        with clock_after_lock(before=start - timedelta(seconds=1), after=start):
            with self.assertRaises(AppointmentNotCancellable):
                self.cancel()
        row = Appointment.objects.get()
        self.assertEqual((row.status, row.cancelled_at), ('CONFIRMED', None))
        with clock_after_lock(before=start - timedelta(hours=2), after=start - timedelta(hours=1)):
            self.assertEqual(self.cancel()['status'], 'CANCELLED')

    def test_repeat_after_a_late_clock_stays_harmless(self):
        start = self.slot.starts_at
        self.cancel()
        with clock_after_lock(before=start - timedelta(hours=1), after=start + timedelta(hours=1)):
            self.assertEqual(self.cancel()['status'], 'CANCELLED')

    def test_repeating_a_cancel_after_start_stays_harmless(self):
        self.cancel()
        self.assertEqual(self.cancel(now=self.slot.starts_at + timedelta(hours=1))['status'], 'CANCELLED')

    def test_other_donors_staff_and_anonymous(self):
        other, _ = make_donor()
        with self.assertRaises(NotFound):  # someone else's appointment looks unknown
            self.cancel(other)
        for person in (make_user('STAFF'), make_user('ADMIN'), make_user('DONOR', is_superuser=True),
                       make_user('DONOR', is_active=False), AnonymousUser(), None):
            with self.subTest(person=repr(person)):
                with self.assertRaises(PermissionDenied):
                    cancel_appointment(actor=person, appointment_id=self.appt['id'])
        with self.assertRaises(NotFound):  # donor with no profile
            self.cancel(make_user('DONOR'))
        self.assertEqual(Appointment.objects.get().status, 'CONFIRMED')

    def test_unknown_or_malformed_id_is_not_found(self):
        for bad in ('00000000-0000-4000-8000-000000000009', 'not-a-uuid'):
            with self.subTest(appt=bad):
                with self.assertRaises(NotFound):
                    self.cancel(appt_id=bad)


class CancelApiTests(TestCase):
    def setUp(self):
        self.slot = make_slot(make_camp(capacity=5), capacity=2)
        self.donor, _ = make_donor()
        self.client, self.token = client_for(self.donor)
        self.appt = book_appointment(actor=self.donor, slot_id=self.slot.id)
        self.url = f'/api/v1/appointments/{self.appt["id"]}/cancel'

    def post(self, payload=None, client=None, token=None, csrf=True):
        extra = {'HTTP_X_CSRFTOKEN': token or self.token} if csrf else {}
        return (client or self.client).post(self.url, payload if payload is not None else {},
                                            content_type='application/json', **extra)

    def test_cancel_is_200_and_repeat_is_still_200(self):
        for _ in range(2):
            r = self.post()
            self.assertEqual((r.status_code, r.json()['status']), (200, 'CANCELLED'))

    def test_csrf_unknown_fields_and_other_roles(self):
        self.assertEqual(self.post(csrf=False).status_code, 403)
        self.assertEqual(self.post({'status': 'CONFIRMED'}).status_code, 400)
        staff, token = client_for(make_user('STAFF'))
        self.assertEqual(self.post(client=staff, token=token).status_code, 403)
        other, other_token = client_for(make_donor()[0])
        self.assertEqual(self.post(client=other, token=other_token).status_code, 404)
        self.assertEqual(Appointment.objects.get().status, 'CONFIRMED')

    def test_started_slot_is_a_422_invalid_transition(self):
        from scheduling.models import Slot
        Slot.objects.update(starts_at=timezone.now() - timedelta(hours=1),
                            ends_at=timezone.now() + timedelta(hours=1))
        r = self.post()
        self.assertEqual((r.status_code, r.json()['error']['code']), (422, 'invalid_transition'))
