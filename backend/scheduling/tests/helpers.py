"""Synthetic fixtures for BB-21 tests (example.test identities only, no real data)."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from donors.configuration import approved_screening_rules
from donors.models import DonorProfile, EligibilityResult
from scheduling.models import Camp, Slot

PASSWORD = 'local-test-only-password'
COUNTER = {'n': 0}


def next_number():
    COUNTER['n'] += 1
    return COUNTER['n']


def make_user(role, **extra):
    return get_user_model().objects.create_user(
        email=f'{role.lower()}{next_number()}@example.test', password=PASSWORD, role=role, **extra)


def make_donor(eligible=True, rules_version=None):
    """Return (user, profile). With eligible=True the latest saved result is a current ELIGIBLE one."""
    number = next_number()
    user = get_user_model().objects.create_user(
        email=f'donor{number}@example.test', password=PASSWORD, role='DONOR')
    profile = DonorProfile.objects.create(
        user=user, full_name='Synthetic Donor', mobile=f'+9198000{number:05d}',
        blood_group='O+', location='Chennai')
    if eligible is not None:
        save_result(profile, 'ELIGIBLE' if eligible else 'INELIGIBLE', rules_version)
    return user, profile


def save_result(profile, status, rules_version=None):
    return EligibilityResult.objects.create(
        donor=profile, age_years=30, weight_kg=Decimal('60'), last_donation_date=None,
        status=status, reason_codes=[], reasons=[],
        rules_version=rules_version or approved_screening_rules()['rules_version'])


def make_camp(**overrides):
    start = timezone.now() + timedelta(days=10)
    data = dict(name='Test Camp', location='Chennai', starts_at=start,
                ends_at=start + timedelta(hours=8), capacity=10)
    data.update(overrides)
    return Camp.objects.create(**data)


def make_slot(camp=None, **overrides):
    start = camp.starts_at if camp else timezone.now() + timedelta(days=10)
    data = dict(camp=camp, location='Chennai', starts_at=start,
                ends_at=start + timedelta(hours=2), capacity=2)
    data.update(overrides)
    return Slot.objects.create(**data)


from contextlib import contextmanager  # noqa: E402
from unittest import mock  # noqa: E402


@contextmanager
def clock_after_lock(before, after):
    """The clock reads `before` until the slot lock is acquired, then `after`.

    Models a request that waited on the lock across the slot's start: a timestamp captured
    BEFORE the lock (the old behaviour) sees `before`; one read AFTER the lock sees `after`.
    """
    from scheduling.services import appointments as service
    state = {'locked': False}
    real_lock = service._lock_slot

    def lock(slot_id):
        result = real_lock(slot_id)
        state['locked'] = True
        return result

    with mock.patch.object(service, '_lock_slot', side_effect=lock), \
            mock.patch.object(service.timezone, 'now',
                              side_effect=lambda: after if state['locked'] else before):
        yield
