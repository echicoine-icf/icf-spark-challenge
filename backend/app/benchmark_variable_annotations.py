"""Two-mode annotated-vs-original metadata study retrieval diagnostic.

Not relevance judgments: annotation approvals must come from recorded human
review, and zero approvals means no expected new matches. Never infer exposure
or participant-level measurement eligibility from study metadata.
"""
import json
import os
from statistics import median
from time import perf_counter
from pathlib import Path
from urllib.request import Request, urlopen

CASES = [
    [ {'term':'type 2 diabetes mellitus','expected_type':'disease'},
      {'term':'metformin','expected_type':'medication'},
      {'term':'2345-7','expected_type':'laboratory_measurement'} ],
    [ {'term':'colorectal cancer','expected_type':'disease'},
      {'term':'blood glucose','expected_type':'laboratory_measurement'} ],
]


def search(base_url, requirements, annotated):
    payload = json.dumps({'requirements':requirements,'match_mode':'ranked',
                          'limit':10,'include_reviewed_annotations':annotated}).encode()
    request=Request(base_url+'/api/v1/search/typed',data=payload,
                    headers={'Content-Type':'application/json'},method='POST')
    started=perf_counter()
    with urlopen(request,timeout=120) as resp:
        data=json.load(resp)
    return data, round((perf_counter()-started)*1000,1)


def main():
    base=os.getenv('SPARK_API_URL','http://api:8000').rstrip('/')
    cases=[]
    for requirements in CASES:
        times_original=[]
        times_reviewed=[]
        for _ in range(2):
            original, elapsed=search(base, requirements, False)
            times_original.append(elapsed)
            enhanced, elapsed=search(base, requirements, True)
            times_reviewed.append(elapsed)
        def summarize(payload):
            return {'study_ids':[r['study_id'] for r in payload['results']],
                'all_requirements_supported_ids':[r['study_id'] for r in payload['results']
                                                  if r['all_requirements_supported']],
                'annotation_evidence_count':sum(f['reviewed_annotation_count']
                   for r in payload['results'] for f in r['requirements']),
                'reviewed_annotation_pool_truncated':payload.get('reviewed_annotation_pool_truncated',False)}
        case={'requirements':requirements, 'original_only':{
                  **summarize(original),'median_ms':round(median(times_original),1)},
              'with_reviewed_annotations':{
                  **summarize(enhanced),'median_ms':round(median(times_reviewed),1)}}
        cases.append(case)
        print(json.dumps(case,ensure_ascii=False),flush=True)
    report={'description':'Bounded study-level exploratory comparison; no judged relevance',
            'iterations':2,
            'limitations':'Unjudged; small samples; query modes can differ; no NDCG, recall, or precision.',
            'cases':cases}
    target=Path('/reports/variable_annotation_retrieval_baseline.json')
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('SAVED:',target)

if __name__=='__main__': main()
