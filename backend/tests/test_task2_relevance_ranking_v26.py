import json

from app import concierge
from app.task2_baseline import normalize_requirements


def _search_rows():
    return {
        "mode": "original_lexical",
        "ranking": "deterministic",
        "results": [
            {"study_id": "phs000001.v1.p1", "original_title": "A", "original_description": "A desc", "matched_original_sources": ["study"], "match_type": "original_only"},
            {"study_id": "phs000002.v1.p1", "original_title": "B", "original_description": "B desc", "matched_original_sources": ["study"], "match_type": "original_only"},
            {"study_id": "phs000003.v1.p1", "original_title": "C", "original_description": "C desc", "matched_original_sources": ["study"], "match_type": "original_only"},
            {"study_id": "phs000004.v1.p1", "original_title": "D", "original_description": "D desc", "matched_original_sources": ["study"], "match_type": "original_only"},
        ],
    }


def test_single_requirement_is_valid():
    assert normalize_requirements([{"term": "heart failure", "role": "disease"}]) == [
        {"term": "heart failure", "role": "disease"}
    ]


def test_relevance_judge_filters_and_reranks(monkeypatch):
    response = {
        "grades": [
            {"g": 2, "c": 0.98},
            {"g": 3, "c": 0.80},
            {"g": 1, "c": 0.95},
            {"g": 0, "c": 0.99},
        ]
    }
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *args, **kwargs: json.dumps(response))
    result = concierge._apply_relevance_judge(
        "heart failure", {"query": "heart failure", "requirements": [], "excluded_terms": []}, _search_rows(),
        relevance_tier=3,
    )
    assert [row["study_id"] for row in result["results"]] == ["phs000002.v1.p1", "phs000001.v1.p1"]
    assert [row["rank"] for row in result["results"]] == [1, 2]
    assert result["relevance_judging"]["threshold_grade"] == 2
    assert result["relevance_judging"]["marginal_or_irrelevant_count"] == 2
    assert result["relevance_judging"]["boundary_reached"] is True


def test_incomplete_judge_falls_back_without_dropping_results(monkeypatch):
    response = {
        "grades": [
            {"g": 3, "c": 0.9}
        ]
    }
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *args, **kwargs: json.dumps(response))
    result = concierge._apply_relevance_judge(
        "heart failure", {"query": "heart failure", "requirements": [], "excluded_terms": []}, _search_rows(),
        relevance_tier=3,
    )
    assert len(result["results"]) == 4
    assert result["relevance_judging"]["applied"] is False
    assert result["relevance_judging"]["fallback"] == "deterministic_ranking"


def test_obsolete_multi_constraint_reply_recovers_broad_query(monkeypatch):
    reply = {
        "action": "reply",
        "query": "",
        "requirements": [],
        "excluded_terms": [],
        "required_terms": [],
        "study_id": "",
        "message": "Provide 2–8 required characteristics",
    }
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *args, **kwargs: json.dumps(reply))
    plan = concierge._plan("I'm looking for datasets about heart failure", [])
    assert plan["action"] == "search_studies"
    assert plan["query"] == "heart failure"


def test_prevalence_search_recovers_population_and_geography_from_broad_plan(monkeypatch):
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *args, **kwargs: json.dumps({
        "action": "search_studies", "query": "cancer", "requirements": [],
    }))
    plan = concierge._plan("What percentage of women have cancer in the Congo?", [])
    assert plan["query"] == ""
    assert {item["term"]: item["role"] for item in plan["requirements"]} == {
        "women": "population", "cancer": "disease", "Congo": "population",
    }


def test_ranking_explanation_selects_supplied_excerpt_and_removes_control_tags(monkeypatch):
    monkeypatch.setattr(concierge, "_inspect_original", lambda _: {"study": {"original_title": "Cohort study"}})

    def explain(task, messages, *, schema, input_ref):
        assert task == "task2_ranking_explainer"
        assert "sections" in schema["properties"]
        assert json.loads(messages[0]["content"])["evidence_options"][0]["excerpt"] == "Fasting glucose measurement"
        return (
            "<unused94>thought\nHidden text<unused95>"
            '{"sections":[{"heading":"Research fit","points":[{"text":"<unused94>It includes a fasting glucose variable.","evidence_indices":[0]}]}]}'
            "<unused95>"
        )

    monkeypatch.setattr(concierge, "_run_ai_task", explain)
    result = concierge.task2_ranking_explanation(concierge.RankingExplanationRequest(
        study_id="phs000001.v1.p1", research_intent="Fasting glucose measurements", rank=1,
        evidence=[{"source_type": "variable_dictionary", "excerpt": "<<Fasting glucose>> measurement"}],
    ))

    assert result["breakdown"]["sections"][0]["points"][0] == {
        "text": "It includes a fasting glucose variable.",
        "evidence": [{"source_type": "variable_dictionary", "excerpt": "Fasting glucose measurement"}],
    }
    assert "Hidden text" not in result["explanation"]
    assert "<unused" not in result["explanation"]


