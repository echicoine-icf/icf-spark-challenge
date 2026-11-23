from pathlib import Path

PROMPT_MARKER = "BROAD EVIDENCE PAYLOAD"
prompt = Path("config/prompts/task2_relevance_judge.txt")
if prompt.exists():
    text = prompt.read_text(encoding="utf-8")
    if PROMPT_MARKER not in text:
        text = text.rstrip() + """

BROAD EVIDENCE PAYLOAD
- For broad one-topic searches, candidates may contain `broad_evidence`.
- `broad_evidence` contains the actual original-corpus excerpts that caused the broad candidate to be retrieved.
- Use those excerpts as primary relevance evidence; do not judge broad candidates from the shortened study description alone.
- A substantive matching study, dataset, variable, analysis, or document excerpt can establish broad-query relevance when its context supports the user's requested concept.
- Do not require `required_evidence` for a broad query; that field is primarily populated by conjunctive searches.
""" + "\n"
        prompt.write_text(text, encoding="utf-8")
        print("Appended broad-evidence guidance to live relevance-judge prompt.")
    else:
        print("Broad-evidence prompt guidance already present.")

print("v2.6.2 broad relevance evidence files are in place.")
