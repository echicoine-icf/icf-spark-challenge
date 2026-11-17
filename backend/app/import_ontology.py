"""Import versioned OBO terminology into Postgres using COPY staging.

No HTTP is used during ontology import or search. Source file is preserved.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
import psycopg
from app.ontology_knowledge import normalize_term, parse_obo, sha256_file

MIGRATION = Path('/migrations/004_ontology_knowledge.sql')


def stage_rows(cur, ontology: str, release: str, source: Path):
    """Two sequential COPY streams, because a connection can COPY one at a time."""
    cur.execute("""CREATE TEMP TABLE ontology_concepts_in (
      ontology text, ontology_release text, concept_id text,
      label text, synonyms text, definition text, relationships text
    ) ON COMMIT DROP""")
    cur.execute("""CREATE TEMP TABLE ontology_terms_in (
      ontology text, ontology_release text, concept_id text,
      term_normalized text, term_text text, term_kind text
    ) ON COMMIT DROP""")
    concept_count = 0
    term_count = 0
    _, concept_iter = parse_obo(source, ontology)
    with cur.copy('COPY ontology_concepts_in FROM STDIN') as cc:
        for term in concept_iter:
            cc.write_row((ontology, release, term.concept_id, term.label,
                          json.dumps(term.synonyms), term.definition,
                          json.dumps([{'type': 'is_a', 'concept_id': p} for p in term.parents])))
            concept_count += 1
    _, term_iter = parse_obo(source, ontology)
    with cur.copy('COPY ontology_terms_in FROM STDIN') as tc:
        for term in term_iter:
            seen = set()
            for txt, kind in [(term.label, 'label'), *((s, 'exact_synonym') for s in term.synonyms)]:
                key = normalize_term(txt)
                if key and key not in seen:
                    tc.write_row((ontology, release, term.concept_id, key, txt, kind))
                    seen.add(key)
                    term_count += 1
    return concept_count, term_count


def merge(cur):
    cur.execute("""INSERT INTO ontology_concepts
        (ontology, ontology_release, concept_id, label, synonyms, definition, relationships)
      SELECT ontology, ontology_release, concept_id, label,
             synonyms::jsonb, NULLIF(definition,''), relationships::jsonb
      FROM ontology_concepts_in WHERE true
      ON CONFLICT (ontology,ontology_release,concept_id) DO UPDATE SET
        label=EXCLUDED.label, synonyms=EXCLUDED.synonyms,
        definition=EXCLUDED.definition, relationships=EXCLUDED.relationships""")
    cur.execute("""INSERT INTO ontology_terms
        (ontology,ontology_release,concept_id,term_normalized,term_text,term_kind)
      SELECT ontology,ontology_release,concept_id,term_normalized,term_text,term_kind
      FROM ontology_terms_in WHERE true
      ON CONFLICT (ontology,ontology_release,concept_id,term_normalized) DO UPDATE SET
        term_text=EXCLUDED.term_text, term_kind=EXCLUDED.term_kind""")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--file', type=Path, required=True)
    p.add_argument('--ontology', choices=['MONDO', 'HP'], required=True)
    p.add_argument('--source-uri', required=True)
    p.add_argument('--license', dest='license_name', default='verify-before-redistribution')
    p.add_argument('--release', help='Overrides the OBO header data-version if absent/ambiguous')
    p.add_argument('--activate', action='store_true',
                   help='Explicitly switch active release for query lookup')
    args = p.parse_args()
    if not args.file.is_file():
        p.error('Ontology file is missing; download it during preparation first')
    detected, _ = parse_obo(args.file, args.ontology)
    release = args.release or detected
    if not release or len(release) > 160:
        p.error('Ontology release unavailable: supply --release with a pinned version')
    digest = sha256_file(args.file)
    started = time.perf_counter()
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        with conn.cursor() as cur:
            cur.execute(MIGRATION.read_text(encoding='utf-8'))
            cur.execute("""SELECT source_sha256 FROM ontology_releases
                           WHERE ontology=%s AND ontology_release=%s""",
                        (args.ontology, release))
            existing = cur.fetchone()
            if existing and existing[0] != digest:
                raise RuntimeError('Same ontology/release but different SHA256; refusing to overwrite!')
            if not existing:
                concepts, term_count = stage_rows(cur, args.ontology, release, args.file)
                if not concepts:
                    raise ValueError('No valid active OBO concepts found; refusing empty release')
                merge(cur)
                cur.execute("""INSERT INTO ontology_releases
                  (ontology,ontology_release,source_uri,source_sha256,source_filename,
                   source_license,imported_concepts,imported_terms)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                  (args.ontology, release, args.source_uri, digest, args.file.name,
                   args.license_name, concepts, term_count))
            else:
                cur.execute("""SELECT imported_concepts, imported_terms FROM ontology_releases
                              WHERE ontology=%s AND ontology_release=%s""",
                            (args.ontology, release))
                concepts, term_count = cur.fetchone()
            if args.activate:
                cur.execute("""INSERT INTO ontology_active_releases(ontology,ontology_release)
                  VALUES (%s,%s) ON CONFLICT (ontology) DO UPDATE SET
                  ontology_release=EXCLUDED.ontology_release""", (args.ontology, release))
    print(json.dumps({'ontology': args.ontology, 'release': release, 'sha256': digest,
                      'concepts': concepts, 'terms': term_count,
                      'active': args.activate, 'already_imported': bool(existing),
                      'elapsed_s': round(time.perf_counter()-started, 2)}, indent=2))

if __name__ == '__main__':
    main()
