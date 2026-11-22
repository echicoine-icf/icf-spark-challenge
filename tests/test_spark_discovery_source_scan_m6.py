import tempfile
import unittest
from pathlib import Path
from spark_discovery_source_scan_m6 import scan

class SourceScanTests(unittest.TestCase):
    def test_only_locations_no_source_lines(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'backend'/'app').mkdir(parents=True)
            (root/'frontend'/'src').mkdir(parents=True)
            (root/'backend'/'app'/'routes.py').write_text('token_private = "secret"\n@router.post("/search/typed")\n',encoding='utf-8')
            (root/'frontend'/'src'/'search.tsx').write_text('fetch("/search/typed")',encoding='utf-8')
            result=scan(root)
            self.assertEqual(result['marker_count_by_category']['typed_route'],2)
            self.assertNotIn('secret',str(result))
            self.assertIn('backend/app/routes.py',[x['file'] for x in result['markers']['typed_route']])

    def test_no_roots_no_false_claims(self):
        with tempfile.TemporaryDirectory() as td:
            result=scan(Path(td))
            self.assertEqual(result['code_files_scanned'],0)
            self.assertEqual(result['scanned_roots'],[])

if __name__=='__main__':unittest.main()
