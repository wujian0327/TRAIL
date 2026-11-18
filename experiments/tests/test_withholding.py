import json
from pathlib import Path
import subprocess
import sys
import types
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'experiments'))
from run_experiments import expand_runs,command_for_run,load_yaml,seed_bundle
from trail_withholding_report import aggregate_period,interval,relative_change,canonical_background,structured_digest


class WithholdingTests(unittest.TestCase):
    def test_matrix_has_three_matched_probabilities_without_seed_changes(self):
        for label,n in [('pilot',18),('main',120)]:
            runs=expand_runs(load_yaml(ROOT/f'experiments/configs/trail_withholding_{label}.yaml'))
            self.assertEqual(len(runs),n)
            self.assertEqual(len({r['run_id'] for r in runs}),n)
            for r in runs:
                self.assertEqual(r['attack_mode'],'selective-withholding')
                self.assertEqual(r['transaction_fee'],.001)
                self.assertEqual(r['initial_liquid_balance'],100.)
                self.assertEqual(r['withholding_node_workers'],8)
                self.assertEqual(r['withholding_forwarding_cost'],1.4055695425812074e-7)
                for key,value in seed_bundle(r['seed_value']).items():self.assertEqual(r[key],value)
                cli=command_for_run('trail',r)
                self.assertEqual(float(cli[cli.index('--withholding-probability')+1]),r['withholding_probability'])
                self.assertNotIn('--coalition-path-selection',cli)

    def test_existing_configuration_expansion_and_commands_remain_identical(self):
        old=types.ModuleType('old_run_experiments')
        old.__file__=str(ROOT/'experiments/run_experiments.py')
        code=subprocess.run(['git','show','c08591b:experiments/run_experiments.py'],cwd=ROOT,
                            capture_output=True,text=True,check=True).stdout
        exec(compile(code,old.__file__,'exec'),old.__dict__)
        checked=0
        for path in sorted((ROOT/'experiments/configs').glob('*.yaml')):
            if path.name.startswith('trail_withholding_'):continue
            spec=load_yaml(path)
            if not isinstance(spec,dict) or 'experiments' not in spec:continue
            a=old.expand_runs(spec);b=expand_runs(spec)
            self.assertEqual(a,b,path.name)
            self.assertEqual([old.command_for_run('trail',r) for r in a],
                             [command_for_run('trail',r) for r in b],path.name)
            checked+=1
        self.assertGreater(checked,40)

    def test_confidence_intervals_are_paired_student_t(self):
        mean,half=interval([1.,2.,3.])
        self.assertEqual(mean,2.)
        self.assertAlmostEqual(half,4.302652729696142/(3**.5))
        self.assertEqual(interval([0.]*20),(0.,0.))
        with self.assertRaises(ValueError):interval([1.])

    def test_zero_contribution_baseline_is_undefined_not_zero_effect(self):
        self.assertIsNone(relative_change(0,0))
        self.assertIsNone(relative_change(1,0))
        self.assertEqual(relative_change(9,10),-9.999999999999998)

    def test_parallel_log_append_order_does_not_change_signed_background_inputs(self):
        a=[dict(id=1,epoch=0,at_ms=10,transaction=dict(hash='a',signature='sig-a')),
           dict(id=2,epoch=0,at_ms=20,transaction=dict(hash='b',signature='sig-b'))]
        self.assertEqual(canonical_background(a[::-1]),a)
        self.assertEqual(structured_digest(canonical_background(a)),structured_digest(canonical_background(a[::-1])))

    def test_background_comparison_still_detects_changed_fields_and_missing_records(self):
        a=[dict(id=1,epoch=0,at_ms=10,transaction=dict(hash='a',signature='sig-a')),
           dict(id=2,epoch=0,at_ms=20,transaction=dict(hash='b',signature='sig-b'))]
        for path,value in [('time',21),('signature','changed'),('epoch',1)]:
            b=json.loads(json.dumps(a))
            if path=='time':b[1]['at_ms']=value
            elif path=='signature':b[1]['transaction']['signature']=value
            else:b[1]['epoch']=value
            self.assertNotEqual(structured_digest(canonical_background(a)),structured_digest(canonical_background(b)))
        for invalid in [[a[1]],[a[0],a[0]],a+[a[1]]]:
            with self.assertRaises(ValueError):canonical_background(invalid)


if __name__=='__main__':unittest.main()
