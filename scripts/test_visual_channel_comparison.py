from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_repair_loop import _round_improved, _round_quality_regressed, visual_comparison_metrics


def metrics(before_status='completed', after_status='failed', cv_before=2, cv_after=2, vlm_before=5, vlm_after=0):
    return {
        'overfull_hbox_total_before': 2, 'overfull_hbox_total_after': 2,
        'defects_remaining_before': cv_before + vlm_before,
        'defects_remaining_after': cv_after + vlm_after,
        'visual_findings_before': cv_before + vlm_before,
        'visual_findings_after': cv_after + vlm_after,
        'vlm_status_before': before_status, 'vlm_status_after': after_status,
        'visual_cv_findings_before': cv_before, 'visual_cv_findings_after': cv_after,
        'visual_vlm_findings_before': vlm_before, 'visual_vlm_findings_after': vlm_after,
    }


class VisualChannelComparisonTest(unittest.TestCase):
    def test_vlm_disappearance_does_not_establish_improvement(self):
        value = metrics()
        self.assertFalse(_round_quality_regressed(value))
        self.assertFalse(_round_improved(value))

    def test_vlm_return_does_not_establish_regression(self):
        self.assertFalse(_round_quality_regressed(metrics('failed', 'completed', vlm_before=0, vlm_after=5)))

    def test_cv_regression_remains_visible_when_vlm_fails(self):
        self.assertTrue(_round_quality_regressed(metrics(cv_after=3)))

    def test_log_regression_remains_visible(self):
        value = metrics()
        value['overfull_hbox_total_after'] = 3
        self.assertTrue(_round_quality_regressed(value))

    def test_complete_vlm_regression_is_comparable(self):
        self.assertTrue(_round_quality_regressed(metrics('completed', 'completed', vlm_after=6)))

    def test_two_partial_runs_are_not_comparable(self):
        self.assertFalse(_round_quality_regressed(metrics('partial', 'partial', vlm_before=1, vlm_after=5)))

    def test_metric_snapshot_separates_vlm_and_cv(self):
        self.assertEqual(visual_comparison_metrics({'findings': [{'source': 'cv_detector'}, {'source': 'vlm_detector'}],
                                                     'vlm_diagnosis': {'status': 'partial'}}, 'before'),
                         {'visual_cv_findings_before': 1, 'visual_vlm_findings_before': 1, 'vlm_status_before': 'partial'})

    def test_legacy_metric_regression_still_works(self):
        self.assertTrue(_round_quality_regressed({'visual_findings_before': 1, 'visual_findings_after': 2}))

    def test_cv_only_observations_establish_progress_without_log_metrics(self):
        for after, expected in ((1, True), (2, False), (3, False)):
            with self.subTest(after=after):
                self.assertIs(_round_improved({
                    'visual_cv_findings_before': 2,
                    'visual_cv_findings_after': after,
                    'vlm_status_before': 'completed',
                    'vlm_status_after': 'failed',
                }), expected)

    def test_missing_or_one_sided_observations_remain_unknown(self):
        self.assertIsNone(_round_improved({}))
        self.assertIsNone(_round_improved({'visual_cv_findings_before': 2}))
        self.assertIsNone(_round_improved({'visual_cv_findings_after': 1}))


if __name__ == '__main__':
    unittest.main()
