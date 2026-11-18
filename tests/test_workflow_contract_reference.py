"""Reference checks for the proposed fashion-planner-workflow contract package.

Runs the P08 reference validator over committed synthetic fixtures, semantic
mutation probes, baseline consistency, and the check that no shared schema copies
are assumed in producer repositories.
Offline: no network, no services. Requires the `workflow` extra (jsonschema).
"""
import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools" / "workflow_contract"
SCRIPTS = ROOT / "scripts"

try:
    import jsonschema  # noqa: F401
    HAVE_JSONSCHEMA = True
except ImportError:  # pragma: no cover - environment dependent
    HAVE_JSONSCHEMA = False


def _load(directory, name):
    sys.path.insert(0, str(directory))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(directory))


@unittest.skipUnless(HAVE_JSONSCHEMA, "jsonschema not installed (install the `workflow` extra)")
class WorkflowContractReferenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validate = _load(TOOLS, "validate_contracts")
        cls.probes = _load(TOOLS, "probe_semantic_gaps")
        cls.freeze = _load(SCRIPTS, "freeze_workflow_contract")

    def test_every_indexed_fixture_meets_its_expected_outcome(self):
        summary = self.validate.collect()
        failed = [r["case"] for r in summary["results"] if not r["pass"]]
        self.assertEqual(failed, [])
        self.assertGreaterEqual(summary["fixtures"], 4)
        self.assertEqual(summary["schemas_checked"], 9)

    def test_semantic_mutations_are_all_rejected(self):
        result = self.probes.collect()
        self.assertEqual([r["case"] for r in result["results"] if r["accepted"]], [])
        self.assertEqual(result["rejected"], result["cases"])

    def test_baseline_matches_snapshot_and_schema_bytes(self):
        snapshot, snapshot_sha, file_errors = self.freeze.snapshot_state()
        self.assertEqual(file_errors, [])
        baseline = self.freeze.read_json(self.freeze.BASELINE)
        self.assertEqual(self.freeze.check_baseline(baseline, snapshot, snapshot_sha, file_errors), [])
        self.assertNotIn("state", snapshot, "snapshot.json is digested into checkpoint keys; keep state in baseline.json")

    def test_no_shared_p08_schema_copies_are_assumed_in_producer_repositories(self):
        missing = [repo for repo, rel in self.freeze.VENDORED if not (ROOT.parent / repo / rel).is_dir()]
        if missing:
            self.skipTest(f"sibling checkouts not present: {', '.join(missing)}")
        snapshot, _, _ = self.freeze.snapshot_state()
        report, problems = self.freeze.vendored_check(snapshot)
        self.assertEqual(problems, [], report)


if __name__ == "__main__":
    unittest.main()
