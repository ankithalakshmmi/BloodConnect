"""BB08/09 read adapter; does NOT create or own Ankitha's BB07 model.
Only this file should need adjustment to her actual persistence schema.
Expected contract is documented in docs/yashwitha-completion.md.
"""
from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from core.errors import FeatureNotImplemented, DecisionRequired
from donors.configuration import approved_screening_rules
from donors.models import DonorProfile
from donors.services.screening import is_history_stale


def read_saved_results(*, donor_ids):
    """Return {str(profile_uuid): exact public latest-result dict}; absent key means no assessment.

    Missing persistence is NOT the same as an assessed population with no results.
    No runtime fixture or invented eligibility result is returned.
    """
    try:
        model = apps.get_model('donors', 'EligibilityResult')
    except LookupError:
        raise FeatureNotImplemented('Saved screening persistence is not integrated yet.') from None
    try:
        for name in ('donor', 'status', 'reason_codes', 'reasons', 'assessed_at', 'rules_version'):
            model._meta.get_field(name)
    except FieldDoesNotExist:
        raise DecisionRequired('The saved screening schema needs its reader adapter connected.') from None
    rules = approved_screening_rules()
    # PR40 publishes rules_version; retain compatibility with the earlier workpack.
    if not isinstance(rules, dict):
        raise DecisionRequired('An approved screening rule version is required.')
    version = rules.get('rules_version', rules.get('version'))
    if not isinstance(version, str) or not version.strip():
        raise DecisionRequired('An approved screening rule version is required.')
    if 'rules_version' in rules and 'version' in rules and rules['rules_version'] != rules['version']:
        raise DecisionRequired('Screening rule version keys disagree.')
    confirmed_dates = dict(DonorProfile.objects.filter(pk__in=donor_ids).values_list(
        'pk', 'last_confirmed_collection_date'))
    rows = model.objects.filter(donor_id__in=donor_ids).order_by('donor_id', '-assessed_at', '-pk')
    result = {}
    for row in rows:
        key = str(row.donor_id)
        if key not in result:
            result[key] = {
                'id': str(row.pk), 'donor_id': key, 'status': row.status,
                'reason_codes': row.reason_codes, 'reasons': row.reasons,
                'assessed_at': row.assessed_at, 'rules_version': row.rules_version,
                'is_stale': row.rules_version != version,
                # S2-10 additive keys: callers that need "may this assessment be trusted now"
                # (booking) reject is_stale OR is_history_stale. The public result API exposes
                # only its declared fields, so its shape is unchanged.
                'confirmed_collection_date': row.confirmed_collection_date,
                'is_history_stale': is_history_stale(
                    result_confirmed_date=row.confirmed_collection_date,
                    profile_confirmed_date=confirmed_dates.get(row.donor_id)),
            }
    return result
