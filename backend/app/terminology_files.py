"""Offline readers for *user-supplied* LOINC CSV ZIP and RxNorm prescribable RRF ZIP.

LOINC CLASS=lab (CLASSTYPE=1), STATUS=ACTIVE, no third-party copyright
notices. Its RELATEDNAMES2 are NOT equivalences. RxNorm accepts only active
English SAB=RXNORM strings; no outside-source proprietary synonyms. Concepts
are never conflated across LOINC axes, RxCUIs, dosage, strength, brands, or
ingredient/product levels.
"""
from __future__ import annotations

import csv
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
RXNORM_MAIN_TTYS = frozenset((
    'IN', 'PIN', 'MIN', 'BN', 'SCDC', 'SCDF', 'SCDFP', 'SCDG', 'SCDGP',
    'SCD', 'SBDC', 'SBDF', 'SBDFP', 'SBDG', 'SBD', 'GPCK', 'BPCK',
))
RXNORM_ALIAS_TTYS = frozenset(('PSN', 'SY'))
RXNORM_TTYS = RXNORM_MAIN_TTYS | RXNORM_ALIAS_TTYS


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
    """Use the authoritative LoincTable CSV, not the PanelsAndForms duplicate.

    Official LOINC releases intentionally contain multiple files named
    ``Loinc.csv`` (notably ``LoincTable/`` and ``PanelsAndForms/``).
    Require exactly one main terminology table, including in nested archives.
    Do not silently select the first basename match or an accessory copy.
    """
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
    """Only non-suppressed English RXNORM strings from the prescribable subset.

    SUPPRESS=N and SAB=RXNORM prevent proprietary-source aliases. We do not
    infer relationships between ingredient, brand, or clinical drug RxCUIs.
    """
    groups: dict[str, dict] = defaultdict(lambda: {'primary': [], 'aliases': []})
    with ZipFile(path) as zf:
        with zf.open(zip_member(zf, 'RXNCONSO.RRF')) as raw:
            for line in TextIOWrapper(raw, encoding='utf-8-sig', errors='strict'):
                fields = line.rstrip('\r\n').split('|')
                # RRF has 18 fields plus a trailing empty column after final |.
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
        # Refuse malformed/mixed type concepts rather than merging drug hierarchy levels.
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


def read_records(path: Path, ontology: str):
    if not path.is_file():
        raise FileNotFoundError(f'Terminology archive not found: {path}')
    if ontology not in ('LOINC', 'RXNORM'):
        raise ValueError('Supported sources: LOINC, RXNORM')
    try:
        yield from (iter_loinc(path) if ontology == 'LOINC' else iter_rxnorm(path))
    except BadZipFile as exc:
        raise ValueError('Expected authentic upstream ZIP archive, not an extracted text file') from exc


def validate_release_filename(path: Path, ontology: str, release: str) -> None:
    """Refuse silent ``current`` aliases or an unintended full RxNorm package."""
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
    else:
        raise ValueError('Unsupported terminology source')
