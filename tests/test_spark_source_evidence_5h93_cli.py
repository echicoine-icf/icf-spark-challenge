"""CLI entrypoints are available without database connections."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]

class CLI(unittest.TestCase):
    def test_both_entrypoints(self):
        for command,flag in [('annotation-source-evidence-extended','--role-run'),
                             ('annotation-source-missing','--corpus-root'),
                             ('annotation-role-review-template','--output-a'),
                             ('annotation-role-review-score','--review-b')]:
            p=subprocess.run([sys.executable,str(ROOT/'sparkctl.py'),command,'--help'],
                capture_output=True,text=True)
            self.assertEqual(0,p.returncode,p.stderr)
            self.assertIn(flag,p.stdout)
        p=subprocess.run([sys.executable,str(ROOT/'spark_source_evidence_5h93.py'),
                          'role-score','--help'],capture_output=True,text=True)
        self.assertEqual(0,p.returncode,p.stderr)
        self.assertIn('--review-a',p.stdout)
