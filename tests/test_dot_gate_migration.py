"""Exact-input migration tests. No source fetching or inferred editorial work."""
import base64
import copy
from pathlib import Path
import pytest
from safety_digest import dot_handoff as dot, dot_recovery as recovery, lab_collector
from safety_digest.dot_checkpoint import Checkpoints
from test_dot_handoff import root, bundle, NOW


def setup(root,tmp_path):
    source=Path(__file__).parents[1]/'src/safety_digest'
    target=root/'src/safety_digest';target.mkdir(parents=True)
    for path in source.glob('*.py'):
        if path.name!='dot_edition.py': (target/path.name).write_bytes(path.read_bytes())
    b=bundle(root)
    b['collection'].update(complete=False,warnings=[{'kind':'pending','source':'scholar:Pending'}],
        pending_s2_authors=['Pending'],feed_transport=lab_collector.feed_transport())
    binding=dot.collection_binding(b['files'],b['state_rows'],NOW,7,lab_collector.feed_transport())
    b['collection']['checkpoint_binding']=binding;b=dot.seal({k:v for k,v in b.items() if k!='bundle_sha256'})
    path=tmp_path/'checkpoints';cp=Checkpoints(path,binding)
    cp.data['parts']={'arxiv':{'complete':True,'papers':[b['candidates'][0]['paper']],
                              'extra':{},'events':[],'retry_at':None}}
    cp.save()
    (target/'dot_handoff.py').write_bytes((target/'dot_handoff.py').read_bytes()+b'\n# reviewed validator release\n')
    (target/'dot_edition.py').write_bytes((source/'dot_edition.py').read_bytes())
    return b,cp,policy(root,b,cp)


def policy(root,b,cp):
    files=dot.provenance(root)
    changed={name for name in set(files)|set(b['files']) if files.get(name)!=b['files'].get(name)}
    return {'schema_version':1,'kind':'validation_gates_v1','from_bundle_sha256':b['bundle_sha256'],
            'from_checkpoint_sha256':cp.data['sha256'],'invalidate_parts':[],
            'changes':{name:{'before':dot.bytehash(base64.b64decode(b['files'][name])) if name in b['files'] else None,
                             'after':dot.bytehash(base64.b64decode(files[name])) if name in files else None}
                       for name in changed}}


def test_exact_corpus_checkpoint_and_health_preserved(root,tmp_path):
    b,cp,p=setup(root,tmp_path);parts=copy.deepcopy(cp.data['parts'])
    receipt=recovery.migrate(root,b,cp.directory,p)
    after=dot.read_json(cp.path)
    assert after['parts']==parts and after['binding']!=cp.binding
    new=recovery.rebind_bundle(root,b,receipt)
    for field in ('run_at','days','system_prompt','state_rows','candidates','suppressed','forced_keys'):
        assert new[field]==b[field]
    assert new['collection']['complete'] is False
    assert new['collection']['pending_s2_authors']==['Pending']
    assert new['collection']['warnings']==b['collection']['warnings']
    assert new['bundle_sha256']!=b['bundle_sha256'] and new['parent_bundle_sha256']==b['bundle_sha256']
    assert new['files']==dot.provenance(root)


@pytest.mark.parametrize('target', ['source','rubric','archive','state','window','parts'])
def test_migration_rejects_changed_input_identity(root,tmp_path,target):
    b,cp,p=setup(root,tmp_path)
    if target=='source':
        path=root/'src/safety_digest/dot_handoff.py'
        path.write_text(path.read_text().replace("'semantic_embeddings': False", "'semantic_embeddings': True"))
        p=policy(root,b,cp)
    elif target=='rubric':
        path=root/'src/safety_digest/prompts.py';path.write_text(path.read_text()+'\n# changed rubric source\n')
        p=policy(root,b,cp)
    elif target=='archive':
        (root/'docs/index.md').write_text('new edition');p=policy(root,b,cp)
    elif target=='state':
        import sqlite3
        with sqlite3.connect(root/'state.db') as conn:
            conn.execute('INSERT INTO seen_papers VALUES (?,?,?,?,?,?)',('new','2026-W39','now','Title','lab','high'))
    elif target=='window':
        b['run_at']='2026-09-29T05:42:24+00:00';b=dot.seal({k:v for k,v in b.items() if k!='bundle_sha256'})
        p['from_bundle_sha256']=b['bundle_sha256']
    elif target=='parts':
        cp.data['parts']['arxiv']['papers']=[];cp.save()
    with pytest.raises(dot.Invalid): recovery.migrate(root,b,cp.directory,p)
