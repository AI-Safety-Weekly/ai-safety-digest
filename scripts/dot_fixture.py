"""Emit an explicitly synthetic, unpublished smoke artifact; no collection/inference."""
import argparse
from datetime import datetime, timezone
from pathlib import Path

from safety_digest import dot_handoff as dot
from safety_digest.models import Paper


def emit(root, out):
    receipt = dot.read_json(out / 'transport-receipt.json')
    if receipt['mode'] != 'smoke':
        return
    abstract = ('This is a synthetic transport fixture, not research. It tests frozen input '
                'hashes, full decision accounting, artifact downloads and staged site rendering.')
    paper = Paper(title='SYNTHETIC TRANSPORT FIXTURE — NOT RESEARCH', authors=['Test fixture'],
                  abstract=abstract, url='https://example.invalid/dot-transport-fixture',
                  source='lab', published=datetime(2026, 10, 2, tzinfo=timezone.utc))
    bundle = dot.make_bundle(root, [paper], paper.published, 7,
                             {'complete':True, 'warnings':[], 'synthetic_fixture':True,
                              'scope':'Transport/build acceptance only; never publish this edition.'})
    if len(bundle['candidates']) != 1:
        raise dot.Invalid('fixture unexpectedly suppressed')
    results = dot.template(bundle)
    results['decisions'][0] = {
        'id':paper.dedupe_key, 'input_sha256':bundle['candidates'][0]['input_sha256'],
        'status':'complete',
        'classification':{'relevance':'low', 'safety_areas':['evals'],
                          'summary':'Synthetic transport test only; this is not a research finding.',
                          'rationale':'Checks data transport and rendering without inference.',
                          'capability':False, 'breakthrough':False, 'content_type':'other'},
        'evidence':[{'source':'abstract', 'quote':abstract}]}
    results['summaries']['off_lane'] = {'themes':[{'label':'Synthetic transport test',
        'summary':'Verifies the unpublished staging path; not research.', 'member_ids':[paper.dedupe_key]}]}
    dot.validate_results(bundle, results, final=True)
    dot.write_json(out / 'bundle.json', bundle)
    dot.write_json(out / 'results-complete.json', results)
    receipt.update(synthetic_fixture=True, candidate_count=1, bundle_sha256=bundle['bundle_sha256'])
    dot.write_json(out / 'transport-receipt.json', receipt)
    print({'synthetic_fixture':True, 'bundle_sha256':bundle['bundle_sha256'], 'published':False})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    emit(args.root, args.out)
