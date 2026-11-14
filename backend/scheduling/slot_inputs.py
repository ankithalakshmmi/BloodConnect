"""BB-21 strict input contracts: SlotCreate, slot list query, AppointmentCreate, list query.

Same rules as the camp inputs: unknown fields rejected, aware ISO date-times only (converted to
UTC), no business maximum for capacity beyond the integer column bound.
"""
from django.utils.dateparse import parse_datetime  # noqa: F401  (kept for parity with camps)
from rest_framework import serializers

from core.validation import (QueryIntegerField, StrictChoiceField, StrictInputSerializer,
                             StrictIntegerField, StrictStringField)
from scheduling.camp_inputs import MAX_DB_INTEGER, AwareDateTimeField


class StrictUUIDField(serializers.UUIDField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail('invalid')
        return super().to_internal_value(data)


class SlotCreateInputSerializer(StrictInputSerializer):
    camp_id = StrictUUIDField(allow_null=True)  # required; null means a walk-in slot
    location = StrictStringField(min_length=1, max_length=120)
    starts_at = AwareDateTimeField()
    ends_at = AwareDateTimeField()
    capacity = StrictIntegerField(min_value=1, max_value=MAX_DB_INTEGER)

    def validate(self, attrs):
        if attrs['starts_at'] >= attrs['ends_at']:
            raise serializers.ValidationError({'ends_at': ['End must be after start.']})
        return attrs


class SlotListInputSerializer(StrictInputSerializer):
    camp_id = StrictUUIDField(required=False)
    kind = StrictChoiceField(choices=('CAMP', 'WALK_IN'), required=False)
    # 'from' is a Python keyword, so these two are declared through the class namespace.
    locals()['from'] = AwareDateTimeField(required=False)
    locals()['to'] = AwareDateTimeField(required=False)
    page = QueryIntegerField(min_value=1, default=1)
    page_size = QueryIntegerField(min_value=1, max_value=100, default=20)

    def validate(self, attrs):
        if 'from' in attrs and 'to' in attrs and attrs['from'] > attrs['to']:
            raise serializers.ValidationError({'to': ['To must not be before from.']})
        return attrs


class AppointmentCreateInputSerializer(StrictInputSerializer):
    slot_id = StrictUUIDField()


class AppointmentListInputSerializer(StrictInputSerializer):
    page = QueryIntegerField(min_value=1, default=1)
    page_size = QueryIntegerField(min_value=1, max_value=100, default=20)


class AppointmentCancelInputSerializer(StrictInputSerializer):
    """Cancel takes no body fields; anything sent is rejected."""
