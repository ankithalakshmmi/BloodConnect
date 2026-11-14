"""Reader compatibility: configuration boundary tests, plus optional real PR40 DB test."""
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import Mock, patch
from uuid import uuid4
from django.apps import apps
from django.test import SimpleTestCase, TestCase
from django.contrib.auth import get_user_model
from donors.models import DonorProfile
from donors.eligibility_reader import read_saved_results
from core.errors import DecisionRequired

class RuleVersionCompatibilityTests(SimpleTestCase):
    def read(self, rules, saved_version='demo-2026-10-v1'):
        profile_id = uuid4()
        row = SimpleNamespace(pk=uuid4(), donor_id=profile_id, status='ELIGIBLE',
            reason_codes=[], reasons=[], assessed_at=None, rules_version=saved_version,
            confirmed_collection_date=None)
        model = Mock()
        model.objects.filter.return_value.order_by.return_value = [row]
        profiles = Mock()  # S2-10: the reader also reads the profile projection (none here)
        profiles.objects.filter.return_value.values_list.return_value = []
        with patch('donors.eligibility_reader.apps.get_model', return_value=model), patch(
                'donors.eligibility_reader.DonorProfile', profiles), patch(
                'donors.eligibility_reader.approved_screening_rules', return_value=rules):
            return read_saved_results(donor_ids=[profile_id])[str(profile_id)]
    def test_pr40_rules_version_key(self):
        self.assertFalse(self.read({'rules_version':'demo-2026-10-v1'})['is_stale'])
    def test_old_workpack_version_key(self):
        self.assertFalse(self.read({'version':'demo-2026-10-v1'})['is_stale'])
    def test_changed_version_is_stale(self):
        self.assertTrue(self.read({'rules_version':'new-version'})['is_stale'])
    def test_matching_aliases_are_allowed(self):
        self.assertFalse(self.read({'rules_version':'demo-2026-10-v1', 'version':'demo-2026-10-v1'})['is_stale'])
    def test_conflicting_aliases_rejected(self):
        with self.assertRaises(DecisionRequired): self.read({'rules_version':'one','version':'two'})
    def test_missing_blank_and_invalid_versions_rejected(self):
        for rules in [None, {}, {'rules_version':''}, {'rules_version':' '}, {'rules_version':None}, {'rules_version':123}]:
            with self.subTest(rules=rules), self.assertRaises(DecisionRequired): self.read(rules)

try:
    SavedResult = apps.get_model('donors', 'EligibilityResult')
except LookupError:
    SavedResult = None

@skipUnless(SavedResult is not None and any(f.name == 'age_years' for f in SavedResult._meta.fields),
            'Actual PR40 screening model is not integrated; not a real integration pass.')
class ActualPR40ReaderTests(TestCase):
    def test_actual_persisted_result_uses_real_pr40_configuration(self):
        from donors.configuration import approved_screening_rules
        actor = get_user_model().objects.create_user(email='reader-pr40@example.test',password='Synthetic-Password-123',role='DONOR')
        profile = DonorProfile.objects.create(user=actor,full_name='Synthetic Donor',mobile='+919876543210',blood_group='O+',location='Bengaluru')
        rules = approved_screening_rules()
        row = SavedResult.objects.create(donor=profile,age_years=25,weight_kg='60.00',status='ELIGIBLE',reason_codes=[],reasons=[],rules_version=rules['rules_version'])
        result = read_saved_results(donor_ids=[profile.pk])[str(profile.pk)]
        self.assertEqual(result['id'],str(row.pk))
        self.assertFalse(result['is_stale'])
        self.assertNotIn('age_years',result)
        row.rules_version='older-version';row.save(update_fields=['rules_version'])
        self.assertTrue(read_saved_results(donor_ids=[profile.pk])[str(profile.pk)]['is_stale'])
