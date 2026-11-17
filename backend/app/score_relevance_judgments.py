"""Calculate diagnostic judged-pool nDCG@k using completed human labels.

This is NOT the official SPARK NDCG: it only measures ranking against the
supplied (typically incomplete) manually judged candidate pool.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
import json
import math
from pathlib import Path


def dcg(rels: list[int]) -> float:
    return sum((2**grade-1)/math.log2(index+2) for index,grade in enumerate(rels))


def ndcg_on_judged_pool(rels: list[int], k: int) -> float:
    ideal=dcg(sorted(rels,reverse=True)[:k])
    return round(dcg(rels[:k])/ideal,6) if ideal>0 else 0.0


def evaluate(rows: list[dict], k: int) -> list[dict]:
    grouped=defaultdict(list)
    for row in rows:
        grouped[(row['query_id'],row['query'])].append(row)
    result=[]
    for (qid,query),records in sorted(grouped.items()):
        records.sort(key=lambda x:int(x['rank']))
        labels=[r['relevance_0_to_3'].strip() for r in records]
        judged=sum(bool(label) for label in labels)
        needed=min(k,len(records))
        top_labeled=all(label for label in labels[:needed])
        complete=all(labels)
        entry={'query_id':qid,'query':query,'total_pool':len(records),
               'judged':judged,'top_k_fully_judged':top_labeled,
               'entire_candidate_pool_judged':complete,'judged_pool_ndcg_at_k':None}
        if complete and records:
            vals=[]
            for label in labels:
                try: score=int(label)
                except ValueError: raise ValueError(f'{qid}: relevance must be integer 0–3')
                if score not in (0,1,2,3): raise ValueError(f'{qid}: relevance must be 0–3')
                vals.append(score)
            entry['judged_pool_ndcg_at_k']=ndcg_on_judged_pool(vals,k)
        result.append(entry)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,default=Path('/reports/relevance_judgments_template.csv'))
    p.add_argument('--k',type=int,default=10)
    p.add_argument('--report',type=Path,default=Path('/reports/relevance_diagnostics.json'))
    args=p.parse_args()
    if not 1<=args.k<=100:p.error('k must be between 1 and 100')
    with args.input.open(newline='',encoding='utf-8-sig') as f:
        rows=list(csv.DictReader(f))
    report={'metric':'judged-pool NDCG, NOT NIH challenge NDCG',
            'k':args.k,'results':evaluate(rows,args.k)}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    main()
