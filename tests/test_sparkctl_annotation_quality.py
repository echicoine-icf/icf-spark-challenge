"""5G.2 operational CLI wiring without Docker or database."""
import sparkctl


def test_quality_sample_does_not_restart_api_or_touch_migrations(monkeypatch):
    monkeypatch.setattr(sparkctl,'read_json',lambda p:{})
    calls=[]
    monkeypatch.setattr(sparkctl,'compose',lambda *a,**kw:calls.append(a) or '')
    assert sparkctl.main(['annotation-quality-sample','--concept','LOINC:2345-7',
                          '--per-class','15','--seed','review-round-a'])==0
    assert any(c[:4]==('--profile','tools','build','variable-annotation-worker') for c in calls)
    assert any('app.annotation_quality' in c and 'sample' in c and 'LOINC:2345-7' in c for c in calls)
    all_args=' '.join(' '.join(c) for c in calls)
    assert 'corpus-import' not in all_args
    assert 'api' not in all_args
    assert 'migrate' not in all_args


def test_quality_score_is_offline_and_skips_db_dependency(monkeypatch):
    monkeypatch.setattr(sparkctl,'read_json',lambda p:{})
    calls=[]
    monkeypatch.setattr(sparkctl,'compose',lambda *a,**kw:calls.append(a) or '')
    assert sparkctl.main(['annotation-quality-score'])==0
    assert len(calls)==1
    assert '--no-deps' in calls[0]
    assert 'score' in calls[0]
    assert not any(x=='db' for x in calls[0])


def test_quality_sample_rejects_out_of_bounds_without_side_effects(monkeypatch):
    monkeypatch.setattr(sparkctl,'read_json',lambda p:{})
    calls=[]
    monkeypatch.setattr(sparkctl,'compose',lambda *a,**kw:calls.append(a) or '')
    assert sparkctl.main(['annotation-quality-sample','--per-class','200'])==1
    assert calls==[]
