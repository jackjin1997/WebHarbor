# FedEx mirror scope

The homepage is based on the US FedEx homepage observed in Chrome on
September 10, 2026. The image/font sources and checksums are recorded in
`homepage-assets.json`; the actual media ships in the paired HF asset archive.
Desktop and mobile use their respective original hero images. No live scripts,
trackers or remote media requests are needed by the mirror.

## Offline boundaries

Tracking, rates, locations, support, local accounts, shipment creation and pickup
scheduling are benchmark workflows backed by deterministic local records. They
do not contact FedEx, purchase shipping, or create real carrier bookings.
The support detail pages explicitly distinguish local guidance from carrier policy.

Marketing, printing, sweepstakes, refunds, corporate links, social links and app
downloads preserve their homepage entry points but open an offline-availability
notice. Those live workflows are not implemented. Fonts include original regular
and light weights; the browser synthesizes bold.

## Tasks and data maintenance

The accepted set contains 18 tasks with one rubric and one verifier per task.
Tasks 2 and 11 now require detail-specific information and cross-page reading,
rather than generic demo boilerplate. Task 10 explicitly names the locations
navigation already required by its rubric. Answer keys remain outside the task file.

`support_content.py` is the canonical source for the two revised local guides.
New seeds use it through `seed_data.py`. To refresh an existing asset seed:

```sh
cd sites/fedex
python refresh_support.py instance_seed/fedex.db
```

This explicit maintenance command updates exactly two existing articles in one
transaction. It is never called from application startup or reset. Normal boot
must preserve a populated seed byte-for-byte.

## Regression check

After fetching the paired assets, from the repository root:

```sh
python -m unittest discover -s sites/fedex/tests -v
node --check sites/fedex/static/js/main.js
```

These tests cover the task contract, scoring fixtures, local route behavior,
media hashes and seed preservation. They do not replace browser execution,
visual comparison, full-environment integration or independent review.
