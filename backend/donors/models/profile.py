"""BB-06 DonorProfile. PROPOSED fields per docs/data-model.md and docs/api/openapi.yaml;
D-05 approval and Arnav's schema review pending."""
import uuid

from django.conf import settings
from django.db import models


class DonorProfile(models.Model):
    class BloodGroup(models.TextChoices):
        A_POS = 'A+', 'A+'
        A_NEG = 'A-', 'A-'
        B_POS = 'B+', 'B+'
        B_NEG = 'B-', 'B-'
        AB_POS = 'AB+', 'AB+'
        AB_NEG = 'AB-', 'AB-'
        O_POS = 'O+', 'O+'
        O_NEG = 'O-', 'O-'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='donor_profile')
    full_name = models.CharField(max_length=120)
    mobile = models.CharField(max_length=16, unique=True)  # PROPOSED E.164, e.g. +919876543210
    blood_group = models.CharField(max_length=3, choices=BloodGroup.choices)
    location = models.CharField(max_length=120)
    # Donors-owned projection (Arya, 14 Nov, S2-10): the latest CONFIRMED collection date.
    # Written only by donors.services.collections.record_confirmed_collection, which inventory
    # calls inside the same transaction as CollectionEvent and the first BloodUnit.
    last_confirmed_collection_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return str(self.id)  # avoid logging personal data