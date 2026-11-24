"""Offline, transaction-safe importer for pinned LOINC, RxNorm, and SNOMED CT US ZIP releases.

The supplied archives are never downloaded here. Registry rows are immutable by
(ontology, release) and source SHA-256. Stage/COPY + set-based inserts make the
operation idempotent while preserving original corpus and other ontologies.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time


from app.ontology_knowledge import normalize_term, sha256_file
from app.terminology_files import read_records, validate_release_filename

MIGRATION = Path('/migrations/006_typed_terminology_sources.sql')
SNOMED_MIGRATION = Path('/migrations/021_snomed_ct_us.sql')
DEFAULT_SOURCES = {
    'LOINC': ('https://loinc.org/downloads', 'LOINC License (https://loinc.org/license)'),
    'RXNORM': ('https://www.nlm.nih.gov/research/umls/licensedcontent/rxnormfiles.html',
               'NLM RxNorm Prescribable Content terms of service; SAB=RXNORM only'),
    'SNOMEDCT_US': ('https://www.nlm.nih.gov/healthit/snomedct/us_edition.html',
                     'SNOMED CT Affiliate License / UMLS Terminology Services'),
}


def stage_rows(cur, ontology: str, release: str, source: Path):
    records = list(read_records(source, ontology))
    if not records:
        raise ValueError('No eligible concepts in supplied ZIP; wrong file, release or filtered content')
    unique = set()
    for r in records:
        if r.concept_id in unique:
            raise ValueError(f'Duplicate concept identifier in source: {r.concept_id}')
        unique.add(r.concept_id)
    cur.execute('''CREATE TEMP TABLE source_concepts (
        ontology text, ontology_release text, concept_id text,
        label text, synonyms text, definition text, relationships text
    ) ON COMMIT DROP''')
    cur.execute('''CREATE TEMP TABLE source_terms (
        ontology text, ontology_release text, concept_id text,
        term_normalized text, term_text text, term_kind text
    ) ON COMMIT DROP''')
    cur.execute('''CREATE TEMP TABLE source_metadata (
        ontology text, ontology_release text, concept_id text,
        concept_type text, subtype text, source_status text, attributes text
    ) ON COMMIT DROP''')
    with cur.copy('COPY source_concepts FROM STDIN') as copy:
        for r in records:
            copy.write_row((ontology, release, r.concept_id, r.label,
                            json.dumps(r.aliases), '', '[]'))
    term_count = 0
    with cur.copy('COPY source_terms FROM STDIN') as copy:
        for r in records:
            for i, txt in enumerate((r.label, *r.aliases)):
                copy.write_row((ontology, release, r.concept_id, normalize_term(txt),
                                txt, 'label' if i == 0 else 'exact_synonym'))
                term_count += 1
    with cur.copy('COPY source_metadata FROM STDIN') as copy:
        for r in records:
            copy.write_row((ontology, release, r.concept_id, r.concept_type,
                            r.subtype, r.status, json.dumps(r.attributes)))
    return len(records), term_count


def merge(cur):
    cur.execute('''INSERT INTO ontology_concepts
        (ontology,ontology_release,concept_id,label,synonyms,definition,relationships)
        SELECT ontology,ontology_release,concept_id,label,synonyms::jsonb,
               NULLIF(definition,''),relationships::jsonb FROM source_concepts WHERE true
        ON CONFLICT (ontology,ontology_release,concept_id) DO NOTHING''')
    cur.execute('''INSERT INTO ontology_terms
        (ontology,ontology_release,concept_id,term_normalized,term_text,term_kind)
        SELECT ontology,ontology_release,concept_id,term_normalized,term_text,term_kind
        FROM source_terms WHERE true ON CONFLICT (ontology,ontology_release,concept_id,term_normalized)
        DO NOTHING''')
    cur.execute('''INSERT INTO terminology_concept_metadata
        (ontology,ontology_release,concept_id,concept_type,subtype,source_status,attributes)
        SELECT ontology,ontology_release,concept_id,concept_type,subtype,source_status,
               attributes::jsonb FROM source_metadata WHERE true
        ON CONFLICT (ontology,ontology_release,concept_id) DO NOTHING''')


def import_release(cur, ontology: str, release: str, source: Path, *, uri: str,
                   license_name: str, activate: bool) -> dict:
    """Caller owns a single transaction. Error = entire new release rolls back."""
    digest = sha256_file(source)
    cur.execute(MIGRATION.read_text(encoding='utf-8'))
    if ontology == 'SNOMEDCT_US':
        cur.execute(SNOMED_MIGRATION.read_text(encoding='utf-8'))
    cur.execute('''SELECT source_sha256, imported_concepts, imported_terms
                   FROM ontology_releases WHERE ontology=%s AND ontology_release=%s''',
                (ontology, release))
    existing = cur.fetchone()
    if existing and existing[0] != digest:
        raise RuntimeError(f'{ontology} {release} already exists with a DIFFERENT SHA256; refusing replacement')
    if existing:
        concepts, terms = int(existing[1]), int(existing[2])
    else:
        concepts, terms = stage_rows(cur, ontology, release, source)
        merge(cur)
        cur.execute('''INSERT INTO ontology_releases
            (ontology, ontology_release,source_uri,source_sha256,source_filename,
             source_license,imported_concepts,imported_terms)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
            (ontology, release, uri, digest, source.name, license_name, concepts, terms))
    if activate:
        cur.execute('''INSERT INTO ontology_active_releases (ontology,ontology_release)
                       VALUES (%s,%s) ON CONFLICT(ontology) DO UPDATE SET
                       ontology_release=EXCLUDED.ontology_release''', (ontology,release))
    # A registry receipt is not sufficient proof that all records are present.
    # Verify independent counts before committing this transaction.
    cur.execute('''SELECT
        (SELECT count(*) FROM ontology_concepts WHERE ontology=%s AND ontology_release=%s),
        (SELECT count(*) FROM ontology_terms WHERE ontology=%s AND ontology_release=%s),
        (SELECT count(*) FROM terminology_concept_metadata WHERE ontology=%s AND ontology_release=%s)''',
        (ontology, release, ontology, release, ontology, release))
    found_concepts, found_terms, found_typed = (int(n) for n in cur.fetchone())
    if (found_concepts, found_terms, found_typed) != (concepts, terms, concepts):
        raise RuntimeError(f'{ontology} {release} DB rows differ from registered counts: '
                           f'{found_concepts}/{found_terms}/{found_typed} vs '
                           f'{concepts}/{terms}/{concepts}')
    return {'ontology': ontology, 'release': release, 'sha256': digest,
            'concepts': concepts, 'terms': terms, 'active': activate,
            'already_imported': bool(existing), 'source_file': source.name}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, required=True)
    parser.add_argument('--ontology', choices=tuple(DEFAULT_SOURCES), required=True)
    parser.add_argument('--release', required=True,
                        help='Explicit immutable upstream release ID, e.g. 2.83, 2026-11-02, or 20260901')
    parser.add_argument('--source-uri', help='Public upstream distribution page')
    parser.add_argument('--activate', action='store_true')
    parser.add_argument('--ack-license', action='store_true',
                        help='Confirm you obtained the ZIP under and reviewed applicable upstream terms')
    args = parser.parse_args()
    if not args.ack_license:
        parser.error('You must explicitly acknowledge upstream terms via --ack-license')
    if not args.file.is_file() or args.file.suffix.lower() != '.zip':
        parser.error('A locally supplied ZIP file must exist (no live downloads during import)')
    if not args.release.strip() or len(args.release) > 100:
        parser.error('A pinned release ID (<=100 characters) is required')
    validate_release_filename(args.file, args.ontology, args.release)
    uri, license_name = DEFAULT_SOURCES[args.ontology]
    started = time.perf_counter()
    import psycopg
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        with conn.cursor() as cur:
            output = import_release(cur, args.ontology, args.release, args.file,
                                    uri=args.source_uri or uri,
                                    license_name=license_name, activate=args.activate)
    output['elapsed_s'] = round(time.perf_counter()-started, 2)
    print(json.dumps(output, indent=2), flush=True)


if __name__ == '__main__':
    main()
