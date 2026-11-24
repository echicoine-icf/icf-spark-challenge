"""Synthetic fixtures only. Never ship copyrighted LOINC or RxNorm source rows."""
import csv
from pathlib import Path
from zipfile import ZipFile
import io

import pytest

from app.terminology_files import iter_loinc, iter_rxnorm, read_records, validate_release_filename
from app.concept_normalization import type_concept, decide_exact, label_exact_candidates


FIELDS = ['LOINC_NUM','LONG_COMMON_NAME','SHORTNAME','CLASSTYPE','STATUS',
          'COMPONENT','SYSTEM','PROPERTY','TIME_ASPCT','SCALE_TYP',
          'METHOD_TYP','EXTERNAL_COPYRIGHT_NOTICE','RELATEDNAMES2','CLASS']


def make_loinc(tmp_path):
    path = tmp_path/'Loinc_2.83.zip'
    buf = io.StringIO(newline='')
    wr = csv.DictWriter(buf, FIELDS)
    wr.writeheader()
    def row(code, *, cls='1', status='ACTIVE', copyright='', related='glucose'):
        wr.writerow({'LOINC_NUM':code, 'LONG_COMMON_NAME':f'Synthetic serum measure {code}',
                     'SHORTNAME':f'SyntheticShort-{code}', 'CLASSTYPE':cls,
                     'STATUS':status, 'COMPONENT':'Synthetic', 'SYSTEM':'Ser',
                     'PROPERTY':'MCnc', 'TIME_ASPCT':'Pt','SCALE_TYP':'Qn',
                     'METHOD_TYP':'', 'EXTERNAL_COPYRIGHT_NOTICE':copyright,
                     'RELATEDNAMES2':related, 'CLASS':'CHEM'})
    row('1234-5')
    row('2345-6',cls='2')
    row('3456-7',status='DISCOURAGED')
    row('4567-8',copyright='Third-party terms')
    with ZipFile(path,'w') as z:
        z.writestr('Loinc_2.83/LoincTable/Loinc.csv',buf.getvalue())
    return path


def rrf(rxcui, term, tty, *, sab='RXNORM', suppress='N', lang='ENG', is_pref='Y'):
    cols = ['']*18
    cols[0],cols[1],cols[6] = rxcui,lang,is_pref
    cols[11],cols[12],cols[14],cols[16] = sab,tty,term,suppress
    return '|'.join(cols)+'|\n'


def make_rxnorm(tmp_path):
    path = tmp_path/'RxNorm_full_prescribe_11022026.zip'
    rows = [
        rrf('111','metformin','IN'),
        rrf('111','Metformin','SY',is_pref='N'),
        rrf('222','Glucophage','BN'),
        rrf('333','metformin 500 MG Oral Tablet','SCD'),
        rrf('333','metformin 500mg tablet','PSN'),
        rrf('444','suppressed drug','IN',suppress='Y'),
        rrf('555','foreign language','IN',lang='SPA'),
        rrf('666','proprietary source content','IN',sab='MMSL'),
        rrf('777','Oral Tablet','DF'),
        rrf('888','malformed mixed types','IN'),
        rrf('888','other mixed type','SCD'),
    ]
    with ZipFile(path,'w') as z:
        z.writestr('RxNorm_prescribe_11022026/rrf/RXNCONSO.RRF',''.join(rows))
    return path


def test_loinc_only_eligible_laboratory_records(tmp_path):
    records = list(iter_loinc(make_loinc(tmp_path)))
    assert len(records) == 1
    r = records[0]
    assert r.concept_id == 'LOINC:1234-5'
    assert r.concept_type == 'laboratory_measurement'
    assert r.attributes['class_type'] == '1'
    assert r.aliases == ('SyntheticShort-1234-5',)
    assert 'glucose' not in r.aliases   # RELATEDNAMES2 is explicitly NOT a synonym


def test_rxnorm_only_normalized_non_suppressed_english_source(tmp_path):
    records = {r.concept_id:r for r in iter_rxnorm(make_rxnorm(tmp_path))}
    assert set(records) == {'RXNORM:111','RXNORM:222','RXNORM:333'}
    assert records['RXNORM:111'].subtype == 'IN'
    assert records['RXNORM:222'].subtype == 'BN'
    assert records['RXNORM:333'].subtype == 'SCD'
    assert records['RXNORM:333'].aliases == ('metformin 500mg tablet',)
    assert 'Glucophage' not in records['RXNORM:111'].aliases
    assert 'metformin 500 MG Oral Tablet' not in records['RXNORM:111'].aliases


