"""Offline readers for user-supplied LOINC, RxNorm, and SNOMED CT US archives.

LOINC CLASS=lab (CLASSTYPE=1), STATUS=ACTIVE, no third-party copyright
notices. Its RELATEDNAMES2 are NOT equivalences. RxNorm accepts only active
English SAB=RXNORM strings; no outside-source proprietary synonyms. SNOMED CT
US uses active RF2 Snapshot concepts/descriptions and the en-US language refset;
only descriptions attached to the same SCTID are treated as aliases.
"""
from __future__ import annotations

import csv

# SNOMED CT RF2 can contain fields larger than Python's default 128 KiB CSV limit.
# Keep the parser bounded, but high enough for official terminology releases.
csv.field_size_limit(64 * 1024 * 1024)
from dataclasses import dataclass
from io import TextIOWrapper
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from collections import defaultdict
import re
from datetime import datetime

from app.ontology_knowledge import normalize_term

LOINC_CODE = re.compile(r'^\d{1,8}-\d$')
RXCUI = re.compile(r'^\d{1,10}$')
SCTID = re.compile(r'^\d{6,18}$')
RXNORM_MAIN_TTYS = frozenset((
    'IN', 'PIN', 'MIN', 'BN', 'SCDC', 'SCDF', 'SCDFP', 'SCDG', 'SCDGP',
    'SCD', 'SBDC', 'SBDF', 'SBDFP', 'SBDG', 'SBD', 'GPCK', 'BPCK',
))
RXNORM_ALIAS_TTYS = frozenset(('PSN', 'SY'))
RXNORM_TTYS = RXNORM_MAIN_TTYS | RXNORM_ALIAS_TTYS

# RF2 identifiers from the SNOMED CT metadata hierarchy.
SNOMED_FSN = '900000000000003001'
SNOMED_SYNONYM = '900000000000013009'
SNOMED_PREFERRED = '900000000000548007'


@dataclass(frozen=True)
class Record:
    concept_id: str
    label: str
    aliases: tuple[str, ...]
    concept_type: str
    subtype: str
    status: str
    attributes: dict


def unique_aliases(label: str, aliases: list[str]) -> tuple[str, ...]:
    seen = {normalize_term(label)}
    output = []
    for alias in aliases:
        alias = alias.strip()
        key = normalize_term(alias)
        if len(key) < 3 or key in seen:
            continue
        seen.add(key)
        output.append(alias)
    return tuple(output)


def zip_member(zf: ZipFile, filename: str) -> str:
    matches = [name for name in zf.namelist()
               if not name.endswith('/') and Path(name).name.casefold() == filename.casefold()]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one {filename} in ZIP, found {len(matches)}')
    return matches[0]


def loinc_primary_member(zf: ZipFile) -> str:
    """Use the authoritative LoincTable CSV, not the PanelsAndForms duplicate."""
    matches = [info.filename for info in zf.infolist()
               if not info.is_dir()
               and info.filename.replace('\\', '/').split('/')[-1].casefold() == 'loinc.csv']
    primary = [name for name in matches
               if len(name.replace('\\', '/').split('/')) >= 2
               and name.replace('\\', '/').split('/')[-2].casefold() == 'loinctable']
    if len(primary) != 1:
        raise ValueError(
            'Expected exactly one authoritative LoincTable/Loinc.csv in ZIP; '
            f'found {len(primary)} primary matches and {len(matches)} Loinc.csv files: '
            + ', '.join(sorted(matches))
        )
    return primary[0]


def iter_loinc(path: Path):
    """Only ACTIVE laboratory tests; no third-party copyrighted content."""
    with ZipFile(path) as zf:
        with zf.open(loinc_primary_member(zf)) as raw:
            reader = csv.DictReader(TextIOWrapper(raw, encoding='utf-8-sig', errors='strict', newline=''))
            required = {'LOINC_NUM', 'LONG_COMMON_NAME', 'SHORTNAME', 'CLASSTYPE',
                        'STATUS', 'COMPONENT', 'SYSTEM', 'PROPERTY', 'TIME_ASPCT',
                        'SCALE_TYP', 'METHOD_TYP', 'EXTERNAL_COPYRIGHT_NOTICE'}
            if not required.issubset(set(reader.fieldnames or [])):
                raise ValueError(f'LOINC CSV missing required fields: {sorted(required - set(reader.fieldnames or []))}')
            for row in reader:
                if row['CLASSTYPE'].strip() != '1' or row['STATUS'].strip().upper() != 'ACTIVE':
                    continue
                if row['EXTERNAL_COPYRIGHT_NOTICE'].strip():
                    continue
                cid = row['LOINC_NUM'].strip()
                label = row['LONG_COMMON_NAME'].strip()
                if not LOINC_CODE.fullmatch(cid) or not label:
                    continue
                yield Record('LOINC:' + cid, label,
                             unique_aliases(label, [row['SHORTNAME']]),
                             'laboratory_measurement', row.get('CLASS', '').strip() or 'LAB',
                             'ACTIVE', {'loinc_num': cid, 'class_type': '1',
                                        'component': row['COMPONENT'], 'property': row['PROPERTY'],
                                        'timing': row['TIME_ASPCT'], 'system': row['SYSTEM'],
                                        'scale': row['SCALE_TYP'], 'method': row['METHOD_TYP'],
                                        'class': row.get('CLASS', ''),
                                        'alias_policy': 'same LOINC code shortname only'})


