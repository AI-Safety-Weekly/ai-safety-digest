import importlib.util
import json
import shutil
from pathlib import Path
import pytest
from safety_digest import dot_handoff as dot

ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('interim_site',ROOT/'scripts/interim_site.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

@pytest.fixture
def source(tmp_path):
    r=tmp_path/'checkout';r.mkdir()
    for name in ('docs','interim-bulletins'):
        shutil.copytree(ROOT/name,r/name)
    shutil.copy(ROOT/'mkdocs.yml',r/'mkdocs.yml')
    return r

def test_overlay_preserves_original_and_archives(source,tmp_path):
    before=dot.provenance(source)
    stage=tmp_path/'stage';shutil.copytree(source,stage)
    m.assemble(stage,source)
    first=(stage/'docs/index.md').read_text()
    assert '12/294' in first and 'October 18' in first
    m.assemble(stage,source)
    assert (stage/'docs/index.md').read_text()==first
    (stage/'docs/digest-2026-W43.md').write_text('# Completed edition\n')
    m.assemble(stage,source)
    assert 'Interim bulletin archive' in (stage/'docs/index.md').read_text()
    assert (stage/'docs/interim/2026-10-25.md').exists()
    assert dot.provenance(source)==before
    with pytest.raises(ValueError,match='disposable'):m.assemble(source,source)

@pytest.mark.parametrize('tamper',['complete','coverage','body'])
def test_rejects_misleading_or_changed_bulletin(source,tamper):
    path=source/'interim-bulletins/2026-10-25/manifest.json';v=json.loads(path.read_text())
    if tamper=='complete':v['full_edition_complete']=True
    elif tamper=='coverage':v['coverage']['complete_profile_count']=294
    else:(path.parent/'bulletin.md').write_text('Changed after review')
    path.write_text(json.dumps(v))
    with pytest.raises(ValueError):m.load(source)

def test_real_strict_build_preserves_frozen_inputs(tmp_path,monkeypatch):
    before=dot.provenance(ROOT);state=(ROOT/'state.db').read_bytes();ownership=(ROOT/'.dot/active-edition.json').read_bytes()
    site=tmp_path/'site';m.build(ROOT,site)
    html=(site/'interim/2026-10-25/index.html').read_text()
    assert '12 of 294' in html and '282' in html and '../2026-10-25-coverage.json' in html
    assert (site/'interim/2026-10-25-coverage.json').exists()
    assert dot.provenance(ROOT)==before and (ROOT/'state.db').read_bytes()==state and (ROOT/'.dot/active-edition.json').read_bytes()==ownership
