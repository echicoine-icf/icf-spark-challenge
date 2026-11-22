"""5H.9.2 command-line smoke: no manifest or DB necessary for --help."""
import subprocess
import sys
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]

class CliTests(unittest.TestCase):
    def test_command_help(self):
        result=subprocess.run([sys.executable,str(ROOT/'sparkctl.py'),
                 'annotation-source-evidence-check','--help'],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)
        for required in ('--context-report','--role-run','--output'):
            self.assertIn(required,result.stdout)

    def test_standalone_help(self):
        result=subprocess.run([sys.executable,str(ROOT/'spark_source_evidence_5h92.py'),
                 '--help'],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertIn('--batch',result.stdout)

if __name__=='__main__':unittest.main()