def iter_rxnorm(path: Path):
    """Only non-suppressed English RXNORM strings from the prescribable subset."""
    groups: dict[str, dict] = defaultdict(lambda: {'primary': [], 'aliases': []})
    with ZipFile(path) as zf:
        with zf.open(zip_member(zf, 'RXNCONSO.RRF')) as raw:
            for line in TextIOWrapper(raw, encoding='utf-8-sig', errors='strict'):
                fields = line.rstrip('\r\n').split('|')
                if len(fields) < 18:
                    raise ValueError('RXNCONSO.RRF has fewer than 18 fields')
                rxcui, lat, is_pref, sab, tty, name, suppress = (
                    fields[0], fields[1], fields[6], fields[11], fields[12], fields[14], fields[16])
                if (lat != 'ENG' or sab != 'RXNORM' or suppress != 'N' or
                        tty not in RXNORM_TTYS or not RXCUI.fullmatch(rxcui) or not name.strip()):
                    continue
                entry = groups[rxcui]
                record = (name.strip(), tty, is_pref == 'Y')
                entry['primary' if tty in RXNORM_MAIN_TTYS else 'aliases'].append(record)
    for rxcui, parts in sorted(groups.items(), key=lambda x: int(x[0])):
        primary = parts['primary']
        ttys = {tty for _, tty, _ in primary}
        if not primary or len(ttys) != 1:
            continue
        tty = next(iter(ttys))
        primary.sort(key=lambda item: (not item[2], normalize_term(item[0]), item[0]))
        label = primary[0][0]
        aliases = [v[0] for v in primary[1:] + parts['aliases']]
        yield Record('RXNORM:' + rxcui, label, unique_aliases(label, aliases),
                     'medication', tty, 'ACTIVE',
                     {'rxcui': rxcui, 'tty': tty, 'source_abbreviation': 'RXNORM',
                      'subset': 'current_prescribable_content',
                      'alias_policy': 'same RxCUI, SAB=RXNORM, SUPPRESS=N only'})


def _rf2_member(zf: ZipFile, pattern: re.Pattern[str], description: str) -> str:
    matches = [info.filename for info in zf.infolist()
               if not info.is_dir() and pattern.search(Path(info.filename).name)]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one {description} RF2 Snapshot file, found {len(matches)}: '
                         + ', '.join(sorted(matches)))
    return matches[0]


def _snomed_members(zf: ZipFile) -> tuple[str, str, str]:
    concept = _rf2_member(zf, re.compile(r'^sct2_Concept_Snapshot.*\.txt$', re.I), 'Concept')
    description = _rf2_member(zf, re.compile(r'^sct2_Description_Snapshot-en_US.*\.txt$', re.I),
                              'en-US Description')
    language = _rf2_member(zf, re.compile(r'^der2_cRefset_LanguageSnapshot-en_US.*\.txt$', re.I),
                           'en-US Language')
    return concept, description, language


def _strip_semantic_tag(fsn: str) -> tuple[str, str]:
    match = re.match(r'^(.*?)\s+\(([^()]+)\)\s*$', fsn.strip())
    if not match:
        return fsn.strip(), ''
    return match.group(1).strip(), match.group(2).strip().casefold()


def _snomed_type(semantic_tag: str) -> str:
    if semantic_tag == 'disorder':
        return 'disease'
    if semantic_tag in {'finding', 'observable entity'}:
        return 'phenotype'
    return 'clinical_concept'


