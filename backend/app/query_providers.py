"""Read-only, versioned knowledge sources for interactive query enrichment."""
from __future__ import annotations

import os
from typing import Literal

from app.concept_normalization import normalize_concept
from app.ontology_knowledge import normalize_term
from app.sapbert_search import model_request, semantic_resolve
from app.variable_annotations import load_approved
from app.umls_api import UMLSAPIError, api_version as umls_api_version, configured as umls_configured, search as umls_search

ProviderId = Literal["snomed", "mondo", "hpo", "loinc", "rxnorm", "sapbert", "ncit", "umls", "dbgap_annotations", "retrieved_evidence"]
EXACT_PROVIDERS = {
    # SNOMED CT is preferred first for disease/phenotype/other clinical terms.
    # MONDO/HPO remain query-side fallbacks/experiments but are not used by the
    # Enhanced View variable-normalization workbench.
    "snomed": ("SNOMED CT US", "SNOMEDCT_US", None),
    "mondo": ("MONDO", "MONDO", "disease"),
    "hpo": ("HPO", "HP", "phenotype"),
    "loinc": ("LOINC", "LOINC", "laboratory_measurement"),
    "rxnorm": ("RxNorm", "RXNORM", "medication"),
}
LEGACY_TERMINOLOGY = (*EXACT_PROVIDERS, "sapbert")
CHAT_QUERY_PROVIDERS = frozenset({"snomed", "mondo", "loinc", "rxnorm", "sapbert", "ncit", "umls", "retrieved_evidence"})
REQUIREMENT_TYPES = {
    "disease": "disease", "measurement": "laboratory_measurement",
    "medication": "medication", "exposure": "medication", "other": "auto",
}


