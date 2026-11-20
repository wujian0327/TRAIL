from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from validate_540b_pilot import epoch_boundary_only


class ObservationRaceTests(unittest.TestCase):
    def fixture(self):
        return dict(beacon_after=dict(head_slot=103),
                    geth_trail_status=[dict(relay_epoch=13,dynamic_relay_epoch=True) for _ in range(8)],
                    formal_experiment=dict(spec=dict(nodes=8,slots_per_epoch=8),
                        acceptance=dict(checks=[dict(name='geth-relay-epoch-current',passed=False)]),
                        measurement_quality=dict(passed=True)))

    def test_only_synchronized_adjacent_boundary_can_be_classified(self):
        self.assertIsNotNone(epoch_boundary_only(self.fixture()))
        for bad_head in [95,101,102,104]:
            summary=self.fixture();summary['beacon_after']['head_slot']=bad_head
            self.assertIsNone(epoch_boundary_only(summary))

    def test_divergent_or_multiple_epoch_advances_are_not_excused(self):
        for epoch in [12,14]:
            summary=self.fixture();summary['geth_trail_status'][0]['relay_epoch']=epoch
            self.assertIsNone(epoch_boundary_only(summary))
        summary=self.fixture();summary['geth_trail_status'].pop()
        self.assertIsNone(epoch_boundary_only(summary))

    def test_other_acceptance_and_data_quality_failures_are_not_excused(self):
        summary=self.fixture();summary['formal_experiment']['acceptance']['checks'].append(dict(name='invalid-evidence',passed=False))
        self.assertIsNone(epoch_boundary_only(summary))
        summary=self.fixture();summary['formal_experiment']['measurement_quality']['passed']=False
        self.assertIsNone(epoch_boundary_only(summary))


if __name__=='__main__':unittest.main()
