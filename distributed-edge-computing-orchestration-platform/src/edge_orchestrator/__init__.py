"""
Edge Orchestrator
==================

A lightweight, dependency-free control plane for orchestrating containerized
workloads across large fleets of edge nodes (cell towers, retail stores,
factories, warehouses, kiosks).

The package is intentionally built on the Python standard library only.
Edge control planes are frequently deployed onto constrained management
servers (and occasionally onto beefy edge gateways themselves) where a
minimal dependency footprint reduces attack surface, speeds up cold start,
and avoids "dependency drift" between the control plane and the fleet it
manages. Everything here runs with nothing more than `python3`.

See docs/ARCHITECTURE.md for a full component breakdown.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
