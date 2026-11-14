"""S2-10 (Arya, 14 Nov): donors owns the last_confirmed_collection_date projection.

Inventory calls record_confirmed_collection inside the SAME transaction that writes the
CollectionEvent and the first BloodUnit, so the projection and the collection commit or roll
back together. Ordinary screening reads the projection; older assessments become
history-stale (see donors.services.screening.is_history_stale).
"""
from datetime import date, datetime
from uuid import UUID

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connection, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from donors.models import DonorProfile


class TransactionRequired(RuntimeError):
    """Called outside the caller's transaction.atomic(); the projection must not commit alone."""


def record_confirmed_collection(*, donor_id: UUID, collected_on: date, today: date | None = None) -> date:
    """Advance the donor's confirmed collection date; return the date now stored.

    Never moves the date backwards (a late or repeated call is harmless). Locks the profile row,
    so it serialises with booking and screening for the same donor. Raises TransactionRequired
    (not in an atomic block), ValidationError (not a date / in the future), NotFound (no profile).
    """
    if not connection.in_atomic_block:
        raise TransactionRequired('record_confirmed_collection must run inside the caller\'s transaction.')
    if isinstance(collected_on, datetime) or not isinstance(collected_on, date):
        raise ValidationError({'collected_on': ['Enter a valid date.']})
    if collected_on > (today or timezone.localdate()):
        raise ValidationError({'collected_on': ['A collection date cannot be in the future.']})
    try:
        with transaction.atomic():  # savepoint inside the caller's transaction
            profile = DonorProfile.objects.select_for_update().get(pk=donor_id)
            current = profile.last_confirmed_collection_date
            if current is None or collected_on > current:
                profile.last_confirmed_collection_date = collected_on
                profile.save(update_fields=['last_confirmed_collection_date', 'updated_at'])
            return profile.last_confirmed_collection_date
    except (DonorProfile.DoesNotExist, DjangoValidationError, ValueError):
        raise NotFound() from None
