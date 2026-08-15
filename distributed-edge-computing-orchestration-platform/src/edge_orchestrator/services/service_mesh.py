"""
Service Mesh Manager
=====================

Provides the control-plane side of secure service-to-service communication
across the fleet:

  * Workload identity issuance (SPIFFE-style URIs + a simulated short-lived
    certificate fingerprint) so every workload has a cryptographic identity
    independent of network location -- essential when nodes roam between
    cell towers, retail sites, and cloud burst capacity.
  * Zero-trust traffic policies (allow-lists between named workloads).
  * Sidecar proxy configuration generation, in an Envoy-compatible shape,
    that a real mesh data plane (Envoy, Linkerd2-proxy) could consume.

Certificate *issuance* here is simulated with the stdlib `secrets`/`hashlib`
modules to keep the project dependency-free; production deployments should
back this with a real CA (e.g. SPIFFE/SPIRE, Vault PKI, or a cloud KMS) as
described in docs/DEPLOYMENT.md.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..core.db import Database, row_document
from ..core.logging_setup import get_logger
from ..core.models import new_id

logger = get_logger("edge_orchestrator.service_mesh")

DEFAULT_CERT_TTL_S = 3600  # short-lived identity, rotated hourly


@dataclass
class WorkloadIdentity:
    spiffe_id: str
    fingerprint: str
    issued_ts: float
    expires_ts: float


@dataclass
class MeshPolicy:
    policy_id: str
    source_workload: str
    destination_workload: str
    allowed_ports: List[int] = field(default_factory=lambda: [443])
    mtls_required: bool = True
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "source_workload": self.source_workload,
            "destination_workload": self.destination_workload,
            "allowed_ports": self.allowed_ports,
            "mtls_required": self.mtls_required,
            "description": self.description,
        }

    @staticmethod
    def from_dict(d: dict) -> "MeshPolicy":
        return MeshPolicy(**d)


class ServiceMeshManager:
    def __init__(self, db: Database, trust_domain: str = "edge.internal"):
        self.db = db
        self.trust_domain = trust_domain

    # -- identity ------------------------------------------------------
    def issue_identity(self, workload_name: str, namespace: str = "default") -> WorkloadIdentity:
        spiffe_id = f"spiffe://{self.trust_domain}/ns/{namespace}/workload/{workload_name}"
        material = f"{spiffe_id}:{secrets.token_hex(16)}:{time.time()}"
        fingerprint = hashlib.sha256(material.encode()).hexdigest()[:32]
        now = time.time()
        identity = WorkloadIdentity(
            spiffe_id=spiffe_id, fingerprint=fingerprint, issued_ts=now, expires_ts=now + DEFAULT_CERT_TTL_S
        )
        logger.info("workload identity issued", extra={"extra_fields": {"spiffe_id": spiffe_id}})
        return identity

    # -- policy ------------------------------------------------------
    def add_policy(
        self,
        source_workload: str,
        destination_workload: str,
        allowed_ports: Optional[List[int]] = None,
        mtls_required: bool = True,
        description: str = "",
    ) -> MeshPolicy:
        policy = MeshPolicy(
            policy_id=new_id("meshpol"),
            source_workload=source_workload,
            destination_workload=destination_workload,
            allowed_ports=allowed_ports or [443],
            mtls_required=mtls_required,
            description=description,
        )
        self.db.execute(
            "INSERT INTO mesh_policies (policy_id, document) VALUES (?, ?)",
            (policy.policy_id, json.dumps(policy.to_dict())),
        )
        return policy

    def list_policies(self) -> List[MeshPolicy]:
        rows = self.db.query("SELECT * FROM mesh_policies ORDER BY policy_id")
        return [MeshPolicy.from_dict(row_document(r)) for r in rows]

    def is_allowed(self, source_workload: str, destination_workload: str, port: int) -> bool:
        for policy in self.list_policies():
            if (
                policy.source_workload in (source_workload, "*")
                and policy.destination_workload in (destination_workload, "*")
                and port in policy.allowed_ports
            ):
                return True
        return False

    # -- data plane config generation ---------------------------------
    def generate_sidecar_config(self, workload_name: str, upstreams: List[str]) -> str:
        """Render an Envoy-compatible sidecar configuration (YAML-shaped
        text, generated without a YAML library to keep the project
        dependency-free -- the output is valid, well-formed YAML)."""
        identity = self.issue_identity(workload_name)
        clusters = "\n".join(
            f"""  - name: {u}
    connect_timeout: 1s
    type: STRICT_DNS
    lb_policy: ROUND_ROBIN
    transport_socket:
      name: envoy.transport_sockets.tls
      typed_config:
        "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext
        common_tls_context:
          tls_certificate_sds_secret_configs:
            - name: {workload_name}-identity"""
            for u in upstreams
        )
        return f"""# Auto-generated sidecar configuration for '{workload_name}'
# SPIFFE identity: {identity.spiffe_id}
# Certificate fingerprint: {identity.fingerprint} (expires {int(identity.expires_ts)})
node:
  id: {workload_name}
  cluster: edge-mesh
static_resources:
  listeners:
    - name: inbound
      address:
        socket_address: {{ address: 0.0.0.0, port_value: 15006 }}
      filter_chains:
        - transport_socket:
            name: envoy.transport_sockets.tls
            typed_config:
              "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.DownstreamTlsContext
              require_client_certificate: true
  clusters:
{clusters if clusters else '    []'}
"""
