from app import umls_api


def test_umls_search_exact_then_normalized_words(monkeypatch):
    calls = []

    def cached(term, search_type, semantic_group, limit):
        calls.append((term, search_type, semantic_group, limit))
        if search_type == "exact":
            return tuple()
        return ({
            "ontology": "UMLS", "ontology_release": "2026AB", "concept_id": "UMLS:C0000001",
            "label": "Synthetic concept", "semantic_types": ["Finding"], "match_basis": "umls_normalizedWords",
        },)

    monkeypatch.setattr(umls_api, "_cached_search", cached)
    result = umls_api.search(" synthetic   concept ", limit=3, semantic_group="Disorders")
    assert calls == [
        ("synthetic concept", "exact", "Disorders", 3),
        ("synthetic concept", "normalizedWords", "Disorders", 3),
    ]
    assert result[0]["concept_id"] == "UMLS:C0000001"


def test_umls_configured_requires_key(monkeypatch):
    monkeypatch.delenv("UMLS_API_KEY", raising=False)
    assert umls_api.configured() is False
    monkeypatch.setenv("UMLS_API_KEY", "test-key")
    assert umls_api.configured() is True
