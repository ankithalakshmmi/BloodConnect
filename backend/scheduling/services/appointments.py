"""BB-21 booking. One atomic transaction; lock order camp -> slot -> bookings (walk-in: slot only).

Not wired yet (INTEGRATION_PENDING): the reminder intent. notification_adapter.services.intents
.enqueue_intent is not on main (Arya's PR #44), so no intent is written and nothing is claimed sent.
PENDING DECISION (Arya): a slot that has already started is currently treated as unavailable.
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound

from core.errors import DecisionRequired
from donors.eligibility_reader import read_saved_results
from donors.models import DonorProfile
from scheduling.dto import appointment_dto
from scheduling.exceptions import (AppointmentNotCancellable, CapacityConflict, DuplicateBooking, IneligibleDonor,
                                   PolicyUnresolved, SlotUnavailable)
from scheduling.models import Appointment, Camp, Slot
from scheduling.permissions import require_camp_role


def _lock_slot(slot_id):
    """Lock the camp (if any), then the slot. Returns (camp_or_None, slot)."""
    try:
        camp_id = Slot.objects.values_list('camp_id', flat=True).get(pk=slot_id)
    except (Slot.DoesNotExist, DjangoValidationError, ValueError):
        raise NotFound() from None
    camp = Camp.objects.select_for_update().get(pk=camp_id) if camp_id else None
    try:
        slot = Slot.objects.select_for_update().get(pk=slot_id)
    except Slot.DoesNotExist:
        raise NotFound() from None
    if slot.camp_id != camp_id:
        raise SlotUnavailable()  # the slot changed while we waited; the caller can retry
    return camp, slot


def _require_current_eligibility(profile) -> None:
    try:
        saved = read_saved_results(donor_ids=[profile.pk]).get(str(profile.pk))
    except DecisionRequired:
        raise PolicyUnresolved('Screening rules are not configured.') from None
    if saved is None or saved['status'] != 'ELIGIBLE' or saved['is_stale']:
        raise IneligibleDonor()


def book_appointment(*, actor, slot_id, now=None) -> dict:
    require_camp_role(user=actor, action='book')
    now = now or timezone.now()
    try:
        profile = DonorProfile.objects.get(user_id=actor.pk)
    except DonorProfile.DoesNotExist:
        raise IneligibleDonor('Complete donor registration and screening before booking.') from None

    with transaction.atomic():
        camp, slot = _lock_slot(slot_id)
        if slot.status != Slot.Status.OPEN or (camp is not None and camp.status != Camp.Status.ACTIVE):
            raise SlotUnavailable()
        if slot.starts_at <= now:
            raise SlotUnavailable()
        _require_current_eligibility(profile)
        confirmed = Appointment.objects.filter(slot=slot, status=Appointment.Status.CONFIRMED)
        if confirmed.filter(donor=profile).exists():
            raise DuplicateBooking()
        if confirmed.count() >= slot.capacity:
            raise CapacityConflict('This slot is full.')
        if camp is not None:
            camp_total = Appointment.objects.filter(
                slot__camp=camp, status=Appointment.Status.CONFIRMED).count()
            if camp_total >= camp.capacity:
                raise CapacityConflict('This camp is full.')
        try:
            with transaction.atomic():
                appointment = Appointment.objects.create(donor=profile, slot=slot)
        except IntegrityError:
            raise DuplicateBooking() from None
    appointment.slot = slot
    return appointment_dto(appointment)


def cancel_appointment(*, actor, appointment_id, now=None) -> dict:
    """A donor cancels their OWN confirmed appointment before its slot starts.

    Repeating the call is harmless: an already cancelled appointment is returned unchanged and
    capacity is never released twice (capacity is counted from CONFIRMED rows only). History is
    kept (status CANCELLED + cancelled_at). Someone else's appointment is indistinguishable from
    an unknown id (404). Lock order camp -> slot -> appointment, same as booking.

    INTEGRATION_PENDING: pending reminders must be invalidated in this same transaction. There is
    no reminder provider on main (enqueue_intent is only in PR #44), so nothing is invalidated
    yet and no reminder state is claimed.
    """
    require_camp_role(user=actor, action='cancel_own')
    now = now or timezone.now()
    try:
        profile = DonorProfile.objects.get(user_id=actor.pk)
        slot_id = Appointment.objects.values_list('slot_id', flat=True).get(
            pk=appointment_id, donor=profile)
    except (DonorProfile.DoesNotExist, Appointment.DoesNotExist, DjangoValidationError, ValueError):
        raise NotFound() from None

    with transaction.atomic():
        _camp, slot = _lock_slot(slot_id)
        try:
            appointment = Appointment.objects.select_for_update().get(pk=appointment_id, donor=profile)
        except Appointment.DoesNotExist:
            raise NotFound() from None
        if appointment.status != Appointment.Status.CANCELLED:
            if slot.starts_at <= now:
                raise AppointmentNotCancellable()
            appointment.status = Appointment.Status.CANCELLED
            appointment.cancelled_at = now
            appointment.save(update_fields=['status', 'cancelled_at', 'updated_at'])
    appointment.slot = slot
    return appointment_dto(appointment)
