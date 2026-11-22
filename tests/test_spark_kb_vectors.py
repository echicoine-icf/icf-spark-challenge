"""Local SapBERT and pgvector unit tests; no real service."""
from __future__ import annotations
import math
import unittest
from unittest.mock import patch
import spark_kb_vectors as v

class VectorTests(unittest.TestCase):
    def test_validated_embeddings_and_dimension(self):
        good=[1.0]+[0.0]*767
        with patch.object(v,'api_json',return_value={'embeddings':[good]}):
            self.assertEqual(len(v.vectors_for(['glucose'])[0]),768)
        with patch.object(v,'api_json',return_value={'embeddings':[[0.2]*767]}):
            with self.assertRaisesRegex(ValueError,'768'):
                v.vectors_for(['glucose'])
        with patch.object(v,'api_json',return_value={'embeddings':[[float('nan')]+[0.]*767]}):
            with self.assertRaisesRegex(ValueError,'numbers'):
                v.vectors_for(['glucose'])

    def test_pin_checks_local_running_service(self):
        with patch.object(v,'pinned_revision',return_value='expected-sha'),\
             patch.object(v,'api_json',return_value={'model_revision':'incorrect'}):
            with self.assertRaisesRegex(ValueError,'revision'):
                v.verified_revision()

    def test_index_requires_explicit_action(self):
        with patch.object(v.kb,'pg_sql') as sql:
            v.build_vector_index()
        self.assertIn('hnsw',sql.call_args.args[0].lower())

if __name__=='__main__':unittest.main()
