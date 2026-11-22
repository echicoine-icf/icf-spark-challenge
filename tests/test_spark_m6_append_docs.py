import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import spark_m6_append_docs as mod

class DocsTests(unittest.TestCase):
    def test_preserves_and_idempotently_appends(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for target, source in mod.FILES.items():
                (root/target).write_text('ORIGINAL CONTENT\n',encoding='utf-8')
                (root/source).write_text('M6 content\n',encoding='utf-8')
            with patch.object(mod,'ROOT',root):
                mod.main();mod.main()
            for target in mod.FILES:
                result=(root/target).read_text(encoding='utf-8')
                self.assertTrue(result.startswith('ORIGINAL CONTENT'))
                self.assertEqual(result.count(mod.MARKER),1)

if __name__=='__main__':unittest.main()
