"""Read-only terminology parser and conservative query-expansion helpers.

Does not turn ontology *related-to*, parent/child or xrefs into query synonyms.
All extracted facts originate in a versioned, explicit ontology artifact.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from pathlib import Path
from typing import Iterator

_EXACT_SYNONYM = re.compile(r'^synonym:\s*"((?:\\.|[^"\\])*)"\s+EXACT(?:\s|$)')
_DEF = re.compile(r'^def:\s*"((?:\\.|[^"\\])*)"')
_VERSION = re.compile(r'^data-version:\s*(.+)$')
_WHITESPACE = re.compile(r'\s+')


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def normalize_term(text: str) -> str:
    return _WHITESPACE.sub(' ', text.strip()).casefold()


def unescape_obo(text: str) -> str:
    return text.replace(chr(92) + chr(34), chr(34)).replace(chr(92) + 'n', ' ').replace(chr(92)*2, chr(92))


@dataclass(frozen=True)
class Term:
    concept_id: str
    label: str
    definition: str
    synonyms: tuple[str, ...]
    parents: tuple[str, ...]


def parse_obo(path: Path, ontology: str) -> tuple[str | None, Iterator[Term]]:
    """Parse OBO [Term] stanzas (no imports, no network, no synthetic mappings)."""
    with path.open(encoding='utf-8-sig', errors='replace') as handle:
        header = []
        for line in handle:
            if line.strip() == '[Term]':
                break
            header.append(line.strip())
    version = next((m.group(1).strip() for line in header if (m := _VERSION.match(line))), None)

    def iterator() -> Iterator[Term]:
        fields: list[str] = []
        in_term = False
        with path.open(encoding='utf-8-sig', errors='replace') as handle:
            for raw in handle:
                line = raw.strip()
                if line == '[Term]':
                    if in_term:
                        parsed = _decode(fields, ontology)
                        if parsed:
                            yield parsed
                    fields = []
                    in_term = True
                elif line.startswith('['):
                    if in_term:
                        parsed = _decode(fields, ontology)
                        if parsed:
                            yield parsed
                    fields = []
                    in_term = False
                elif in_term and line:
                    fields.append(line)
            if in_term:
                parsed = _decode(fields, ontology)
                if parsed:
                    yield parsed
    return version, iterator()


def _decode(lines: list[str], ontology: str) -> Term | None:
    values: dict[str, list[str]] = {}
    for line in lines:
        if ':' not in line:
            continue
        key, value = line.split(':', 1)
        values.setdefault(key, []).append(value.strip())
    identifier = (values.get('id') or [''])[0]
    if not identifier.startswith(ontology + ':') or not values.get('name'):
        return None
    if (values.get('is_obsolete') or ['false'])[0].lower() == 'true':
        return None
    label = values['name'][0]
    synonyms = []
    for line in lines:
        match = _EXACT_SYNONYM.match(line)
        if match:
            synonym = unescape_obo(match.group(1)).strip()
            if synonym and normalize_term(synonym) != normalize_term(label):
                synonyms.append(synonym)
    definition = ''
    for line in lines:
        match = _DEF.match(line)
        if match:
            definition = unescape_obo(match.group(1))
            break
    parents = [p.split('!',1)[0].strip() for p in values.get('is_a', [])]
    return Term(identifier, label, definition,
                tuple(dict.fromkeys(synonyms)), tuple(dict.fromkeys(parents)))


def choose_expansions(query: str, candidates: list[dict], *, limit: int = 5) -> dict:
    """Only expand an unambiguous exact label/synonym match.

    Higher-order ontology relationships are retained in storage but never used
    as equivalent text. Ambiguous matches across distinct concept IDs opt out.
    """
    if limit < 0:
        raise ValueError("limit must be nonnegative")
    concepts = {(r['ontology'], r['ontology_release'], r['concept_id']) for r in candidates}
    if len(concepts) != 1:
        return {'status':'ambiguous' if concepts else 'not_found',
                'original_query':query, 'expansions':[], 'concepts':candidates}
    canonical = candidates[0]
    if limit == 0:
        return {'status':'resolved','original_query':query, 'expansions':[], 'concepts':candidates}
    terms = [query]
    for term in [canonical['label'], *canonical['exact_synonyms']]:
        if normalize_term(term) not in {normalize_term(t) for t in terms}:
            # Cap additions, avoid generic one-character aliases.
            if len(normalize_term(term)) >= 3:
                terms.append(term)
            if len(terms) >= limit + 1:
                break
    return {'status':'resolved', 'original_query':query,
            'expansions':terms[1:], 'concepts':candidates}
