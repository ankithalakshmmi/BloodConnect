"""BB-07 EligibilityResult. Demonstration rules only, not a statement of medical fitness.

Insert-only by convention: every screening saves a new row, earlier assessments are kept,
and the latest row is the current result. Deleting the donor profile (or its account)
deletes its results (approved for this synthetic-data project; audit records are separate).
"""
import uuid

from django.db import models

from .profile import DonorProfile


class EligibilityResult(models.Model):
    class Status(models.TextChoices):
        ELIGIBLE = 'ELIGIBLE', 'Eligible'
        INELIGIBLE = 'INELIGIBLE', 'Ineligible'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    donor = models.ForeignKey(
        DonorProfile, on_delete=models.CASCADE, related_name='eligibility_results')
    # Assessed answers needed to apply the approved rules.
    age_years = models.PositiveSmallIntegerField()
    weight_kg = models.DecimalField(max_digits=5, decimal_places=2)
    last_donation_date = models.DateField(null=True, blank=True)  # self-reported answer
    # The confirmed collection date (DonorProfile projection) used for this assessment, or null.
    confirmed_collection_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices)
    reason_codes = models.JSONField(default=list)
    reasons = models.JSONField(default=list)
    assessed_at = models.DateTimeField(auto_now_add=True)
    rules_version = models.CharField(max_length=64)

    class Meta:
        indexes = [models.Index(fields=['donor', '-assessed_at'])]
        ordering = ['-assessed_at']

    def __str__(self):
        return str(self.id)  # avoid logging health answers