def provider_catalog(cur, selected: set[str] | frozenset[str] | None = None) -> list[dict]:
    """Report installed data and model readiness without assuming optional tables exist."""
    def wanted(provider):
        return selected is None or provider in selected

    def option(provider, label, category, available, capabilities, *, version=None, reason=None, default_selected=None):
        selected = available if default_selected is None else bool(default_selected and available)
        return {"id": provider, "label": label, "category": category, "available": available,
                "version": version, "capabilities": capabilities, "reason": reason,
                "default_selected": selected}

    output = []
    if any(wanted(key) for key in (*EXACT_PROVIDERS, "sapbert")):
        cur.execute("SELECT to_regclass('public.ontology_active_releases') AS active,"
                    " to_regclass('public.ontology_term_embeddings') AS vectors")
        tables = cur.fetchone()
        releases = {}
        if tables["active"]:
            cur.execute("""SELECT r.ontology,r.ontology_release FROM ontology_releases r
                JOIN ontology_active_releases a USING (ontology,ontology_release) WHERE r.imported_terms > 0""")
            releases = {row["ontology"]: row["ontology_release"] for row in cur.fetchall()}
        for key, (label, namespace, _) in EXACT_PROVIDERS.items():
            if wanted(key):
                version = releases.get(namespace)
                output.append(option(key, label, "validated_terminology", bool(version), ["exact", "synonym"],
                                     version=version, reason=None if version else "No indexed active release"))
        if wanted("sapbert"):
            scopes = {}
            revision = None
            if tables["active"] and tables["vectors"]:
                try:
                    ready = model_request("/ready", timeout=2)
                    revision = ready.get("model_revision")
                    if ready.get("status") == "ready" and revision:
                        cur.execute("""SELECT DISTINCT e.ontology,e.ontology_release FROM ontology_term_embeddings e
                            JOIN ontology_active_releases a USING (ontology,ontology_release)
                            WHERE e.model_revision=%s AND e.model_repo=%s AND e.ontology IN ('MONDO','HP')""",
                                    (revision, ready.get("model_repo")))
                        scopes = {row["ontology"]: row["ontology_release"] for row in cur.fetchall()}
                except RuntimeError:
                    pass
            output.append({**option("sapbert", "SapBERT", "semantic", bool(scopes), ["review_candidates"],
                                    version=revision, reason=None if scopes else "No ready model with a matching active index"),
                           "indexed_ontologies": sorted(scopes)})

    if wanted("umls"):
        available = umls_configured()
        output.append(option(
            "umls", "UMLS Metathesaurus API", "reference", available, ["cui_candidates"],
            version=umls_api_version(),
            reason=None if available else "UMLS_API_KEY is not configured",
            default_selected=False,
        ))

    if wanted("ncit"):
        cur.execute("SELECT to_regclass('public.spark_kb_releases') AS releases,"
                    " to_regclass('public.spark_kb_synonym_terms') AS synonyms")
        tables = cur.fetchone()
        release = None
        if tables["releases"] and tables["synonyms"]:
            cur.execute("""SELECT release,redistribution_reviewed,redistribution_permitted FROM spark_kb_releases
                           WHERE source='ncit' AND active AND concept_count > 0""")
            release = cur.fetchone()
        submission = os.environ.get("SPARK_SUBMISSION_MODE", "").lower() in {"1", "true", "yes"}
        cleared = bool(release and release["redistribution_reviewed"] and release["redistribution_permitted"])
        available = bool(release) and (not submission or cleared)
        reason = None if available else ("Redistribution clearance required" if release else "No indexed active release")
        output.append(option("ncit", "NCI Thesaurus (NCIt)", "reference", available, ["reference_matches"],
                             version=release["release"] if release else None, reason=reason))

    if wanted("dbgap_annotations"):
        cur.execute("SELECT to_regclass('public.variable_concept_reviews') AS reviews,"
                    " to_regclass('public.variable_concept_candidates') AS candidates")
        tables = cur.fetchone()
        available = False
        if tables["reviews"] and tables["candidates"]:
            cur.execute("""SELECT EXISTS (
                SELECT 1 FROM variable_concept_candidates c
                JOIN ontology_active_releases a USING (ontology,ontology_release)
                JOIN phenotype_variables v USING (dataset_id,variable_id)
                JOIN LATERAL (SELECT decision,source_fingerprint FROM variable_concept_reviews
                              WHERE annotation_id=c.annotation_id ORDER BY review_id DESC LIMIT 1) r ON true
                WHERE r.decision='approved' AND r.source_fingerprint=c.source_fingerprint
                AND r.source_fingerprint=md5(coalesce(v.variable_name,'') || chr(31) ||
                    coalesce(v.variable_description,'') || chr(31) || coalesce(v.unit,'') || chr(31) ||
                    coalesce(v.reported_type,'') || chr(31) || coalesce(v.source_path,''))
            ) AS available""")
            available = cur.fetchone()["available"]
        output.append(option("dbgap_annotations", "Reviewed dbGaP annotations", "local_annotations", available,
                             ["reviewed_source_evidence"], reason=None if available else "No current approved mappings"))
    if wanted("retrieved_evidence"):
        output.append(option("retrieved_evidence", "Use search results to refine my query", "result_feedback",
                             True, ["source_supported_terms"]))
    return output


def _knowledge(term, provider, source_type, *, concept_id=None, canonical_label=None, version=None,
               score=None, validated=False, evidence=None):
    return {"term": term, "source_provider": provider, "source_type": source_type,
            "concept_id": concept_id, "canonical_label": canonical_label, "version": version,
            "semantic_score": score, "validated": validated, "selected": False, "evidence": evidence or []}


