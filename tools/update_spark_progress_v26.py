from pathlib import Path

p = Path("SPARK_PROGRESS.md")
if not p.exists():
    raise SystemExit("SPARK_PROGRESS.md not found; run from SPARK repo root.")
text = p.read_text(encoding="utf-8")
marker = "## Task 2 relevance judge / ranking explanations (v2.6)"
if marker in text:
    print("SPARK_PROGRESS.md already contains v2.6 relevance-ranking entry.")
else:
    block = r'''

## Task 2 relevance judge / ranking explanations (v2.6)

- Removed the interactive chat's fixed eight-result cap as the relevance boundary.
- Added registered MedGemma tasks `task2_relevance_judge` and `task2_ranking_explainer` with live-reload prompt files and prompt hashes.
- Deterministic original-corpus retrieval remains the candidate generator. MedGemma grades each reviewed candidate on a 0-3 relevance scale and reranks only from supplied original-corpus evidence.
- Default normal-result threshold is grade 2+ (`Relevant` or `Strong Match`). Grade 0-1 candidates are excluded from normal interactive results when judging succeeds.
- The API reports judged count, below-threshold count, whether an irrelevance boundary was encountered, and whether the configurable candidate-review window was exhausted first.
- Ranking results preserve deterministic retrieval rank and the final AI-assisted rank. Opening a ranked study invokes MedGemma again through `task2_ranking_explainer` for a formal evidence-grounded explanation.
- `/api/v1/task2/rank` keeps deterministic ranking by default and accepts `ai_rerank=true` for controlled comparisons/NDCG evaluation.
- One explicit required characteristic is now valid; broad one-topic searches are no longer required to provide 2-8 characteristics.
- Enrichment remains a separate, non-baseline prompt task and does not supply study evidence to this ranking layer.
'''
    p.write_text(text.rstrip() + block + "\n", encoding="utf-8")
    print("SPARK_PROGRESS.md updated for v2.6.")
