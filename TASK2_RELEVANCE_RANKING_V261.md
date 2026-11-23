# Task 2 relevance/ranking v2.6.1

Latency/reliability correction for v2.6.

- Relevance judge remains one MedGemma batch call, but output is now a compact ordered vector of grade + confidence only.
- Candidate evidence sent to the judge is substantially smaller.
- Per-candidate prose is deferred to `task2_ranking_explainer` when the researcher opens a result.
- The automatic post-search `task2_result_analyst` call is disabled by default to remove one sequential model call from interactive search. Set `SPARK_AUTO_RESULT_ANALYST=1` to restore it for experiments.
- Judge elapsed milliseconds are returned in `search.relevance_judging.elapsed_ms`.
- If AI judging fails, deterministic retrieval is preserved and the UI labels those results `Retrieved` rather than pretending an AI relevance grade exists.
