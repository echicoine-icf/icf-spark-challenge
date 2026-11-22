#!/usr/bin/env python3
"""Score a FULLY human-judged, common-pool M6 search ranking diagnostic.

Local judged-pool NDCG@10 is not official challenge NDCG or clinical accuracy.
No LLM or web calls. Reviewers must label ALL pooled studies before use.
"""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import json
import math
from collections import defaultdict
from pathlib import Path


def read_csv(path:Path)->list[dict]:
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))


def ndcg(grades:list[int], ideal_grades:list[int], k:int=10)->float:
    def dcg(values):
        return sum((2**grade-1)/math.log2(rank+2) for rank,grade in enumerate(values[:k]))
    ideal=dcg(sorted(ideal_grades,reverse=True))
    return round(dcg(grades)/ideal,5) if ideal>0 else 0.0


def score(review_rows:list[dict],rankings:list[dict])->dict:
    judgments=defaultdict(dict)
    for row in review_rows:
        scenario=row.get('scenario','').strip()
        sid=row.get('study_id','').strip()
        label=row.get('relevance_0_to_3','').strip()
        if not scenario or not sid:
            raise ValueError('Review row has missing scenario or study ID')
        if sid in judgments[scenario]:
            raise ValueError(f'Duplicate judgment for {scenario}/{sid}')
        if label not in {'0','1','2','3'}:
            raise ValueError(f'Incomplete or invalid human grade for {scenario}/{sid}; fill ALL rows before scoring')
        judgments[scenario][sid]=int(label)
    if not judgments:
        raise ValueError('No human-reviewed relevance rows')
    groups=defaultdict(list)
    for row in rankings:
        scenario=row.get('scenario','').strip()
        variant=row.get('variant','').strip()
        sid=row.get('study_id','').strip()
        if not (scenario and variant and sid):
            raise ValueError('Ranking lacks study ID; cannot score unknown studies')
        if sid not in judgments.get(scenario,{}):
            raise ValueError(f'Returned study {scenario}/{sid} lacks a pooled review label')
        try:
            rank=int(row['rank'])
        except (KeyError,ValueError) as e:
            raise ValueError(f'Invalid ranking position: {row}') from e
        groups[(scenario,variant)].append((rank,sid))
    summaries=[]
    for (scenario,variant),ranked in sorted(groups.items()):
        if len({sid for _,sid in ranked})!=len(ranked):
            raise ValueError(f'Duplicate ranked study for {scenario}/{variant}')
        if len({rank for rank,_ in ranked})!=len(ranked) or min(rank for rank,_ in ranked)<1:
            raise ValueError(f'Invalid/duplicate rank numbers for {scenario}/{variant}')
        ranked.sort(key=lambda a:a[0])
        if [r for r,_ in ranked] != list(range(1,len(ranked)+1)):
            raise ValueError(f'Nonconsecutive ranks in {scenario}/{variant}')
        grades=[judgments[scenario][sid] for _,sid in ranked]
        ideal=list(judgments[scenario].values())
        positive=sum(g>=2 for g in grades[:10])
        summaries.append({'scenario':scenario,'variant':variant,'ndcg_at_10_judged_pool':ndcg(grades,ideal),
                          'positive_grade_ge_2_at_10':positive, 'precision_at_10_grade_ge_2':round(positive/10,4),
                          'returned_count':len(ranked),'judged_pool_count':len(ideal)})
    return {'assessment':'shared_pooled_human_judgments_not_official_nih_evaluation',
            'all_pooled_studies_judged':True,'total_judged_study_query_pairs':sum(len(v) for v in judgments.values()),
            'rankings':summaries,'caveat':'Only pooled studies can contribute to ideal DCG; cannot measure relevant unreturned studies; document reviewer identity/criteria separately.'}


def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--review',type=Path,required=True,help='Fully human-reviewed copy of *_review_BLANK.csv')
    p.add_argument('--rankings',type=Path,required=True,help='Matching *_rankings.csv from same timestamp')
    p.add_argument('--output',type=Path,required=True,help='New JSON path')
    args=p.parse_args(argv)
    if args.output.resolve() in (args.review.resolve(),args.rankings.resolve()):
        p.error('Cannot overwrite source inputs')
    if args.output.exists():
        p.error('Output already exists; supply a new filename')
    try:
        result=score(read_csv(args.review),read_csv(args.rankings))
    except ValueError as e:
        p.error(str(e))
    result['generated_at_utc']=dt.datetime.now(dt.timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print('SAVED',args.output)
    print(json.dumps(result,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
