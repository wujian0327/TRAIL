import hashlib
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from four_figure_reference_report import ROOT, extra_suite_plan, verify_source_equivalence


class ParticipationExtensionTests(unittest.TestCase):
    def test_multiple_extensions_must_cover_every_seed_exactly_once(self):
        groups = {'first': [202, 203], 'second': [204, 205]}
        self.assertEqual(extra_suite_plan(None, groups, [202, 203, 204, 205]), groups)
        for bad in [{'first': [202, 203]}, {'first': [202, 203], 'second': [203, 204, 205]}]:
            with self.assertRaises(AssertionError):
                extra_suite_plan(None, bad, [202, 203, 204, 205])

    def test_original_single_extension_and_two_seed_modes_remain_supported(self):
        self.assertEqual(extra_suite_plan('original', None, [202, 203]), {'original': [202, 203]})
        self.assertEqual(extra_suite_plan(None, None, []), {})

    def test_equivalence_rejects_changed_source_and_wrong_binary(self):
        historical = ROOT/'results/backups/restore-yesterday-20261113-183832/experiment_results/diagnostics/reproducibility_v3'
        if not (historical/'baseline.json').exists():
            self.skipTest('Local historical experiment archive is unavailable.')
        baseline = json.loads((historical/'baseline.json').read_text())
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            snapshot = directory/'source'
            archive = historical/'baseline_source.tar.gz'
            # The successful fixture is the archived source, independently of
            # later opt-in simulator features in the current working tree.
            with tarfile.open(archive) as archived:
                for name in baseline['source_sha256']:
                    target = snapshot/name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archived.extractfile(name).read())
            proof = dict(binary_sha256=baseline['binary_sha256'], binary=str(historical/'trail_before'),
                         historical_source_archive=str(archive),
                         historical_source_archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                         current_source_snapshot=str(snapshot), source_sha256=baseline['source_sha256'])
            path = directory/'proof.json'
            path.write_text(json.dumps(proof))
            self.assertEqual(verify_source_equivalence(path, baseline['binary_sha256'])['matching_source_files'], 26)
            with self.assertRaises(AssertionError):
                verify_source_equivalence(path, 'wrong-binary')
            with (snapshot/'src/main.rs').open('a') as stream:
                stream.write('\n// changed source\n')
            with self.assertRaises(AssertionError):
                verify_source_equivalence(path, baseline['binary_sha256'])


if __name__ == '__main__':
    unittest.main()
