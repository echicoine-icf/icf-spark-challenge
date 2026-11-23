# SPARK relevance/ranking v2.6.2

Fixes broad-query relevance judging.

Problem:
- strict queries carried `required_evidence` snippets into MedGemma;
- broad lexical queries carried only title/short description/source labels;
- therefore the judge could reject all broad candidates without seeing the text that retrieved them.

Change:
- fetch top 2 original-corpus evidence excerpts for every selected broad candidate in one bulk SQL query;
- attach those excerpts as `broad_evidence`;
- pass `broad_evidence`, matched source labels, and evidence-hit count to the relevance judge;
- append live prompt guidance explaining how to use broad evidence;
- do not change the grade-2 relevance cutoff.

No enrichment is used.
