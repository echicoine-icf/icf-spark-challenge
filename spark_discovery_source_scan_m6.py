#!/usr/bin/env python3
"""Static read-only inventory of SPARK search references; not a call-graph verifier.

Captures path, line number, and matched fixed marker ONLY, never raw source text.
No imports of application code, network, database, or subprocesses.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
from pathlib import Path

ROOTS=('backend','frontend','client','web','src','app')
EXTENSIONS={'.py','.ts','.tsx','.js','.jsx'}
SKIP={'node_modules','.git','.venv','venv','dist','build','.next','__pycache__','coverage','.pytest_cache','tests','test'}
MARKERS={
    'typed_route':'/search/typed',
    'normalized_route':'/search/normalized',
    'structured_route':'/search/structured',
    'normalization_route':'/ontology/normalize',
    'terminology_function':'normalize_concept',
    'reviewed_mapping_gate':'include_reviewed_annotations',
    'legacy_tsquery':'websearch_to_tsquery',
    'strict_tsquery':'plainto_tsquery',
    'ranking_rrf':'reciprocal_rank',
    'bounded_candidates':'candidate_pool_truncated',
    'enriched_evidence':'study_enrichments',
    'source_evidence':'source_categories',
    'qwen_runtime':'qwen2.5',
}
MAX_HITS_PER_MARKER=120


def scan(root:Path, max_files:int=2500) -> dict:
    roots=[root/x for x in ROOTS if (root/x).is_dir()]
    found={k:[] for k in MARKERS}
    files=0; skipped=0; visited=set()
    for scanroot in roots:
        for f in scanroot.rglob('*'):
            if not f.is_file() or f.suffix.lower() not in EXTENSIONS or any(part in SKIP for part in f.relative_to(root).parts):
                continue
            if f in visited:
                continue
            visited.add(f)
            if files >= max_files:
                skipped+=1
                continue
            files+=1
            try:
                if f.stat().st_size > 2_000_000:
                    skipped+=1;continue
                with f.open(encoding='utf-8',errors='replace') as stream:
                    for lineno,line in enumerate(stream,1):
                        for name,marker in MARKERS.items():
                            if marker.lower() in line.lower() and len(found[name]) < MAX_HITS_PER_MARKER:
                                found[name].append({'file':f.relative_to(root).as_posix(),'line':lineno,'marker':marker})
            except (OSError,ValueError):
                skipped+=1
    return {'scanned_roots':[p.relative_to(root).as_posix() for p in roots],
            'code_files_scanned':files, 'files_skipped_or_limited':skipped,
            'markers':found, 'marker_count_by_category':{k:len(v) for k,v in found.items()},
            'interpretation':'Source string references only; no proof of route reachability, frontend use, security, or execution behavior'}


def main(argv=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--output',type=Path,default=Path('reports/m6_discovery_audit/source_inventory.json'))
    args=parser.parse_args(argv)
    result=scan(args.repo.resolve())
    result['generated_at_utc']=dt.datetime.now(dt.timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print('SAVED',args.output)
    print(json.dumps({'roots':result['scanned_roots'],'files':result['code_files_scanned'],'markers':result['marker_count_by_category']},indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
