"""BB-20 interim role gate for camp actions. PENDING S2-04 approval.

INTENDED matrix (shared team context; wording NOT yet recorded in config/sprint2-decisions.json):
active STAFF and ADMIN create/update/cancel camps, DONOR/STAFF/ADMIN list. The older ADMIN-only
create/cancel proposal conflicts with SRS FR-021 and is not used. Only active, authenticated,
non-superuser accounts. Replace the body with core.sprint2_permissions once that shared helper is on main;
this is the only place scheduling checks camp roles.
"""
from rest_framework.exceptions import PermissionDenied

# PENDING: S2-04 wording not yet recorded. REQUESTER never manages camps; no superuser bypass.
CAMP_ACTION_ROLES = {
    'create': frozenset({'ADMIN', 'STAFF'}),
    'update': frozenset({'ADMIN', 'STAFF'}),
    'cancel': frozenset({'ADMIN', 'STAFF'}),
    'list': frozenset({'DONOR', 'STAFF', 'ADMIN'}),
    # BB-21 (S2-04 proposal: slots ADMIN or STAFF; booking DONOR only).
    'slot_create': frozenset({'ADMIN', 'STAFF'}),
    'slot_list': frozenset({'DONOR', 'STAFF', 'ADMIN'}),
    'book': frozenset({'DONOR'}),
    'own_appointments': frozenset({'DONOR'}),
    'cancel_own': frozenset({'DONOR'}),
}


def may_manage_camps(*, user, action) -> bool:
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if not getattr(user, 'is_active', False):
        return False
    if getattr(user, 'is_superuser', False):
        return False
    return getattr(user, 'role', None) in CAMP_ACTION_ROLES.get(action, frozenset())


def require_camp_role(*, user, action) -> None:
    if not may_manage_camps(user=user, action=action):
        raise PermissionDenied()
