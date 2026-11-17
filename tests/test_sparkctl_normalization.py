"""Minimal pure host-side deployment smoke contract tests (no Docker)."""
import sparkctl


def test_normalization_smoke_safety_guards(monkeypatch):
    def fake_api(route, **kwargs):
        if route == '/ready':
            return {'status':'ready'}
        if 'medication' in route or 'laboratory_measurement' in route:
            return {'status':'terminology_not_loaded', 'normalized_concept':None,
                    'semantic_suggestions':[], 'semantic_candidates_can_expand':False}
        return {'status':'normalized_exact', 'normalized_concept':{'concept_id':'MONDO:0005148','concept_type':'disease'},
                'semantic_suggestions':[], 'semantic_candidates_can_expand':False}
    monkeypatch.setattr(sparkctl, 'api_json', fake_api)
    sparkctl.normalization_smoke()


def test_normalization_smoke_rejects_unsafe_expansion(monkeypatch):
    def fake_api(route, **kwargs):
        if route == '/ready':
            return {'status':'ready'}
        return {'status':'normalized_exact', 'normalized_concept':{'concept_id':'MONDO:0005148','concept_type':'disease'},
                'semantic_suggestions':[{'concept_id':'MONDO:bad'}],
                'semantic_candidates_can_expand':True}
    monkeypatch.setattr(sparkctl, 'api_json', fake_api)
    try:
        sparkctl.normalization_smoke()
    except sparkctl.DeploymentError:
        pass
    else:
        raise AssertionError('Unsafe semantic acceptance passed deployment smoke')
