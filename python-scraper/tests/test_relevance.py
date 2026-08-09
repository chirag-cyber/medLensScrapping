"""
test_relevance.py — Regression guard for search-result relevance scoring.

search_engine.compute_relevance_score decides which scraped result is kept per
platform (pick_best_per_platform drops anything below 0.2). It is a hand-tuned
heuristic — brand-word hits, exact-dose match, wrong-form penalties — so it is
exactly the kind of code a small tweak silently degrades. These tests pin the
behaviours the scoring comments promise.

Run offline:
    python -m unittest tests.test_relevance -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from search_engine import compute_relevance_score as score


class TestRelevanceScore(unittest.TestCase):

    def test_exact_brand_and_dose_scores_high(self):
        self.assertGreaterEqual(score("Dolo 650", "Dolo 650 Tablet"), 0.8)

    def test_missing_primary_brand_is_rejected(self):
        # Primary brand absent -> massive penalty, clamped to 0.
        self.assertEqual(score("Dolo 650", "Crocin 650 Tablet"), 0.0)

    def test_wrong_dose_scores_below_exact(self):
        exact = score("Dolo 650", "Dolo 650 Tablet")
        wrong = score("Dolo 650", "Dolo 500 Tablet")
        self.assertLess(wrong, exact)
        # Wrong dose falls under the 0.2 keep threshold used by pick_best.
        self.assertLess(wrong, 0.2)

    def test_injection_penalized_when_not_requested(self):
        tablet = score("Dolo 650", "Dolo 650 Tablet")
        injection = score("Dolo 650", "Dolo 650 Injection")
        self.assertLess(injection, tablet)

    def test_injection_allowed_when_requested(self):
        # If the query itself asks for an injection, no form penalty applies.
        self.assertGreaterEqual(score("Dolo 650 Injection", "Dolo 650 Injection"), 0.8)

    def test_score_is_clamped_to_unit_interval(self):
        # Never exceeds 1.0 nor drops below 0.0, whatever the inputs.
        hi = score("Foo Plus Oral 500", "Foo Plus Oral 500 Tablet")
        lo = score("Foo 500", "Bar 999 Injection")
        self.assertLessEqual(hi, 1.0)
        self.assertGreaterEqual(hi, 0.0)
        self.assertEqual(lo, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
