"""BB-07, Ankitha. screen_donor: validate, authorize, evaluate, save a new result.

Demonstration rules only; this is NOT a statement that anyone is medically fit to donate.
Every screening saves a NEW EligibilityResult. Earlier assessments are kept.
"""
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from accounts.permissions import may_view_donor_health
from donors.configuration import approved_screening_rules
from donors.models import DonorProfile, EligibilityResult
from donors.services.evaluation import evaluate

MAX_AGE_YEARS = 150  # input sanity bound only, not an eligibility rule


class ScreeningAccessDenied(Exception):
    """The actor is neither the owning donor nor authorized clinical staff.

    The API view records one audit event and answers 404, like the BB-12 profile endpoint.
    """


def is_stale(result, rules) -> bool:
    """A result assessed under a different rules_version than the current one is stale."""
    return result.rules_version != rules['rules_version']


def is_history_stale(*, result_confirmed_date, profile_confirmed_date) -> bool:
    """True when a collection was confirmed after the assessment saw the donor's history.

    Compares dates only: the profile has a confirmed date and the result recorded none, or an
    earlier one. A history-stale result must not be trusted even if ELIGIBLE and rules-current.
    """
    if profile_confirmed_date is None:
        return False
    return result_confirmed_date is None or result_confirmed_date < profile_confirmed_date


def _validated_answers(answers, today):
    """Return (age_years, weight_kg, last_donation_date) or raise ValidationError."""
    if not isinstance(answers, dict):
        raise ValidationError({'non_field_errors': ['A JSON object is required.']})
    age = answers.get('age_years')
    weight = answers.get('weight_kg')
    last = answers.get('last_donation_date')
    errors = {}

    if isinstance(age, bool) or not isinstance(age, int) or age < 0 or age > MAX_AGE_YEARS:
        errors['age_years'] = ['Enter a whole number of years.']

    weight_value = None
    if not isinstance(weight, str):
        errors['weight_kg'] = ['Enter a valid weight.']
    else:
        try:
            weight_value = Decimal(weight)
        except ArithmeticError:
            errors['weight_kg'] = ['Enter a valid weight.']
        else:
            if not weight_value.is_finite() or weight_value <= 0 or weight_value >= 1000:
                errors['weight_kg'] = ['Enter a valid weight.']

    last_value = None
    if last is not None:
        if not isinstance(last, date):
            errors['last_donation_date'] = ['Enter a valid date.']
        elif last > today:
            errors['last_donation_date'] = ['The last donation date cannot be in the future.']
        else:
            last_value = last

    if errors:
        raise ValidationError(errors)
    return age, weight_value, last_value


def screen_donor(*, actor, donor_id: UUID, answers: dict):
    """Return a newly persisted EligibilityResult.

    Raises: NotFound (unknown donor profile), ScreeningAccessDenied (not own donor or
    authorized staff), DecisionRequired (rules file missing or invalid), ValidationError
    (bad input, nothing saved).
    """
    rules = approved_screening_rules()  # DecisionRequired propagates; no defaults.

    try:
        profile = DonorProfile.objects.get(pk=donor_id)
    except DonorProfile.DoesNotExist:
        raise NotFound() from None
    # donor_id is the donor PROFILE id; ownership is profile.user_id vs the account id.
    if not may_view_donor_health(user=actor, donor_user_id=profile.user_id):
        raise ScreeningAccessDenied()

    today = timezone.localdate()
    age, weight, last = _validated_answers(answers, today)

    with transaction.atomic():
        # Lock the profile so a collection confirmed at the same moment is either seen here or
        # makes this result history-stale afterwards (never silently missed).
        confirmed = (DonorProfile.objects.select_for_update()
                     .values_list('last_confirmed_collection_date', flat=True).get(pk=profile.pk))
        # S2-10: the later of the self-reported and the confirmed date, with the configured gap.
        effective_last = max((d for d in (last, confirmed) if d is not None), default=None)
        status, codes, reasons = evaluate(age_years=age, weight_kg=weight,
                                          last_donation_date=effective_last,
                                          today=today, rules=rules)
        return EligibilityResult.objects.create(
            donor=profile, age_years=age, weight_kg=weight, last_donation_date=last,
            confirmed_collection_date=confirmed,
            status=status, reason_codes=codes, reasons=reasons,
            rules_version=rules['rules_version'])