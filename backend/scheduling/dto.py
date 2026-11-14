"""JSON-ready camp projection shared by services and selectors."""


def camp_dto(camp) -> dict:
    return {
        'id': str(camp.id),
        'name': camp.name,
        'location': camp.location,
        'starts_at': camp.starts_at.isoformat(),
        'ends_at': camp.ends_at.isoformat(),
        'capacity': camp.capacity,
        'status': camp.status,
    }


def slot_dto(slot, confirmed_count: int) -> dict:
    return {
        'id': str(slot.id),
        'camp_id': str(slot.camp_id) if slot.camp_id else None,
        'location': slot.location,
        'starts_at': slot.starts_at.isoformat(),
        'ends_at': slot.ends_at.isoformat(),
        'capacity': slot.capacity,
        'remaining_capacity': max(slot.capacity - confirmed_count, 0),
        'status': slot.status,
    }


def appointment_dto(appointment) -> dict:
    # reminder_state (contract field) is intentionally omitted until the real notification
    # provider (enqueue_intent) exists; a fake PENDING would claim a reminder is queued.
    return {
        'id': str(appointment.id),
        'donor_id': str(appointment.donor_id),
        'slot_id': str(appointment.slot_id),
        'scheduled_time': appointment.slot.starts_at.isoformat(),
        'status': appointment.status,
        'cancelled_at': appointment.cancelled_at.isoformat() if appointment.cancelled_at else None,
    }
