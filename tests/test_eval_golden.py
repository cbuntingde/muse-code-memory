"""Golden eval: recall/capture quality must stay perfect on the frozen set."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from muse_memory import evalset


class EvalGoldenTest(unittest.TestCase):
    def test_golden_metrics(self) -> None:
        report = evalset.run_eval()
        self.assertGreaterEqual(report["recall_top1"], 0.99, report)
        self.assertGreaterEqual(report["recall_at3"], 0.99, report)
        self.assertGreaterEqual(report["capture_precision"], 0.99, report)
        self.assertGreaterEqual(report["capture_recall"], 0.99, report)


if __name__ == "__main__":
    unittest.main()
