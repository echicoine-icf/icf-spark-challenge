"""No Docker, external downloads or licensed data in deployment-controller tests."""
import json
from pathlib import Path
import pytest
import sparkctl


def test_terminology_import_pins_only_after_success(tmp_path, monkeypatch):
    monkeypatch.setattr(sparkctl,'ROOT',tmp_path)
    (tmp_path/'knowledge').mkdir()
    archive=tmp_path/'knowledge'/'Loinc_2.83.zip'
    archive.write_bytes(b'synthetic upstream bytes')
    calls=[]
    digest=sparkctl.sha256_file(archive)
    def fake_compose(*args,**kwargs):
        calls.append(' '.join(args))
        if 'terminology-import' in args and '--file' in args:
            return json.dumps({'ontology':'LOINC','release':'2.83','sha256':digest,
                               'concepts':5,'terms':7,'active':True})
        return ''
    monkeypatch.setattr(sparkctl,'compose',fake_compose)
    manifest={'corpus':{'path':'../dbgap.tar.gz'},'ontologies':[],'models':[]}
    target=tmp_path/'deployment'/'artifacts.json'
    target.parent.mkdir()
    sparkctl.terminology_import(manifest,ontology='LOINC',source=Path('knowledge/Loinc_2.83.zip'),
                                release='2.83',ack_license=True,manifest_path=target)
    assert manifest['terminologies'][0]['sha256']==digest
    assert manifest['terminologies'][0]['license_acknowledged'] is True
    assert manifest['terminologies'][0]['expected_terms']==7
    assert '/knowledge/Loinc_2.83.zip' in ' '.join(calls)
    assert 'app.migrate --through 6' in ' '.join(calls)
    sparkctl.verify_terminology_assets(manifest)
    assert json.loads(target.read_text())['terminologies'][0]['release']=='2.83'
    archive.write_bytes(b'changed')
    with pytest.raises(sparkctl.DeploymentError,match='checksum mismatch'):
        sparkctl.verify_terminology_assets(manifest)


def test_import_requires_ack_and_local_artifact(tmp_path,monkeypatch):
    monkeypatch.setattr(sparkctl,'ROOT',tmp_path)
    with pytest.raises(sparkctl.DeploymentError,match='Acknowledge'):
        sparkctl.terminology_import({},ontology='RXNORM',source=Path('somewhere.zip'),
                                   release='2026-11-02')
    with pytest.raises(sparkctl.DeploymentError,match='Place'):
        sparkctl.terminology_import({},ontology='RXNORM',source=Path('somewhere.zip'),
                                   release='2026-11-02',ack_license=True)


def test_bootstrap_reuses_pinned_terminology(monkeypatch):
    monkeypatch.setattr(sparkctl,'preflight',lambda *a,**k:None)
    monkeypatch.setattr(sparkctl,'verify',lambda *a,**k:None)
    calls=[]
    monkeypatch.setattr(sparkctl,'compose',lambda *a,**k:calls.append(' '.join(a)) or '')
    manifest={'ontologies':[],'terminologies':[
        {'name':'RXNORM','container_file':'/knowledge/RxNorm_full_prescribe_11022026.zip',
         'release':'2026-11-02','source_uri':'https://example.org'}]}
    sparkctl.bootstrap(manifest,no_build=True,skip_audit=True)
    text='\n'.join(calls)
    assert 'terminology-import --file /knowledge/RxNorm_full_prescribe_11022026.zip' in text
    assert 'app.migrate --through 7' in text
    assert 'corpus-extract' not in text