def iter_snomed_us(path: Path):
    """Read active concepts from an official SNOMED CT US Edition RF2 Snapshot.

    The preferred en-US synonym is the canonical display label. Active synonyms
    attached to the same SCTID are exact lexical aliases for lookup purposes;
    no hierarchy traversal or cross-concept equivalence is inferred here.
    """
    with ZipFile(path) as zf:
        concept_member, description_member, language_member = _snomed_members(zf)

        active: dict[str, dict] = {}
        with zf.open(concept_member) as raw:
            reader = csv.DictReader(TextIOWrapper(raw, encoding='utf-8-sig', errors='strict'), delimiter='\t')
            required = {'id', 'effectiveTime', 'active', 'moduleId', 'definitionStatusId'}
            if not required.issubset(set(reader.fieldnames or [])):
                raise ValueError('SNOMED Concept Snapshot is missing required RF2 columns')
            for row in reader:
                cid = row['id'].strip()
                if row['active'] != '1' or not SCTID.fullmatch(cid):
                    continue
                active[cid] = {
                    'effective_time': row['effectiveTime'].strip(),
                    'module_id': row['moduleId'].strip(),
                    'definition_status_id': row['definitionStatusId'].strip(),
                }

        preferred_description_ids: set[str] = set()
        with zf.open(language_member) as raw:
            reader = csv.DictReader(TextIOWrapper(raw, encoding='utf-8-sig', errors='strict'), delimiter='\t')
            required = {'active', 'referencedComponentId', 'acceptabilityId'}
            if not required.issubset(set(reader.fieldnames or [])):
                raise ValueError('SNOMED Language Snapshot is missing required RF2 columns')
            for row in reader:
                if row['active'] == '1' and row['acceptabilityId'].strip() == SNOMED_PREFERRED:
                    preferred_description_ids.add(row['referencedComponentId'].strip())

        descriptions: dict[str, dict[str, list[tuple[str, str]]]] = defaultdict(
            lambda: {'fsn': [], 'synonym': []})
        with zf.open(description_member) as raw:
            reader = csv.DictReader(TextIOWrapper(raw, encoding='utf-8-sig', errors='strict'), delimiter='\t')
            required = {'id', 'active', 'conceptId', 'languageCode', 'typeId', 'term'}
            if not required.issubset(set(reader.fieldnames or [])):
                raise ValueError('SNOMED Description Snapshot is missing required RF2 columns')
            for row in reader:
                cid = row['conceptId'].strip()
                if cid not in active or row['active'] != '1' or row['languageCode'].lower() != 'en':
                    continue
                description_id = row['id'].strip()
                term = row['term'].strip()
                if not term:
                    continue
                if row['typeId'].strip() == SNOMED_FSN:
                    descriptions[cid]['fsn'].append((description_id, term))
                elif row['typeId'].strip() == SNOMED_SYNONYM:
                    descriptions[cid]['synonym'].append((description_id, term))

        for cid in sorted(active, key=int):
            bucket = descriptions.get(cid)
            if not bucket or not bucket['fsn']:
                continue
            fsn = sorted(bucket['fsn'], key=lambda item: item[0])[0][1]
            fsn_label, semantic_tag = _strip_semantic_tag(fsn)
            synonyms = bucket['synonym']
            preferred = [term for desc_id, term in synonyms if desc_id in preferred_description_ids]
            label = sorted(preferred, key=lambda value: (normalize_term(value), value))[0] if preferred else fsn_label
            alias_values = [fsn_label] + [term for _, term in synonyms]
            meta = active[cid]
            yield Record(
                'SNOMEDCT_US:' + cid,
                label,
                unique_aliases(label, alias_values),
                _snomed_type(semantic_tag),
                semantic_tag or 'unknown',
                'ACTIVE',
                {
                    'sctid': cid,
                    'fsn': fsn,
                    'semantic_tag': semantic_tag,
                    'effective_time': meta['effective_time'],
                    'module_id': meta['module_id'],
                    'definition_status_id': meta['definition_status_id'],
                    'edition': 'US',
                    'alias_policy': 'active en-US descriptions attached to the same SCTID only',
                },
            )


def read_records(path: Path, ontology: str):
    if not path.is_file():
        raise FileNotFoundError(f'Terminology archive not found: {path}')
    if ontology not in ('LOINC', 'RXNORM', 'SNOMEDCT_US'):
        raise ValueError('Supported sources: LOINC, RXNORM, SNOMEDCT_US')
    try:
        if ontology == 'LOINC':
            yield from iter_loinc(path)
        elif ontology == 'RXNORM':
            yield from iter_rxnorm(path)
        else:
            yield from iter_snomed_us(path)
    except BadZipFile as exc:
        raise ValueError('Expected authentic upstream ZIP archive, not an extracted text file') from exc


def validate_release_filename(path: Path, ontology: str, release: str) -> None:
    """Refuse silent current aliases and verify the pinned release in the archive name."""
    if ontology == 'LOINC':
        matched = re.fullmatch(r'Loinc_(\d+(?:\.\d+)*)\.zip', path.name, re.I)
        if not matched or matched.group(1) != release:
            raise ValueError('LOINC archive filename/release mismatch: expected Loinc_<release>.zip')
    elif ontology == 'RXNORM':
        matched = re.fullmatch(r'RxNorm_full_prescribe_(\d{8})\.zip', path.name, re.I)
        if not matched:
            raise ValueError('Use RxNorm FULL PRESCRIBABLE subset ZIP (not licensed full RxNorm release)')
        detected = datetime.strptime(matched.group(1), '%m%d%Y').date().isoformat()
        if detected != release:
            raise ValueError(f'RxNorm archive release is {detected}, not {release}')
    elif ontology == 'SNOMEDCT_US':
        if not re.fullmatch(r'\d{8}', release):
            raise ValueError('SNOMED CT US release must be the RF2 effective date YYYYMMDD, e.g. 20260901')
        if release not in path.name:
            raise ValueError('SNOMED CT US archive filename/release mismatch: filename must contain the pinned YYYYMMDD release')
        if 'snomed' not in path.name.casefold() or 'us' not in path.name.casefold():
            raise ValueError('Use the official SNOMED CT US Edition RF2 ZIP')
    else:
        raise ValueError('Unsupported terminology source')
