"""Task 2 baseline contract tests; no live database required."""
from app import task2_baseline as t2


def test_requirement_roles_and_deduplication():
    rows = t2.normalize_requirements([
        {"term": "lung adenocarcinoma", "role": "disease"},
        {"term": "smoking exposure", "role": "exposure"},
        {"term": "whole genome sequencing", "role": "data_modality"},
    ])
    assert [r["role"] for r in rows] == ["disease", "exposure", "data_modality"]


def test_invalid_requirement_role_is_rejected():
    try:
        t2.normalize_requirements([
            {"term": "lung cancer", "role": "disease"},
            {"term": "magic", "role": "unsupported"},
        ])
    except ValueError as exc:
        assert "Unsupported" in str(exc)
    else:
        raise AssertionError("invalid role accepted")


def test_exclusions_are_bounded_and_deduplicated():
    assert t2.normalize_excluded_terms(["pediatric", " Pediatric "]) == ["pediatric"]


def test_structured_sql_never_references_enrichment():
    sql = t2.structured_sql(True)
    assert "study_enrichments" not in sql
    assert "task2_public_objects" in sql
    assert "variable_summaries" in sql
    assert "HAVING count(*)" in sql


def test_structured_sql_can_run_before_public_object_migration():
    sql = t2.structured_sql(False)
    assert "task2_public_objects" not in sql


def test_evidence_exposes_original_aggregate_summary_fields():
    sql = t2.evidence_sql(False)
    for field in ("total_n", "total_nulls", "total_cases", "total_controls"):
        assert field in sql


def test_role_qualification_blocks_generic_documents_for_data_modality():
    assert "document" not in t2.qualifying_sources("data_modality")
    assert "genotype_dataset" in t2.qualifying_sources("data_modality")


def test_role_qualification_blocks_generic_documents_for_population_and_disease():
    assert "document" not in t2.qualifying_sources("population")
    assert "document" not in t2.qualifying_sources("disease")


def test_broad_sql_has_study_title_boost():
    sql = t2.broad_sql(True)
    assert "20.0 * ts_rank_cd" in sql
    assert "original_title" in sql


def test_pediatric_exclusion_expands_to_age_synonyms():
    variants = t2.exclusion_variants("pediatric")
    assert "adolescent" in variants
    assert "youth" in variants
    assert "children" in variants


def test_exclusion_sql_uses_study_population_sources_not_incidental_documents():
    sql = t2._exclusion_sql(True)
    assert "FROM studies" in sql
    assert "FROM phenotype_datasets" in sql
    assert "task2_public_objects" not in sql
    assert "variable_summaries" not in sql
    assert "phenotype_variables" not in sql


def test_structured_sql_filters_negative_requirement_context():
    sql = t2.structured_sql(True)
    assert "negative_context_re" in sql
    assert "context_excerpt" in sql
    assert "lower(h.context_excerpt)" in sql


def test_evidence_sql_filters_negative_context():
    sql = t2.evidence_sql(True)
    assert "allow_negative_context" in sql
    assert "negative_context_re" in sql