def test_invalid_ranking_explanation_uses_source_fallback(monkeypatch):
    monkeypatch.setattr(concierge, "_inspect_original", lambda _: {"study": {"original_description": "Original study description."}})
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *args, **kwargs: '{"match":"Fabrication","evidence_index":9,"caveat":""}')

    result = concierge.task2_ranking_explanation(concierge.RankingExplanationRequest(
        study_id="phs000001.v1.p1", research_intent="Glucose measurements", rank=1,
    ))

    assert result["breakdown"]["evidence"]["excerpt"] == "Original study description."
    assert result["breakdown"]["match"] == ""
    assert "Fabrication" not in result["explanation"]


def test_analysis_tiers_load_progressively_more_original_evidence(monkeypatch):
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, params=None):
            self.statement = " ".join(statement.split())

        def fetchone(self):
            assert "FROM studies" in self.statement
            return {
                "original_title": "Complete study title",
                "original_description": "Complete original study description that is longer than the retrieval packet.",
                "source_record": {"fields": {"study_disease_mesh_name": ["Hypertension"]}},
            }

        def fetchall(self):
            if "FROM phenotype_datasets" in self.statement:
                return [{"dataset_id": "pht1", "original_title": "Phenotype", "original_description": "Phenotype dataset description"}]
            if "FROM genotype_datasets" in self.statement:
                return [{"dataset_id": "phg1", "original_title": "Genotype", "original_description": "Genotype dataset description"}]
            if "FROM phenotype_variables" in self.statement:
                return [
                    {"dataset_id": "pht1", "variable_id": "phv1", "variable_name": "SBP", "variable_description": "Systolic blood pressure"},
                    {"dataset_id": "pht1", "variable_id": "phv2", "variable_name": "DBP", "variable_description": "Diastolic blood pressure"},
                ]
            raise AssertionError(self.statement)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(concierge, "_db_connection", lambda: Connection())
    reviewed_batches = []

    def analyze_variables(task, messages, **kwargs):
        assert task == "task2_study_explainer"
        payload = json.loads(messages[0]["content"])
        reviewed_batches.extend(payload["variables"])
        return "Variable batch supports blood-pressure measurement."

    monkeypatch.setattr(concierge, "_run_ai_task", analyze_variables)
    row = {
        "study_id": "phs000001.v1.p1",
        "original_title": "Short title",
        "original_description": "Short desc",
        "matched_original_sources": ["study"],
        "match_type": "original_only",
    }

    tier1 = concierge._analysis_candidates("blood pressure", {}, [row], 1)[0]
    assert tier1["description"] == "Short desc"
    assert tier1["analysis_scope"] == "title_and_retrieval_excerpts"
    assert "original_study_metadata" not in tier1

    tier2 = concierge._analysis_candidates("blood pressure", {}, [row], 2)[0]
    assert tier2["description"].startswith("Complete original study description")
    assert "original_study_metadata" not in tier2

    tier3 = concierge._analysis_candidates("blood pressure", {}, [row], 3)[0]
    assert tier3["original_study_metadata"]["fields"]["study_disease_mesh_name"] == ["Hypertension"]
    assert "phenotype_datasets" not in tier3

    tier4 = concierge._analysis_candidates("blood pressure", {}, [row], 4)[0]
    assert tier4["phenotype_datasets"][0]["dataset_id"] == "pht1"
    assert tier4["genotype_datasets"][0]["dataset_id"] == "phg1"
    assert "variable_analysis" not in tier4

    tier5 = concierge._analysis_candidates("blood pressure", {}, [row], 5)[0]
    assert tier5["variable_record_count"] == 2
    assert len(reviewed_batches) == 2
    assert tier5["variable_analysis"][0]["count"] == 2
    assert tier5["analysis_scope"] == "study_datasets_and_all_phenotype_variables_batched"


