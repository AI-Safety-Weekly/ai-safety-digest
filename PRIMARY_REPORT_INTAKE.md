# Dedicated primary-report intake

This isolated change ports only dedicated OpenAI and Anthropic collection from
PR #20. It does not change cs.CL, LessWrong, classification prompts, editorial
schemas, state keys, schedules, publication variables, or the completed edition.

Configured sources collect **first disclosures only**, every weekday. Update and
incident dates remain separate evidence; a later update does not make an old report
new. Primary dates have explicit date-only versus timestamp precision. Conflicting
article/index first dates or canonical URLs fail collection. Index admission is
not editorial admission: the ordinary primary-review publication gate still applies,
and auto-admit is false.

## Anthropic repair

The current official alignment page has changed from `publicationList` to a
`FilteringListing` containing `initialPage`, `articleCount`, and the alignment team
selector. It supplies 24 of 48 records. The official `FilteringListing` module at
`https://www.anthropic.com/_next/static/chunks/04_9peblvyok2.js` uses
`/listing/team-alignment/default/2` for the second page. A read-only request confirmed
24 more records and `hasNextPage: false` on October 26, 2026.

The Flight stream also contains UTF-8 byte-length text frames without intervening
newlines. A line-only decoder silently missed the following JSON record. The parser
now skips those frames by byte length and supports both the old complete payload
and the new bounded pagination schema without executing JavaScript. It validates
team, order, advertised count, unique IDs/URLs, page limits, and visible dates.
Malformed, repeated, truncated or out-of-domain data cannot produce a complete result.

OpenAI pages reject duplicate primary URLs within a page. Across pages, identical
overlap is counted once; conflicting titles, dates, precision, incident evidence or
summaries fail. Advertised totals must agree between pages and equal the final
unique-URL count before any article fetch. Regression coverage includes the captured
20-row index with one row replaced by a duplicate.

The two configured sources remain required. Legacy collection raises an explicit
source failure; hosted collection already records failures and leaves the part
incomplete. This change does not relax the current all-required-primary-parts gate.
PR #28's separate partial-child retrieval fix should be integrated independently.

## Evidence and limits

`tests/fixtures/primary_reports/provenance.json` records official URLs and hashes.
Index responses are captured public content. Article fixtures retain primary metadata
and main/article markup, omitting executable site assets; their original response
hash and transformed fixture hash are both recorded.

Read-only collection verified:

- October 9: OpenAI's damaging-the-task-environment, disallowed-statistics requests,
  and disallowed-web-requests/public-file-service reports; Anthropic's investigating
  unintended model actions report. All four have October 9 first-disclosure evidence.
- October 18–25: OpenAI's suppressing/disabling misalignment monitoring report
  (October 20) and Anthropic's UK AISI alignment assessment (October 23).
- Both complete current indexes parsed; 48 unique Anthropic index records were
  reconciled across two pages. Only the six in-window articles above were fetched,
  not all historical article bodies.

Not proven: future upstream schema stability, exhaustive author coverage, same-URL
material-update editorial handling, or unattended publication. Date-only boundary
records remain candidates requiring precision-aware primary review. These fixtures
are public-source regression evidence, not assertions that any reported model
behavior is independently established. No paid inference was used.
