"""S2-10 donors side: confirmed-collection projection, screening, history-stale (real database).

Synthetic data only. Demonstration rules; not a statement of medical fitness.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from donors.eligibility_reader import read_saved_results
from donors.models import DonorProfile, EligibilityResult
from donors.services.collections import TransactionRequired, record_confirmed_collection
from donors.services.screening import is_history_stale, screen_donor

PASSWORD = 'local-test-only-password'
RULES = {'rules_version': 'test-v1', 'min_age_years': 18, 'max_age_years': 65,
         'min_weight_kg': Decimal('50'), 'min_days_between_donations': 90}
COUNTER = {'n': 0}


def make_donor():
    COUNTER['n'] += 1
    user = get_user_model().objects.create_user(
        email=f'donor{COUNTER["n"]}@example.test', password=PASSWORD)
    return user, DonorProfile.objects.create(
        user=user, full_name='Synthetic Donor', mobile=f'+9199000{COUNTER["n"]:05d}',
        blood_group='O+', location='Chennai')


def answers(**overrides):
    data = {'age_years': 30, 'weight_kg': '60', 'last_donation_date': None}
    data.update(overrides)
    return data


def today():
    return timezone.localdate()


class RecordConfirmedCollectionTests(TestCase):
    def setUp(self):
        self.user, self.profile = make_donor()

    def record(self, on, **kw):
        return record_confirmed_collection(donor_id=self.profile.pk, collected_on=on, **kw)

    def test_sets_the_projection_and_returns_it(self):
        on = today() - timedelta(days=3)
        self.assertEqual(self.record(on), on)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_confirmed_collection_date, on)

    def test_never_moves_backwards_and_repeat_is_harmless(self):
        newer, older = today() - timedelta(days=1), today() - timedelta(days=40)
        self.record(newer)
        self.assertEqual(self.record(older), newer)
        self.assertEqual(self.record(newer), newer)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_confirmed_collection_date, newer)

    def test_a_later_collection_advances_it(self):
        self.record(today() - timedelta(days=100))
        self.assertEqual(self.record(today()), today())

    def test_rejects_future_and_non_date_values(self):
        for bad in (today() + timedelta(days=1), datetime.now(), '2026-11-01', None):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(ValidationError):
                    self.record(bad)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.last_confirmed_collection_date)

    def test_unknown_donor_is_not_found(self):
        with self.assertRaises(NotFound):
            record_confirmed_collection(donor_id='00000000-0000-4000-8000-000000000009',
                                        collected_on=today())

    def test_rolls_back_with_the_callers_transaction(self):
        try:
            with transaction.atomic():
                self.record(today())
                raise RuntimeError('inventory failed after the projection was written')
        except RuntimeError:
            pass
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.last_confirmed_collection_date)


class RecordRequiresTransactionTests(TransactionTestCase):
    def test_outside_an_atomic_block_it_refuses(self):
        _, profile = make_donor()
        with self.assertRaises(TransactionRequired):
            record_confirmed_collection(donor_id=profile.pk, collected_on=today())
        profile.refresh_from_db()
        self.assertIsNone(profile.last_confirmed_collection_date)


@patch('donors.services.screening.approved_screening_rules', return_value=RULES)
class ScreeningUsesConfirmedDateTests(TestCase):
    def setUp(self):
        self.user, self.profile = make_donor()

    def screen(self, **overrides):
        return screen_donor(actor=self.user, donor_id=self.profile.pk, answers=answers(**overrides))

    def confirm(self, days_ago):
        with transaction.atomic():
            record_confirmed_collection(donor_id=self.profile.pk, collected_on=today() - timedelta(days=days_ago))

    def test_no_confirmed_date_behaves_as_before(self, _rules):
        result = self.screen()
        self.assertEqual((result.status, result.confirmed_collection_date), ('ELIGIBLE', None))

    def test_recent_confirmed_collection_makes_the_donor_ineligible(self, _rules):
        self.confirm(10)
        result = self.screen()  # nothing self-reported
        self.assertEqual(result.status, 'INELIGIBLE')
        self.assertIn('DONATION_TOO_RECENT', result.reason_codes)
        self.assertEqual(result.confirmed_collection_date, today() - timedelta(days=10))
        self.assertIsNone(result.last_donation_date)  # self-reported stays as answered

    def test_the_later_of_self_reported_and_confirmed_decides(self, _rules):
        self.confirm(200)  # old confirmed date
        recent_self = self.screen(last_donation_date=today() - timedelta(days=5))
        self.assertEqual(recent_self.status, 'INELIGIBLE')
        old_self = self.screen(last_donation_date=today() - timedelta(days=300))
        self.assertEqual(old_self.status, 'ELIGIBLE')  # confirmed 200 days ago is the later date

    def test_the_configured_gap_boundary_uses_the_confirmed_date(self, _rules):
        self.confirm(89)
        self.assertEqual(self.screen().status, 'INELIGIBLE')
        DonorProfile.objects.filter(pk=self.profile.pk).update(
            last_confirmed_collection_date=today() - timedelta(days=90))
        self.assertEqual(self.screen().status, 'ELIGIBLE')


class HistoryStaleTests(TestCase):
    def setUp(self):
        self.user, self.profile = make_donor()

    def result(self, confirmed=None, status='ELIGIBLE'):
        from donors.configuration import approved_screening_rules
        return EligibilityResult.objects.create(
            donor=self.profile, age_years=30, weight_kg=Decimal('60'), status=status,
            confirmed_collection_date=confirmed, reason_codes=[], reasons=[],
            rules_version=approved_screening_rules()['rules_version'])

    def saved(self):
        return read_saved_results(donor_ids=[self.profile.pk])[str(self.profile.pk)]

    def set_profile_date(self, days_ago):
        DonorProfile.objects.filter(pk=self.profile.pk).update(
            last_confirmed_collection_date=today() - timedelta(days=days_ago))

    def test_helper_rules(self):
        d1, d2 = date(2026, 10, 1), date(2026, 10, 2)
        self.assertFalse(is_history_stale(result_confirmed_date=None, profile_confirmed_date=None))
        self.assertFalse(is_history_stale(result_confirmed_date=d1, profile_confirmed_date=None))
        self.assertTrue(is_history_stale(result_confirmed_date=None, profile_confirmed_date=d1))
        self.assertTrue(is_history_stale(result_confirmed_date=d1, profile_confirmed_date=d2))
        self.assertFalse(is_history_stale(result_confirmed_date=d2, profile_confirmed_date=d2))
        self.assertFalse(is_history_stale(result_confirmed_date=d2, profile_confirmed_date=d1))

    def test_not_stale_before_any_collection(self):
        self.result()
        saved = self.saved()
        self.assertFalse(saved['is_history_stale'])
        self.assertIsNone(saved['confirmed_collection_date'])

    def test_becomes_stale_after_a_collection_even_if_eligible_and_rules_current(self):
        self.result()
        self.set_profile_date(1)
        saved = self.saved()
        self.assertEqual(saved['status'], 'ELIGIBLE')
        self.assertFalse(saved['is_stale'])  # rules version is still current
        self.assertTrue(saved['is_history_stale'])

    def test_a_new_screening_after_the_collection_clears_it(self):
        self.result()
        self.set_profile_date(120)  # old enough to be eligible again
        self.assertTrue(self.saved()['is_history_stale'])
        with patch('donors.services.screening.approved_screening_rules',
                   return_value={**RULES, 'rules_version': self.saved()['rules_version']}):
            screen_donor(actor=self.user, donor_id=self.profile.pk, answers=answers())
        saved = self.saved()
        self.assertFalse(saved['is_history_stale'])
        self.assertEqual(saved['confirmed_collection_date'], today() - timedelta(days=120))

    def test_a_later_collection_stales_the_newer_assessment_again(self):
        self.result(confirmed=today() - timedelta(days=120))
        self.set_profile_date(120)
        self.assertFalse(self.saved()['is_history_stale'])
        self.set_profile_date(2)
        self.assertTrue(self.saved()['is_history_stale'])

    def test_the_latest_result_is_judged_not_older_ones(self):
        self.result(confirmed=None)
        self.set_profile_date(5)
        newest = self.result(confirmed=today() - timedelta(days=5))
        saved = self.saved()
        self.assertEqual(saved['id'], str(newest.pk))
        self.assertFalse(saved['is_history_stale'])
