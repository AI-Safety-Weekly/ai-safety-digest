# Primary-source coverage contract

`config/source_contract.yml` selects `primary_sources_v1` for new editions. It replaces the former all-or-nothing Semantic Scholar requirement with arXiv and configured official/author feeds. Semantic Scholar and OpenAlex are supplemental; this implementation does not make new calls to either. Existing completed Scholar records remain available. Neither a successful query nor a name match establishes exhaustive author coverage.

Every publication includes a source-coverage JSON report and visible disclosure. All configured people remain explicitly unchecked unless separately supported by reviewed identity evidence. The scheduled runner discloses that its dates come from source metadata; the Dot recovery additionally requires primary publication-date and work-identity evidence for each listed item. Revised or older work is not presented as a new publication in the frozen window.

## Active edition transition

Use the existing owned `migrate_active` request with `kind: primary_source_contract_v1`. The current bundle must be the exact checkpoint-source bundle. The immutable policy binds both bundle hashes, checkpoint file and payload hashes, old/new basis fingerprints, exact file changes, every retained checkpoint part, unchanged candidates, and primary reviews. Only the listed source-contract implementation/configuration files can change; history, author configuration, state rows and the edition window cannot.

The workflow produces a migration receipt. A successful acknowledgment verifies that receipt and advances basis and bundle together through the existing non-force ownership CAS. Failure leaves the old bundle/basis intact. Replay cannot rewrite a migrated scratch checkpoint. No direct ownership edits or local artifact import are supported.

For subsequent editions, a read-only `enrich` request may include `primary_review`, an ordinary hashed `dot-inputs/` JSON file. Each proof binds the frozen paper hash, original URL, checked time, quoted primary date/identity evidence, and evidence hash. Enrichment preserves the collection checkpoint. Final editorial validation still requires every candidate decision, quoted substantive evidence, deep-read attempts and existing quality checks.

The migration does not enable publication. Use the existing validation and publication flow, retain interim history, verify the deployed commit and live coverage report, then restore any temporary publishing flag to its previous state. No model inference is performed by the collection/migration/enrichment transport.
