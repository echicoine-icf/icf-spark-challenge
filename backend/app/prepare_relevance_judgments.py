"""Prepare a BLANK human relevance-judgment worksheet from original results.

Never creates synthetic relevance labels. Do not commit research judgments from
private challenge holdouts, or use public benchmark answers as hardcoded outputs.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
import json

QUERY_SET = (
    ('Q01','diabetes'),
    ('Q02','glycated hemoglobin'),
    ('Q03','renal function'),
    ('Q04','metformin'),
    ('Q05','colorectal cancer'),
    ('Q06','blood pressure'),
)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-url',default='http://api:8000')
    p.add_argument('--candidate-cap',type=int,default=4000)
    p.add_argument('--limit',type=int,default=20)
    p.add_argument('--output',type=Path,default=Path('/reports/relevance_judgments_template.csv'))
    p.add_argument('--force',action='store_true',help='Overwrite an existing worksheet; OFF by default')
    args=p.parse_args()
    if args.output.exists() and not args.force:
        p.error(f'{args.output} already exists; refusing to overwrite hand-entered judgments (use --force explicitly)')
    rows=[]
    for query_id,query in QUERY_SET:
        params=urlencode({'q':query,'limit':args.limit,'candidate_cap':args.candidate_cap})
        with urlopen(args.base_url.rstrip('/')+'/api/v1/search?'+params,timeout=120) as res:
            result=json.load(res)
        for rank,item in enumerate(result['results'],1):
            rows.append({'query_id':query_id,'query':query,'rank':rank,
                         'study_id':item['study_id'],'study_title':item['original_title'],
                         'matched_sources':';'.join(item.get('matched_original_sources',[])),
                         'relevance_0_to_3':'','notes':''})
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=['query_id','query','rank','study_id','study_title',
                                            'matched_sources','relevance_0_to_3','notes'])
        writer.writeheader()
        writer.writerows(rows)
    print(f'Created {len(rows)} UNLABELED result rows in {args.output}')
    print('Grade 0=not relevant, 1=weak, 2=relevant, 3=highly relevant. Labels remain blank until a human reviews evidence.')

if __name__=='__main__':
    main()
