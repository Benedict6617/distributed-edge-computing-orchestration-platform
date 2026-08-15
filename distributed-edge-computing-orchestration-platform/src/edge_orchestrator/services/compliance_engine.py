"""
Compliance Engine
=================

Evaluates workload placement against data-sovereignty and data-residency
rules (GDPR-style EU localization, sector-specific rules, etc). This is a
policy-evaluation engine, not a legal-advice engine: the shipped rule set
in `data/regions_compliance.json` is a realistic starting point that a
compliance/legal team is expected to customize per jurisdiction.

Every placement decision is written to an append-only audit log so a
business can demonstrate, after the fact, exactly why a given workload
ran on a given node in a given country.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from ..core.db import Database, row_document
from ..core.logging_setup import get_logger
from ..core.models import EdgeNode, Workload

logger = get_logger("edge_orchestrator.compliance")


@dataclass
class ComplianceRegionRule:
    region_code: str                 # e.g. "eu"
    countries: List[str]              # ISO-3166 alpha-2 codes belonging to this region
    allowed_destination_regions: List[str]   # regions data MAY additionally leave to (usually just itself)
    regulation: str                   # human-readable regulation name, e.g. "GDPR"
    requires_encryption_at_rest: bool = True
    requires_encryption_in_transit: bool = True
    data_retention_days_max: Optional[int] = None


@dataclass
class ComplianceResult:
    allowed: bool
    reason: str
    applicable_rule: Optional[str] = None


class ComplianceEngine:
    def __init__(self, db: Database, region_rules: Dict[str, ComplianceRegionRule]):
        self.db = db
        self.region_rules = region_rules

    @staticmethod
    def load_rules_from_file(path: str) -> Dict[str, ComplianceRegionRule]:
        with open(path) as f:
            raw = json.load(f)
        rules = {}
        for region_code, entry in raw.items():
            rules[region_code] = ComplianceRegionRule(region_code=region_code, **entry)
        return rules

    def evaluate_placement(self, workload: Workload, node: EdgeNode) -> ComplianceResult:
        if not workload.data_residency_region:
            return ComplianceResult(allowed=True, reason="workload has no residency constraint")

        rule = self.region_rules.get(workload.data_residency_region)
        if not rule:
            return ComplianceResult(
                allowed=False,
                reason=f"unknown data residency region '{workload.data_residency_region}'",
            )

        node_country = node.location.country_code
        node_region = node.location.region

        in_home_region = node_country in rule.countries or node_region == rule.region_code
        in_allowed_destination = node_region in rule.allowed_destination_regions

        allowed = in_home_region or in_allowed_destination
        reason = (
            f"node country '{node_country}' / region '{node_region}' "
            f"{'satisfies' if allowed else 'violates'} {rule.regulation} residency rule "
            f"for region '{rule.region_code}'"
        )

        self._audit(workload, node, allowed, reason, rule.regulation)
        return ComplianceResult(allowed=allowed, reason=reason, applicable_rule=rule.regulation)

    def eligible_nodes(self, workload: Workload, candidate_nodes: List[EdgeNode]) -> List[EdgeNode]:
        return [n for n in candidate_nodes if self.evaluate_placement(workload, n).allowed]

    def _audit(self, workload: Workload, node: EdgeNode, allowed: bool, reason: str, regulation: str) -> None:
        entry = {
            "workload_id": workload.workload_id,
            "node_id": node.node_id,
            "node_country": node.location.country_code,
            "node_region": node.location.region,
            "regulation": regulation,
            "allowed": allowed,
            "reason": reason,
        }
        self.db.execute(
            "INSERT INTO compliance_audit_log (timestamp, document) VALUES (?, ?)",
            (time.time(), json.dumps(entry)),
        )

    def audit_log(self, limit: int = 100) -> List[dict]:
        rows = self.db.query(
            "SELECT * FROM compliance_audit_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [row_document(r) for r in rows]

    def list_regions(self) -> List[dict]:
        out = []
        for code, rule in self.region_rules.items():
            out.append(
                {
                    "region_code": code,
                    "regulation": rule.regulation,
                    "countries": rule.countries,
                    "allowed_destination_regions": rule.allowed_destination_regions,
                    "requires_encryption_at_rest": rule.requires_encryption_at_rest,
                    "requires_encryption_in_transit": rule.requires_encryption_in_transit,
                    "data_retention_days_max": rule.data_retention_days_max,
                }
            )
        return out
