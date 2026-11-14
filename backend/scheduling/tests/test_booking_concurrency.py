"""BB-21 race tests: separate database connections, real row locks. PostgreSQL only.

On SQLite (no row locks) these are skipped, so they only count as evidence from a PostgreSQL run.
"""
import threading
import unittest

from django.db import connection, connections
from django.test import TransactionTestCase
from rest_framework.exceptions import APIException

from scheduling.models import Appointment
from scheduling.services.appointments import book_appointment
from scheduling.tests.helpers import make_camp, make_donor, make_slot


def run_parallel(jobs):
    barrier = threading.Barrier(len(jobs))
    results = [None] * len(jobs)

    def worker(i, job):
        try:
            barrier.wait(timeout=10)
            results[i] = ('ok', job())
        except APIException as exc:
            results[i] = ('error', exc.default_code)
        except Exception as exc:  # surfaced in the assertion below
            results[i] = ('crash', repr(exc))
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i, j)) for i, j in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


@unittest.skipUnless(connection.vendor == 'postgresql', 'needs PostgreSQL row locking')
class BookingRaceTests(TransactionTestCase):
    def test_last_place_goes_to_exactly_one_donor(self):
        slot = make_slot(make_camp(capacity=10), capacity=1)
        donors = [make_donor()[0] for _ in range(6)]
        results = run_parallel([lambda d=d: book_appointment(actor=d, slot_id=slot.id) for d in donors])
        self.assertEqual([r[0] for r in results].count('ok'), 1, results)
        self.assertEqual([r for r in results if r[0] == 'crash'], [])
        self.assertEqual(Appointment.objects.filter(status='CONFIRMED').count(), 1)

    def test_camp_total_is_never_exceeded_across_slots(self):
        camp = make_camp(capacity=2)
        slots = [make_slot(camp, capacity=2) for _ in range(3)]
        donors = [make_donor()[0] for _ in range(6)]
        results = run_parallel([lambda d=d, s=slots[i % 3]: book_appointment(actor=d, slot_id=s.id)
                                for i, d in enumerate(donors)])
        self.assertEqual([r for r in results if r[0] == 'crash'], [])
        self.assertEqual(Appointment.objects.filter(status='CONFIRMED').count(), 2)

    def test_same_donor_double_click_books_once(self):
        slot = make_slot(make_camp(capacity=10), capacity=5)
        donor = make_donor()[0]
        results = run_parallel([lambda: book_appointment(actor=donor, slot_id=slot.id)] * 4)
        self.assertEqual([r[0] for r in results].count('ok'), 1, results)
        self.assertEqual([r for r in results if r[0] == 'crash'], [])
        self.assertEqual(Appointment.objects.count(), 1)


@unittest.skipUnless(connection.vendor == 'postgresql', 'needs PostgreSQL row locking')
class CancelRaceTests(TransactionTestCase):
    def test_double_cancel_and_a_booking_race_never_overbook(self):
        from scheduling.services.appointments import cancel_appointment
        slot = make_slot(make_camp(capacity=10), capacity=1)
        owner = make_donor()[0]
        appt = book_appointment(actor=owner, slot_id=slot.id)
        waiting = [make_donor()[0] for _ in range(4)]
        jobs = [lambda: cancel_appointment(actor=owner, appointment_id=appt['id'])] * 3
        jobs += [lambda d=d: book_appointment(actor=d, slot_id=slot.id) for d in waiting]
        results = run_parallel(jobs)
        self.assertEqual([r for r in results if r[0] == 'crash'], [])
        self.assertLessEqual(Appointment.objects.filter(status='CONFIRMED').count(), 1)
        self.assertEqual(Appointment.objects.get(pk=appt['id']).status, 'CANCELLED')
