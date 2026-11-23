# Task 2 relevance/ranking v2.6

Interactive search now uses:

1. MedGemma query planner/follow-up router.
2. Deterministic original dbGaP candidate retrieval.
3. MedGemma relevance judge over only supplied original-corpus evidence.
4. Grade-based cutoff: 3=Strong Match, 2=Relevant, 1=Marginal, 0=Irrelevant. Normal results require grade >=2.
5. Final rank is explicit in result cards. Deterministic retrieval rank is retained for provenance.
6. Opening a ranked study calls the dedicated ranking-explainer prompt and displays a formal grounded explanation.

The interactive candidate review window defaults to 32 and is configurable with `SPARK_CHAT_CANDIDATE_LIMIT`. If the window is exhausted before any candidate falls below the threshold, the API/UI reports that the true irrelevance boundary has not yet been reached.

The evaluation `/api/v1/task2/rank` endpoint remains deterministic by default. Send `"ai_rerank": true` to compare the AI-assisted ranking against the deterministic baseline.
