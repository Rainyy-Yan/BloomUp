"""Only fabricated records are used; no competition attachments required."""

import contextlib
import io
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from challenge.__main__ import main
from challenge.pipeline import ROOT, prepare
from tools import error_propagation as legacy
from tools.label_noise import assumed_neighbour_kernel


class LegacyCompatibilityTests(unittest.TestCase):
    def test_seeded_legacy_scenario_preserves_original_result(self):
        records={'term':{'a':[1,4,2],'b':[3,6],'c':[2,2,5],'d':[6,5]}}
        turns={'term':{'a':{'c1':[(1,1),(2,4)]},'b':{'c2':[(1,3),(2,6)]},
                       'c':{'c3':[(1,5),(2,2)]},'d':{'c4':[(1,6),(2,5)]}}}
        # Reference generated from the pre-integration algorithm on this fabricated fixture.
        expected=(4.666666666666667,70.83333333333333,.55,1.0)
        actual=legacy.scenario(records,turns,'term',random.Random(2045))
        for before,after in zip(expected,actual): self.assertAlmostEqual(before,after)
        self.assertEqual(legacy.neighbour_kernel([1,2,6]),assumed_neighbour_kernel([1,2,6],.2))

    def test_unconfigured_source_does_not_create_identity_or_read_cwd(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ,{},clear=True):
                with self.assertRaisesRegex(ValueError,'no source directory is assumed'):
                    prepare(root,ROOT/'configs/preparation.json')
            self.assertFalse((Path(root)/'.local').exists())

    def test_repository_ignores_private_workflow_inputs(self):
        paths=['.local/pseudonym.key','local_state/artifacts/labels/example.json','runs/private/turns.jsonl',
               'configs/private.local.json','data/ground_truth.csv','.env.production','tools/apply_annotations.py',
               'docs/real_student_report.md','inference/ledger.json','exports/private/result.csv']
        # Source archives have no .git. Verify the shipped rules in an isolated repository.
        with tempfile.TemporaryDirectory() as root:
            (Path(root)/'.gitignore').write_bytes((ROOT/'.gitignore').read_bytes())
            subprocess.run(['git','init','--quiet'],cwd=root,capture_output=True,check=True)
            result=subprocess.run(['git','-c',f'core.excludesFile={os.devnull}','check-ignore','-z','--stdin'],
                                  input=('\0'.join(paths)+'\0').encode(),cwd=root,capture_output=True,check=False)
        self.assertEqual(result.returncode,0)
        self.assertEqual(set(result.stdout.decode().strip('\0').split('\0')),set(paths))

    def test_public_config_has_no_machine_source_path(self):
        config=json.loads((ROOT/'configs/preparation.json').read_text(encoding='utf-8'))
        self.assertEqual(config['source_root'],'')
        self.assertFalse(config['model']['remote_calls_enabled'])

    def test_cli_demo_includes_versioned_label_sensitivity_report(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()) as stream:
            code=main(['--root',root,'demo'])
            receipt=json.loads(stream.getvalue())
            self.assertEqual(code,0)
            self.assertEqual(receipt['status'],'completed')
            self.assertIn('label_sensitivity',receipt['result'])
            report=Path(receipt['result']['report']['markdown']).read_text(encoding='utf-8')
            self.assertIn('不是实测模型错误率',report)
            self.assertIn('假设扰动概率',report)
            self.assertNotIn('SYNTHETIC-0',report)


if __name__=='__main__': unittest.main()
