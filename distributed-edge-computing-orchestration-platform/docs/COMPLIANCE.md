# Data Sovereignty & Compliance

## What the compliance engine does

`services/compliance_engine.py` evaluates every workload placement
against a configurable set of regional data-residency rules
(`data/regions_compliance.json`) *before* the scheduler is allowed to
bind a workload to a node. A workload tagged with
`data_residency_region: "eu"` cannot be scored onto a node outside the
EU's configured country list, full stop — the compliance filter runs
before scoring, so a non-compliant node is never even a candidate the
cost optimizer could pick.

Every evaluation — allowed or denied — is written to an append-only
audit log (`compliance_audit_log` table), so an operator can answer "why
did/didn't workload X run on node Y" days or months later, which matters
for demonstrating compliance to an auditor or regulator after the fact.

## Shipped rule set

| Region code | Regulation modeled | Countries | Notes |
|---|---|---|---|
| `eu` | GDPR | DE, FR, NL, IE, ES, IT, BE, PL, SE, AT | Data must stay within listed EU countries |
| `us` | US state privacy laws (CCPA/CPRA and sector rules) | US | |
| `apac` | APAC data localization (e.g. Singapore PDPA) | SG, JP, AU, IN | |
| `ca` | PIPEDA | CA | May additionally route to `us` (`allowed_destination_regions`) |

This is a **realistic starting point, not legal advice**. Data-residency
law varies by sector (health data, financial data, and children's data
often have stricter rules than general consumer data) and changes over
time. A business adopting this platform should have its legal/compliance
team review and customize `data/regions_compliance.json` for its actual
regulatory obligations before relying on it.

## Extending the rule set

Add or edit an entry in `data/regions_compliance.json`:

```json
{
  "uk": {
    "countries": ["GB"],
    "allowed_destination_regions": ["uk", "eu"],
    "regulation": "UK GDPR",
    "requires_encryption_at_rest": true,
    "requires_encryption_in_transit": true,
    "data_retention_days_max": 365
  }
}
```

No code changes are required — `ComplianceEngine.load_rules_from_file`
(wired up in `container.py`) picks up every region key automatically.

## What "compliant" checks today

- **Node location** (`GeoLocation.country_code` / `region`) against the
  workload's `data_residency_region`.
- **Explicit destination allow-lists** (`allowed_destination_regions`),
  for cases like Canada's PIPEDA permitting transfer to the US under
  certain conditions.

## What it does not (yet) check

These are flagged in the rule schema (`requires_encryption_at_rest`,
`requires_encryption_in_transit`, `data_retention_days_max`) but are not
independently enforced by this reference implementation — they are
metadata a real deployment should wire up to:

- Actual at-rest encryption verification on the target node (e.g.
  confirming disk encryption is enabled via the node's reported hardware
  attestation).
- Actual in-transit encryption enforcement — pair this with the service
  mesh's `mtls_required` policy flag so a compliance-tagged workload can
  only be reached over mTLS.
- Automated data-retention enforcement/purge jobs against
  `data_retention_days_max`.

Treat the compliance engine as the *placement gate*, and these
adjacent controls as the operational practices a real business layers on
top of correct placement.