def test_chat_relevance_tier_reaches_relevance_judge(monkeypatch):
    plan = {"action": "search_studies", "query": "hypertension", "requirements": [], "excluded_terms": []}
    monkeypatch.setattr(concierge, "_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(concierge, "_search_original", lambda *args, **kwargs: {"results": [{"study_id": "phs000001.v1.p1"}]})
    seen = {}

    def judge(message, parsed_plan, data, *, progress, relevance_tier, cutoff_grade, analysis_effort):
        seen["tier"] = relevance_tier
        seen["cutoff"] = cutoff_grade
        seen["effort"] = analysis_effort
        return {**data, "relevance_judging": {"applied": True}}

    monkeypatch.setattr(concierge, "_apply_relevance_judge", judge)
    monkeypatch.setattr(concierge, "_summarize", lambda *args, **kwargs: "ok")
    concierge._chat(concierge.ChatRequest(message="hypertension", relevance_tier=4, relevance_cutoff_grade=3))
    assert seen["tier"] == 4
    assert seen["cutoff"] == 3


def test_ranking_explanation_reuses_result_analysis_tier(monkeypatch):
    monkeypatch.setattr(concierge, "_inspect_original", lambda _: {"study": {"original_title": "Study", "original_description": "Description"}})
    seen = {}

    def analysis(message, plan, rows, tier):
        seen["tier"] = tier
        return [{"study_id": rows[0]["study_id"], "analysis_tier": tier}]

    monkeypatch.setattr(concierge, "_analysis_candidates", analysis)
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *args, **kwargs: json.dumps({
        "sections": [{"heading": "Research fit", "points": [{"text": "Supported.", "evidence_indices": []}]}]
    }))
    concierge.task2_ranking_explanation(concierge.RankingExplanationRequest(
        study_id="phs000001.v1.p1", research_intent="hypertension", rank=1, relevance_tier=5,
    ))
    assert seen["tier"] == 5



def test_adaptive_relevance_stops_easy_study_and_escalates_ambiguous_one(monkeypatch):
    rows = _search_rows()["results"][:2]
    search = {"mode": "original_lexical", "ranking": "deterministic", "results": rows}
    calls = []

    def analysis(message, plan, stage_rows, tier):
        return [{"study_id": row["study_id"], "title": row.get("original_title", ""), "analysis_tier": tier,
                 "analysis_scope": concierge.ANALYSIS_TIER_SCOPES[tier]} for row in stage_rows]

    def judge(message, plan, search_data, candidates, tier, progress):
        calls.append((tier, [candidate["study_id"] for candidate in candidates]))
        if tier == 1:
            return [
                {"grade": 3, "confidence": 0.98},
                {"grade": 2, "confidence": 0.95},
            ]
        assert tier == 2
        return [{"grade": 2, "confidence": 0.95}]

    monkeypatch.setattr(concierge, "_analysis_candidates", analysis)
    monkeypatch.setattr(concierge, "_judge_analysis_stage", judge)
    progress_events = []
    result = concierge._apply_relevance_judge(
        "hypertension", {"query": "hypertension", "requirements": [], "excluded_terms": []}, search,
        progress=lambda stage, message, **details: progress_events.append((stage, message, details)),
    )

    by_id = {row["study_id"]: row for row in result["results"]}
    assert by_id[rows[0]["study_id"]]["relevance_tier"] == 1
    assert by_id[rows[1]["study_id"]]["relevance_tier"] == 2
    assert calls == [(1, [rows[0]["study_id"], rows[1]["study_id"]]), (2, [rows[1]["study_id"]])]
    assert result["relevance_judging"]["analysis_mode"] == "adaptive"
    assert result["relevance_judging"]["analysis_depth_counts"] == {"1": 1, "2": 1}
    assert any(event[2].get("analysis_action") == "escalate" for event in progress_events if event[0] == "analysis")


