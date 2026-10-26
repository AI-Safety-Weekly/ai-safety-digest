"""Prepare a source-labelled author sweep from verified saved artifacts; never publish."""
import argparse
from pathlib import Path
from safety_digest import dot_handoff as dot,source_sweep as sweep


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--checkpoint',type=Path,required=True)
    parser.add_argument('--recovery-receipt',type=Path,required=True)
    parser.add_argument('--verified-items',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1];out=args.output.resolve()
    if out.exists() or out==root or root in out.parents:
        raise dot.Invalid('sweep output must be new and outside checkout')
    bundle=dot.load_bundle(args.bundle);checkpoint=dot.read_json(args.checkpoint);receipt=dot.read_json(args.recovery_receipt)
    if receipt['bundle_sha256']!=bundle['bundle_sha256']:
        raise dot.Invalid('recovery receipt differs from source bundle')
    checksum=receipt['checkpoint_sha256'];report=sweep.prepare(bundle,checkpoint,checksum)
    if args.verified_items:
        for item in dot.read_json(args.verified_items):
            report=sweep.add_verified(report,bundle,item,checkpoint,checksum)
    dot.write_json(out,report)
    print(dot.canonical(sweep.summary(report,bundle,checkpoint,checksum)).decode())


if __name__=='__main__':main()
