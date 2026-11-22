"""Minimal main CLI registration test without a database or model."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

class CliSmoke(unittest.TestCase):
    def test_both_cli_help_commands_registered(self):
        for name in ('annotation-source-context-audit','annotation-source-context-run'):
            result=subprocess.run([sys.executable,str(ROOT/'sparkctl.py'),name,'--help'],
                                  capture_output=True,text=True,timeout=10)
            self.assertEqual(0,result.returncode, result.stderr)
            self.assertIn(name,result.stdout)

    def test_standalone_audit_help(self):
        result=subprocess.run([sys.executable,str(ROOT/'spark_source_context_5h9.py'),
                               'audit','--help'],capture_output=True,text=True,timeout=10)
        self.assertEqual(0,result.returncode, result.stderr)
        self.assertIn('--variable',result.stdout)

if __name__=='__main__':unittest.main()