def test_adaptive_relevance_forces_constrained_query_to_full_metadata(monkeypatch):
    row = _search_rows()["results"][:1]
    search = {"mode": "strict_multi_term", "ranking": "deterministic", "results": row}
    seen_tiers = []

    monkeypatch.setattr(concierge, "_analysis_candidates", lambda message, plan, rows, tier: [
        {"study_id": rows[0]["study_id"], "analysis_tier": tier, "analysis_scope": concierge.ANALYSIS_TIER_SCOPES[tier]}
    ])

    def judge(message, plan, search_data, candidates, tier, progress):
        seen_tiers.append(tier)
        return [{"grade": 3, "confidence": 0.99}]

    monkeypatch.setattr(concierge, "_judge_analysis_stage", judge)
    result = concierge._apply_relevance_judge(
        "adult asthma with WGS",
        {"query": "", "requirements": [{"term": "adult", "role": "population"}, {"term": "asthma", "role": "disease"}, {"term": "WGS", "role": "data_modality"}], "excluded_terms": []},
        search, analysis_effort=5,
    )
    assert seen_tiers == [3]
    assert result["results"][0]["relevance_tier"] == 3
    assert result["results"][0]["analysis_path"][0]["tier"] == 3


def test_chat_defaults_to_adaptive_relevance(monkeypatch):
    plan = {"action": "search_studies", "query": "hypertension", "requirements": [], "excluded_terms": []}
    monkeypatch.setattr(concierge, "_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(concierge, "_search_original", lambda *args, **kwargs: {"results": [{"study_id": "phs000001.v1.p1"}]})
    seen = {}

    def judge(message, parsed_plan, data, *, progress, relevance_tier, cutoff_grade, analysis_effort):
        seen["tier"] = relevance_tier
        seen["cutoff"] = cutoff_grade
        seen["effort"] = analysis_effort
        return {**data, "relevance_judging": {"applied": True}}

    monkeypatch.setattr(concierge, "_apply_relevance_judge", judge)
    monkeypatch.setattr(concierge, "_summarize", lambda *args, **kwargs: "ok")
    concierge._chat(concierge.ChatRequest(message="hypertension"))
    assert seen["tier"] is None
    assert seen["cutoff"] == 2
    assert seen["effort"] == 2


def test_adaptive_relevance_cutoff_grade_three_is_strict_and_drives_deeper_review(monkeypatch):
    rows = _search_rows()["results"][:2]
    search = {"mode": "original_lexical", "ranking": "deterministic", "results": rows}
    calls = []

    monkeypatch.setattr(concierge, "_analysis_candidates", lambda message, plan, stage_rows, tier: [
        {"study_id": row["study_id"], "analysis_tier": tier, "analysis_scope": concierge.ANALYSIS_TIER_SCOPES[tier]}
        for row in stage_rows
    ])

    def judge(message, plan, search_data, candidates, tier, progress):
        calls.append((tier, [candidate["study_id"] for candidate in candidates]))
        if tier == 1:
            return [{"grade": 3, "confidence": 0.99}, {"grade": 2, "confidence": 0.99}]
        if tier == 2:
            return [{"grade": 3, "confidence": 0.99}, {"grade": 2, "confidence": 0.99}]
        assert tier == 3
        return [{"grade": 2, "confidence": 0.99}]

    monkeypatch.setattr(concierge, "_judge_analysis_stage", judge)
    result = concierge._apply_relevance_judge(
        "hypertension", {"query": "hypertension", "requirements": [], "excluded_terms": []}, search,
        cutoff_grade=3, analysis_effort=5,
    )

    assert [row["study_id"] for row in result["results"]] == [rows[0]["study_id"]]
    assert result["relevance_judging"]["threshold_grade"] == 3
    assert calls == [
        (1, [rows[0]["study_id"], rows[1]["study_id"]]),
        (2, [rows[0]["study_id"], rows[1]["study_id"]]),
        (3, [rows[1]["study_id"]]),
    ]


def test_adaptive_relevance_cutoff_grade_one_includes_marginal_matches(monkeypatch):
    rows = _search_rows()["results"][:2]
    search = {"mode": "original_lexical", "ranking": "deterministic", "results": rows}

    monkeypatch.setattr(concierge, "_analysis_candidates", lambda message, plan, stage_rows, tier: [
        {"study_id": row["study_id"], "analysis_tier": tier, "analysis_scope": concierge.ANALYSIS_TIER_SCOPES[tier]}
        for row in stage_rows
    ])

    def judge(message, plan, search_data, candidates, tier, progress):
        if tier == 1:
            return [{"grade": 1, "confidence": 0.99}, {"grade": 0, "confidence": 0.99}]
        assert tier == 2
        return [{"grade": 1, "confidence": 0.99}]

    monkeypatch.setattr(concierge, "_judge_analysis_stage", judge)
    result = concierge._apply_relevance_judge(
        "hypertension", {"query": "hypertension", "requirements": [], "excluded_terms": []}, search,
        cutoff_grade=1,
    )

    assert [row["study_id"] for row in result["results"]] == [rows[0]["study_id"]]
    assert result["results"][0]["relevance_grade"] == 1
    assert result["relevance_judging"]["threshold_grade"] == 1


def test_analysis_effort_one_stops_high_confidence_cutoff_match_on_fast_pass(monkeypatch):
    rows = _search_rows()["results"][:1]
    search = {"mode": "original_lexical", "ranking": "deterministic", "results": rows}
    calls = []
    monkeypatch.setattr(concierge, "_analysis_candidates", lambda message, plan, stage_rows, tier: [
        {"study_id": row["study_id"], "analysis_tier": tier, "analysis_scope": concierge.ANALYSIS_TIER_SCOPES[tier]}
        for row in stage_rows
    ])
    def judge(message, plan, search_data, candidates, tier, progress):
        calls.append(tier)
        return [{"grade": 2, "confidence": 0.93}]
    monkeypatch.setattr(concierge, "_judge_analysis_stage", judge)
    result = concierge._apply_relevance_judge(
        "hypertension", {"query": "hypertension", "requirements": [], "excluded_terms": []}, search,
        cutoff_grade=2, analysis_effort=1,
    )
    assert calls == [1]
    assert result["results"][0]["relevance_tier"] == 1
    assert result["relevance_judging"]["analysis_effort"] == 1
    assert result["relevance_judging"]["analysis_effort_max_tier"] == 1


def test_analysis_effort_two_balanced_caps_constrained_query_at_description_when_stable(monkeypatch):
    rows = _search_rows()["results"][:1]
    search = {"mode": "strict_multi_term", "ranking": "deterministic", "results": rows}
    calls = []
    monkeypatch.setattr(concierge, "_analysis_candidates", lambda message, plan, stage_rows, tier: [
        {"study_id": row["study_id"], "analysis_tier": tier, "analysis_scope": concierge.ANALYSIS_TIER_SCOPES[tier]}
        for row in stage_rows
    ])
    def judge(message, plan, search_data, candidates, tier, progress):
        calls.append(tier)
        return [{"grade": 3, "confidence": 0.99}]
    monkeypatch.setattr(concierge, "_judge_analysis_stage", judge)
    result = concierge._apply_relevance_judge(
        "adult asthma with WGS",
        {"query": "", "requirements": [{"term": "adult"}, {"term": "asthma"}, {"term": "WGS"}], "excluded_terms": []},
        search, cutoff_grade=2, analysis_effort=2,
    )
    assert calls == [2]
    assert result["results"][0]["relevance_tier"] == 2
    assert result["relevance_judging"]["analysis_effort_label"] == "Balanced"


def test_analysis_effort_five_preserves_exhaustive_constrained_minimum(monkeypatch):
    rows = _search_rows()["results"][:1]
    search = {"mode": "strict_multi_term", "ranking": "deterministic", "results": rows}
    calls = []
    monkeypatch.setattr(concierge, "_analysis_candidates", lambda message, plan, stage_rows, tier: [
        {"study_id": row["study_id"], "analysis_tier": tier, "analysis_scope": concierge.ANALYSIS_TIER_SCOPES[tier]}
        for row in stage_rows
    ])
    def judge(message, plan, search_data, candidates, tier, progress):
        calls.append(tier)
        return [{"grade": 3, "confidence": 0.99}]
    monkeypatch.setattr(concierge, "_judge_analysis_stage", judge)
    result = concierge._apply_relevance_judge(
        "adult asthma with WGS",
        {"query": "", "requirements": [{"term": "adult"}, {"term": "asthma"}, {"term": "WGS"}], "excluded_terms": []},
        search, cutoff_grade=2, analysis_effort=5,
    )
    assert calls == [3]
    assert result["results"][0]["relevance_tier"] == 3
    assert result["relevance_judging"]["analysis_effort_label"] == "Exhaustive"
