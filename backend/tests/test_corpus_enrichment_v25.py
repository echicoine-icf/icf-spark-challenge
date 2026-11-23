import json
from app import corpus_enrichment


def test_enrichment_keeps_original_id_and_provenance(monkeypatch):
    result_json = json.dumps({
        "record_id": "phs000001.v1.p1",
        "enriched_title": "Example",
        "enriched_summary": "Grounded summary",
        "characteristics": [],
        "concepts": [],
        "uncertainty": [],
    })
    meta = {"task": "corpus_enrichment", "sha256": "abc", "model": "medgemma1.5"}
    monkeypatch.setattr(corpus_enrichment, "run_prompt_task", lambda *args, **kwargs: (result_json, meta))
    out = corpus_enrichment.enrich_record(
        record_id="phs000001.v1.p1",
        original_fragments=[{"pointer": "study", "text": "source"}],
    )
    assert out["source_layer"] == "enriched"
    assert out["original_record_id"] == "phs000001.v1.p1"
    assert out["ai_provenance"]["task"] == "corpus_enrichment"