def test_reader_rejects_missing_wrong_member(tmp_path):
    file = tmp_path/'bad.zip'
    with ZipFile(file,'w') as z:
        z.writestr('wrong.csv','a,b\n')
    with pytest.raises(ValueError, match='LoincTable/Loinc.csv'):
        list(read_records(file,'LOINC'))


def test_onto_typed_metadata_must_match_verified_code():
    row={'ontology':'LOINC','ontology_release':'2.83','concept_id':'LOINC:1234-5',
         'label':'Synthetic test','exact_synonyms':['Synthetic abbreviated test'],
         'source_sha256':'f'*64,'source_uri':'https://loinc.org/downloads'}
    ident=('LOINC','2.83','LOINC:1234-5')
    meta={ident:{'concept_type':'laboratory_measurement','subtype':'CHEM',
                 'source_status':'ACTIVE','attributes':{'class_type':'1'}}}
    assert type_concept(row,set(),meta) == 'laboratory_measurement'
    normalized = label_exact_candidates('Synthetic abbreviated test',[row],set(),meta)
    output=decide_exact('Synthetic abbreviated test',normalized,
                       requested_type='laboratory_measurement',max_expansions=4,
                       matches_truncated=False)
    assert output['status'] == 'normalized_exact'
    assert output['expansions'] == ['Synthetic test']
    assert output['normalized_concept']['type_provenance']=='LOINC_ACTIVE_class_type_1'
    bad={ident:{**meta[ident], 'source_status':'DISCOURAGED'}}
    assert type_concept(row,set(),bad)=='unclassified'


def test_rxnorm_no_ingredient_product_substitution():
    row={'ontology':'RXNORM','ontology_release':'2026-11-02', 'concept_id':'RXNORM:111',
         'label':'metformin', 'exact_synonyms':[], 'source_uri':'https://example.org',
         'source_sha256':'f'*64}
    meta={('RXNORM','2026-11-02','RXNORM:111'):{
        'concept_type':'medication','subtype':'IN','source_status':'ACTIVE','attributes':{}}}
    typed=label_exact_candidates('metformin',[row],set(),meta)
    assert typed[0]['concept_subtype']=='IN'
    assert decide_exact('metformin',typed,requested_type='medication',
                        max_expansions=4,matches_truncated=False)['status']=='normalized_exact'


def test_file_release_and_subset_validation(tmp_path):
    loinc=make_loinc(tmp_path)
    rx=make_rxnorm(tmp_path)
    validate_release_filename(loinc,'LOINC','2.83')
    validate_release_filename(rx,'RXNORM','2026-11-02')
    with pytest.raises(ValueError,match='mismatch'):
        validate_release_filename(loinc,'LOINC','2.82')
    with pytest.raises(ValueError,match='2026-11-02'):
        validate_release_filename(rx,'RXNORM','2026-10-05')
    with pytest.raises(ValueError,match='PRESCRIBABLE'):
        validate_release_filename(tmp_path/'RxNorm_full_11022026.zip','RXNORM','2026-11-02')


def test_official_loinc_duplicate_selects_primary_table(tmp_path):
    """An official release may have Loinc.csv in two different subfolders."""
    archive = make_loinc(tmp_path)
    with ZipFile(archive, 'a') as z:
        # Accessory copy is deliberately invalid: parsing it would fail.
        z.writestr('Loinc_2.83/PanelsAndForms/Loinc.csv',
                   'LOINC_NUM,LONG_COMMON_NAME\n9999-9,Accessory copy\n')
    rows = list(read_records(archive, 'LOINC'))
    assert [r.concept_id for r in rows] == ['LOINC:1234-5']


def test_reject_multiple_authoritative_loinc_tables(tmp_path):
    archive = make_loinc(tmp_path)
    with ZipFile(archive, 'a') as z:
        z.writestr('OtherRelease/LoincTable/Loinc.csv',
                   'LOINC_NUM,LONG_COMMON_NAME\n9999-9,Wrong release\n')
    with pytest.raises(ValueError, match='found 2 primary matches'):
        list(read_records(archive, 'LOINC'))


def test_does_not_fallback_to_panels_forms_file(tmp_path):
    archive = tmp_path / 'Loinc_2.83.zip'
    with ZipFile(archive, 'w') as z:
        z.writestr('Loinc_2.83/PanelsAndForms/Loinc.csv',
                   'LOINC_NUM,LONG_COMMON_NAME\n9999-9,Accessory copy\n')
    with pytest.raises(ValueError, match='found 0 primary matches'):
        list(read_records(archive, 'LOINC'))