def collect_provider_knowledge(cur, terms: list[str], roles: list[str], providers: list[dict]) -> tuple[list[dict], list[dict]]:
    """Collect exact search aliases and keep all other matches as review context."""
    enabled = {item["id"]: item for item in providers if item["available"]}
    lookups, annotations = [], []
    for index, (term, role) in enumerate(zip(terms, roles, strict=True)):
        expected_type = REQUIREMENT_TYPES.get(role)
        if expected_type is None:
            continue
        exact = []
        conflicting = False
        for key, (_, namespace, concept_type) in EXACT_PROVIDERS.items():
            if key not in enabled:
                continue
            if key == "snomed":
                if expected_type not in {"auto", "disease", "phenotype"}:
                    continue
                provider_type = expected_type
            else:
                if expected_type not in {"auto", concept_type}:
                    continue
                provider_type = concept_type
            resolution = normalize_concept(cur, term, expected_type=provider_type, ontology=namespace,
                                           include_semantic=False, max_expansions=3)
            if resolution["status"] == "normalized_exact":
                # Provider order is intentional. For a disease/phenotype term,
                # prefer a precise SNOMED CT exact match; fall back to MONDO/HPO
                # only when SNOMED has no exact concept. Do not blend aliases
                # across distinct vocabularies as if they were equivalent.
                exact.append((key, resolution["normalized_concept"], resolution["expansions"]))
                break
            elif resolution["status"] in {"ambiguous_exact", "unclassified_exact", "type_mismatch"}:
                conflicting = True
                break

        entry = {"term": term, "requirement_index": index, "status": "not_found", "knowledge": [], "aliases": []}
        lookups.append(entry)
        if len(exact) == 1 and not conflicting:
            provider, concept, aliases = exact[0]
            kind = "exact_canonical" if concept["match_basis"] == "canonical_label" else "exact_synonym"
            entry["status"] = "normalized_exact"
            provenance = {"concept_id": concept["concept_id"], "canonical_label": concept["label"],
                          "version": concept["ontology_release"], "validated": True}
            entry["knowledge"].append(_knowledge(term, provider, kind, **provenance))
            for alias in aliases:
                item = _knowledge(alias, provider, "exact_canonical" if alias == concept["label"] else "exact_synonym",
                                  **provenance)
                entry["aliases"].append(item)
                entry["knowledge"].append(item)
            if "dbgap_annotations" in enabled and concept["ontology"] != "HP":
                annotations.extend({
                    **_knowledge(term, "dbgap_annotations", "reviewed_annotation", **provenance,
                                 evidence=[{"study_id": row["study_id"], "source_path": row["source_path"],
                                            "variable_id": row["variable_id"],
                                            "excerpt": row["variable_description"] or row["variable_name"]}]),
                    "requirement_index": index,
                } for row in load_approved(cur, concept["concept_id"], limit=12))
        elif exact or conflicting:
            entry["status"] = "ambiguous_exact"
        elif "sapbert" in enabled:
            namespace = "HP" if expected_type == "phenotype" else "MONDO"
            if expected_type in {"auto", "disease", "phenotype"} and namespace in enabled["sapbert"]["indexed_ontologies"]:
                try:
                    result = semantic_resolve(cur, term, ontology=namespace, limit=3)
                    for hit in result["candidates"]:
                        entry["knowledge"].append(_knowledge(
                            hit["matched_ontology_term"], "sapbert", "semantic_candidate",
                            concept_id=hit["concept_id"], canonical_label=hit["canonical_label"],
                            version=hit["ontology_release"], score=hit["cosine_similarity"],
                        ))
                except RuntimeError:
                    entry["semantic_unavailable"] = True

        if "ncit" in enabled:
            cur.execute("""WITH matches AS (
                SELECT concept_id,label,release,true AS canonical FROM spark_kb_concepts
                WHERE source='ncit' AND release=%(release)s AND lower(label)=%(term)s
                UNION ALL
                SELECT c.concept_id,c.label,c.release,false FROM spark_kb_synonym_terms t
                JOIN spark_kb_concepts c USING (source,release,concept_id)
                WHERE t.source='ncit' AND t.release=%(release)s AND t.term_normalized=%(term)s
                )
                SELECT concept_id,label,release FROM matches GROUP BY concept_id,label,release
                ORDER BY bool_or(canonical) DESC,concept_id LIMIT 6""",
                        {"release": enabled["ncit"]["version"], "term": normalize_term(term)})
            for row in cur.fetchall():
                entry["knowledge"].append(_knowledge(
                    term, "ncit", "reference_match", concept_id=row["concept_id"],
                    canonical_label=row["label"], version=row["release"],
                ))

        if "umls" in enabled:
            semantic_group = "Disorders" if expected_type == "disease" else None
            try:
                for candidate in umls_search(term, limit=3, semantic_group=semantic_group):
                    entry["knowledge"].append(_knowledge(
                        candidate["label"], "umls", "umls_cui_candidate",
                        concept_id=candidate["concept_id"], canonical_label=candidate["label"],
                        version=candidate["ontology_release"], validated=False,
                        evidence=[{
                            "semantic_types": candidate.get("semantic_types", []),
                            "root_source": candidate.get("root_source"),
                            "match_basis": candidate.get("match_basis"),
                        }],
                    ))
            except UMLSAPIError:
                entry["umls_unavailable"] = True
    return lookups, annotations
