import importlib.util
import json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('publication_receipt',Path(__file__).parents[1]/'scripts/publication_receipt.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


def site(tmp_path):
    (tmp_path/'index.html').write_text('Current homepage')
    page=tmp_path/'digest-2026-W44/index.html';page.parent.mkdir();page.write_text('New weekly content')
    (tmp_path/'source-coverage-2026-W44.json').write_text(json.dumps({'source_contract':{'id':'primary_sources_v1'},'window':{'end':'2026-11-01T10:17:00Z'}}))
    return r.prepare(tmp_path,'a'*40,'2026-W44','123')


def test_actual_live_content_and_exact_artifact_required(tmp_path):
    receipt=site(tmp_path);fetch=lambda path:(tmp_path/path).read_bytes()
    assert r.verify('https://example.invalid','2026-W44',receipt,fetch)==receipt
    (tmp_path/receipt['page_path']).write_text('old cached page')
    with pytest.raises(ValueError,match='content'):r.verify('', '2026-W44',receipt,fetch)


def test_stale_week_and_wrong_commit_fail(tmp_path):
    receipt=site(tmp_path);fetch=lambda path:(tmp_path/path).read_bytes()
    with pytest.raises(ValueError):r.verify('','2026-W45',fetch=fetch)
    with pytest.raises(ValueError):r.verify('','2026-W44',{**receipt,'source_commit':'b'*40},fetch)


def run(event='schedule',status='completed',title='Weekly digest',conclusion='success'):
    return {'createdAt':'2026-11-01T10:00:00Z','status':status,'event':event,'displayTitle':title,'conclusion':conclusion}


def test_successful_job_or_commit_is_not_publication():
    assert r.decision([run()],'2026-11-01',False)=='dispatch'
    assert r.decision([run(conclusion='cancelled')],'2026-11-01',False)=='dispatch'
    assert r.decision([run()],'2026-11-01',True)=='published'


def test_build_only_does_not_consume_budget_and_active_run_prevents_race():
    assert r.decision([run('workflow_dispatch',title='Build-only: 123')],'2026-11-01',False)=='dispatch'
    assert r.decision([run('workflow_dispatch')],'2026-11-01',False)=='exhausted'
    assert r.decision([run(status='queued')],'2026-11-01',False)=='active'


def test_interim_notice_rolls_over_without_october_25_digest(tmp_path):
    spec=importlib.util.spec_from_file_location('interim_site',Path(__file__).parents[1]/'scripts/interim_site.py')
    interim=importlib.util.module_from_spec(spec);spec.loader.exec_module(interim)
    (tmp_path/'digest-2026-W42.md').write_text('old')
    assert not interim.has_replacement_edition(tmp_path)
    (tmp_path/'digest-2026-W44.md').write_text('new')
    assert interim.has_replacement_edition(tmp_path)
    page=tmp_path/'digest-2026-W44/index.html';page.parent.mkdir();page.write_text('new')
    assert interim.has_replacement_edition(tmp_path,rendered=True)


def test_workflow_keeps_schedule_and_controls_and_verifies_actual_commit():
    import yaml
    root=Path(__file__).parents[1]
    load=lambda name:yaml.load((root/'.github/workflows'/name).read_text(),Loader=yaml.BaseLoader)
    weekly=load('weekly.yml');watchdog=load('watchdog.yml')
    assert weekly['on']['schedule']==[{'cron':'17 10 * * 0'}]
    assert [r['cron'] for r in watchdog['on']['schedule']]==['43 11 * * 0','43 13 * * 0','43 15 * * 0']
    assert "DOT_REPLACE_GEMINI != 'true'" in weekly['jobs']['digest']['if']
    scripts='\n'.join(s.get('run','') for s in weekly['jobs']['digest']['steps'])
    assert 'GITHUB_SHA="$(git rev-parse HEAD)"' in scripts
    deploy=weekly['jobs']['deploy']['steps']
    assert '--commit "$EXPECTED_COMMIT"' in deploy[-1]['run']
    assert 'publication_receipt.py verify' in deploy[-1]['run']


def test_stale_homepage_fails_even_when_edition_and_coverage_match(tmp_path):
    receipt=site(tmp_path)
    (tmp_path/'index.html').write_text('Previous week homepage')
    with pytest.raises(ValueError,match='homepage'):
        r.verify('', '2026-W44',receipt,lambda path:(tmp_path/path).read_bytes())


def test_dot_receipt_uses_frozen_week_and_replay_commit(tmp_path, monkeypatch):
    import base64
    import yaml
    from safety_digest import source_contract
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'scripts'))
    import dot_publish
    output=tmp_path/'output';built=output/'site';built.mkdir(parents=True)
    site(built)
    bundle={'run_at':'2026-11-01T10:17:00Z','files':{
        'config/source_contract.yml':base64.b64encode(yaml.safe_dump(source_contract.CONFIG).encode()).decode()}}
    result={'commit':'b'*40,'status':'already_committed','deploy':True,'published':False}
    dot_publish.bind_publication_receipt(output,bundle,result,'edition-retry')
    receipt=r.verify('', '2026-W44',fetch=lambda path:(built/path).read_bytes())
    assert receipt['source_commit']=='b'*40
    assert result['edition']=='2026-W44' and result['receipt'] is True
    assert result['published'] is False  # Building proof does not establish live publication.
    legacy={'commit':'c'*40}
    dot_publish.bind_publication_receipt(tmp_path/'absent',{'files':{}},legacy,'old')
    assert legacy['receipt'] is False


def test_dot_deploy_retains_ownership_deadline_and_live_commit_checks():
    import yaml
    root=Path(__file__).parents[1]
    workflow=yaml.load((root/'.github/workflows/dot-publish.yml').read_text(),Loader=yaml.BaseLoader)
    steps=workflow['jobs']['deploy']['steps']
    scripts='\n'.join(s.get('run','') for s in steps)
    assert 'require_request' in scripts and 'live=True' in scripts
    assert 'git ls-remote' in scripts
    assert 'publication_receipt.py verify' in steps[-1]['run']
    assert '--commit "$EXPECTED_COMMIT"' in steps[-1]['run']
