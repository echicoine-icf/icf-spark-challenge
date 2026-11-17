"""Preparation-time download of public OBO terminology sources.

Do not run during NIH isolated evaluation; freeze, hash and disclose sources.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import urlopen, Request
from app.ontology_knowledge import parse_obo, sha256_file

SOURCES = {
    'MONDO': ('https://purl.obolibrary.org/obo/mondo.obo', 'MONDO.obo'),
    'HP': ('https://purl.obolibrary.org/obo/hp.obo', 'HP.obo'),
}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ontology', choices=SOURCES, required=True)
    p.add_argument('--out', type=Path, default=Path('/knowledge'))
    p.add_argument('--refresh', action='store_true',
                   help='Explicitly replace a previously downloaded source')
    args = p.parse_args()
    url, filename = SOURCES[args.ontology]
    args.out.mkdir(parents=True, exist_ok=True)
    dest = args.out / filename
    if dest.exists() and not args.refresh:
        print('EXISTING source preserved; use --refresh to intentionally update:', dest)
    else:
        temp = dest.with_suffix('.download')
        try:
            with urlopen(Request(url, headers={'User-Agent':'ICF-SPARK-development-ontology-fetch/0.1'}), timeout=120) as response, temp.open('wb') as out:
                while chunk := response.read(1024*1024):
                    out.write(chunk)
            release, iterator = parse_obo(temp, args.ontology)
            if not release or not next(iterator, None):
                raise ValueError('Downloaded file is not an OBO release with active terms')
            temp.replace(dest)
        finally:
            temp.unlink(missing_ok=True)
    release, _ = parse_obo(dest, args.ontology)
    record = {'ontology': args.ontology, 'source_uri': url, 'filename': filename,
              'sha256': sha256_file(dest), 'release_header': release,
              'bytes': dest.stat().st_size}
    receipt = args.out / (args.ontology + '.receipt.json')
    if receipt.exists() and not args.refresh:
        previous = json.loads(receipt.read_text(encoding='utf-8'))
        if previous['sha256'] != record['sha256']:
            raise RuntimeError('Existing receipt and file hashes disagree: manual review required')
    else:
        receipt.write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(json.dumps(record, indent=2))

if __name__ == '__main__':
    main()