def make_snomed_us(tmp_path):
    """Synthetic RF2 Snapshot fixture; contains no licensed SNOMED content."""
    path = tmp_path/'SnomedCT_USEditionRF2_PRODUCTION_20260901T120000Z.zip'
    concept = (
        'id\teffectiveTime\tactive\tmoduleId\tdefinitionStatusId\n'
        '100001\t20260901\t1\t900000000000207008\t900000000000074008\n'
        '100002\t20260901\t0\t900000000000207008\t900000000000074008\n'
    )
    description = (
        'id\teffectiveTime\tactive\tmoduleId\tconceptId\tlanguageCode\ttypeId\tterm\tcaseSignificanceId\n'
        '200001\t20260901\t1\t900000000000207008\t100001\ten\t900000000000003001\tSynthetic hypertension (disorder)\t900000000000448009\n'
        '200002\t20260901\t1\t900000000000207008\t100001\ten\t900000000000013009\tSynthetic hypertension\t900000000000448009\n'
        '200003\t20260901\t1\t900000000000207008\t100001\ten\t900000000000013009\tSynthetic high blood pressure\t900000000000448009\n'
        '200004\t20260901\t1\t900000000000207008\t100002\ten\t900000000000003001\tInactive synthetic finding (finding)\t900000000000448009\n'
    )
    language = (
        'id\teffectiveTime\tactive\tmoduleId\trefsetId\treferencedComponentId\tacceptabilityId\n'
        '300001\t20260901\t1\t900000000000207008\t900000000000509007\t200002\t900000000000548007\n'
        '300002\t20260901\t1\t900000000000207008\t900000000000509007\t200003\t900000000000549004\n'
    )
    with ZipFile(path,'w') as z:
        z.writestr('Snapshot/Terminology/sct2_Concept_Snapshot_US1000124_20260901.txt', concept)
        z.writestr('Snapshot/Terminology/sct2_Description_Snapshot-en_US1000124_20260901.txt', description)
        z.writestr('Snapshot/Refset/Language/der2_cRefset_LanguageSnapshot-en_US1000124_20260901.txt', language)
    return path


def test_snomed_us_reads_active_preferred_and_same_concept_synonyms(tmp_path):
    from app.terminology_files import iter_snomed_us
    records = list(iter_snomed_us(make_snomed_us(tmp_path)))
    assert len(records) == 1
    row = records[0]
    assert row.concept_id == 'SNOMEDCT_US:100001'
    assert row.label == 'Synthetic hypertension'
    assert row.concept_type == 'disease'
    assert row.subtype == 'disorder'
    assert row.aliases == ('Synthetic high blood pressure',)
    assert row.attributes['fsn'] == 'Synthetic hypertension (disorder)'
    assert row.attributes['edition'] == 'US'


def test_snomed_us_release_validation(tmp_path):
    file = make_snomed_us(tmp_path)
    validate_release_filename(file, 'SNOMEDCT_US', '20260901')
    with pytest.raises(ValueError, match='filename/release mismatch'):
        validate_release_filename(file, 'SNOMEDCT_US', '20260301')


def test_snomed_typed_exact_candidate():
    row={'ontology':'SNOMEDCT_US','ontology_release':'20260901','concept_id':'SNOMEDCT_US:100001',
         'label':'Synthetic hypertension','exact_synonyms':['Synthetic high blood pressure'],
         'source_sha256':'f'*64,'source_uri':'https://www.nlm.nih.gov/healthit/snomedct/us_edition.html'}
    meta={('SNOMEDCT_US','20260901','SNOMEDCT_US:100001'):{
        'concept_type':'disease','subtype':'disorder','source_status':'ACTIVE',
        'attributes':{'semantic_tag':'disorder'}}}
    typed=label_exact_candidates('Synthetic high blood pressure',[row],set(),meta)
    assert type_concept(row,set(),meta) == 'disease'
    assert typed[0]['type_provenance'] == 'SNOMED_CT_US_active_FSN_semantic_tag'
    output=decide_exact('Synthetic high blood pressure',typed,requested_type='disease',
                        max_expansions=3,matches_truncated=False)
    assert output['status'] == 'normalized_exact'
    assert output['normalized_concept']['concept_id'] == 'SNOMEDCT_US:100001'
