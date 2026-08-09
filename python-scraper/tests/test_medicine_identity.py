"""
test_medicine_identity.py — Regression guard for the identity/dedup logic.

medicine_identity.py is pure (no DB, no network), yet it encodes the subtle
rules that decide whether two scraped records are the SAME product. A careless
tweak there silently causes false merges (two different drugs collapsed) or
false splits (one drug scattered across duplicates). These tests pin the exact
invariants the module's own docstrings promise.

Run offline:
    python -m unittest tests.test_medicine_identity -v
    # or, from the tests/ dir:  python -m unittest test_medicine_identity -v
"""

import os
import sys
import unittest

# Make the parent package importable whether run from repo root or tests/.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from medicine_identity import (
    parse_identity,
    is_same_medicine,
    _normalize_salt_key,
    _salt_is_molecular,
)


def same(name_a, name_b, salt_a="", salt_b=""):
    return is_same_medicine(
        parse_identity(name_a, salt_a),
        parse_identity(name_b, salt_b),
    )


class TestSameProductMerges(unittest.TestCase):
    """Surface differences that must NOT split one product."""

    def test_case_form_packsize_variants_merge(self):
        # Docstring example: case, "Tablets" vs "Tablet", "10s" pack suffix.
        self.assertTrue(same("BRUCARE 600MG TABLETS", "Brucare 600mg Tablet 10s"))

    def test_unit_present_vs_absent_merge(self):
        # "600" ~ "600mg" — a bare dose equals the same dose with a unit.
        self.assertTrue(same("Dolo 650", "Dolo 650mg Tablet"))

    def test_dose_reordered_around_brand_merge(self):
        # brand compared as an unordered token set.
        self.assertTrue(same("Crocin 240 DS Syrup", "Crocin DS 240 Syrup"))


class TestDifferentProductsStaySeparate(unittest.TestCase):
    """Genuinely different products that must NOT merge."""

    def test_different_form_splits(self):
        self.assertFalse(same("Brucare 600mg Tablet", "Brucare 600 Syrup"))

    def test_different_brand_splits(self):
        self.assertFalse(same("Brucare 600mg Tablet", "Mucare 600mg Tablet"))

    def test_formulation_modifier_is_not_noise(self):
        # "Ibugesic Plus" (ibuprofen+paracetamol) != "Ibugesic" (ibuprofen alone).
        self.assertFalse(same("Ibugesic Plus Tablet", "Ibugesic Tablet"))

    def test_trailing_n_suffix_kept(self):
        # Betnesol-N (adds neomycin) is a different product from Betnesol.
        self.assertFalse(same("Betnesol Tablet", "Betnesol-N Tablet"))

    def test_same_number_different_unit_splits(self):
        # 600mg must not equal 600mcg.
        self.assertFalse(same("Foo 600mg Tablet", "Foo 600mcg Tablet"))

    def test_different_dose_splits(self):
        self.assertFalse(same("Dolo 650 Tablet", "Dolo 500 Tablet"))


class TestBrandParsing(unittest.TestCase):
    """Guard the specific parsing bugs the docstrings call out."""

    def test_tenovate_m_unit_glue_does_not_pollute_brand(self):
        # "15gm" must match unit "gm", not "g" (which would leave a stray "m"
        # polluting the brand — the documented Tenovate-M false-merge bug).
        ident = parse_identity("Tenovate GN 15gm Cream")
        self.assertNotIn("m", ident.brand.split())
        self.assertEqual(ident.form, "cream")

    def test_volume_is_packaging_not_strength(self):
        # A 60ml bottle volume must not become the dose, so "...suspension 60ml"
        # and "...suspension" share an identity.
        self.assertTrue(same("Ceff 60ml Suspension", "Ceff Suspension"))


class TestSaltNormalization(unittest.TestCase):
    """_normalize_salt_key must fold surface noise to one key."""

    def test_synonym_folding(self):
        # Acetaminophen -> paracetamol; the pair collapses to one token.
        self.assertEqual(
            _normalize_salt_key("Paracetamol / Acetaminophen"), "paracetamol")

    def test_hyphen_glued_dose_stripped(self):
        self.assertEqual(_normalize_salt_key("CAFFEINE-32MG"), "caffeine")

    def test_combo_sorted_and_deduped(self):
        # Order-independent: same two molecules -> same key regardless of order.
        a = _normalize_salt_key("Amoxicillin 500mg + Clavulanic Acid 125mg")
        b = _normalize_salt_key("Clavulanic Acid + Amoxicillin")
        self.assertEqual(a, b)

    def test_prose_salt_flagged_non_molecular(self):
        prose = _normalize_salt_key(
            "Embeta XR 25 composition consists of metoprolol")
        self.assertFalse(_salt_is_molecular(prose))

    def test_real_salt_flagged_molecular(self):
        self.assertTrue(_salt_is_molecular(_normalize_salt_key("Ibuprofen 600mg")))


class TestSaltEcho(unittest.TestCase):
    """A molecule name leaking into one title but not the other is composition,
    not brand — merge only when the molecule is shared by BOTH salts."""

    def test_shared_molecule_echo_merges(self):
        self.assertTrue(same(
            "Crocin Baby Paracetamol Drops", "Crocin Baby Drops",
            salt_a="Paracetamol", salt_b="Paracetamol"))

    def test_different_molecules_do_not_merge(self):
        # Kriam Ambroxol vs Kriam Bromhexine — different drugs, must stay split.
        self.assertFalse(same(
            "Kriam Ambroxol Syrup", "Kriam Bromhexine Syrup",
            salt_a="Ambroxol", salt_b="Bromhexine"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
