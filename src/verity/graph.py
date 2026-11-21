"""Evidence graph export (JSON, DOT, text).

The graph is intentionally derived from the evidence store rather than stored
separately: there is exactly one source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .engine import Engine
from .models import Claim, ClaimStatus, Evidence, EvidenceStatus


@dataclass
class GraphNode:
    id: str
    type: str
    label: str
    status: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "status": self.status,
            "meta": self.meta,
        }


@dataclass
class GraphEdge:
    source: str
    target: str
    relation: str

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target, "relation": self.relation}


@dataclass
class EvidenceGraph:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }


def build_graph(engine: Engine, *, include_history: bool = False) -> EvidenceGraph:
    graph = EvidenceGraph()
    claims = engine.repo.claims()
    claim_by_id = {c.id: c for c in claims}
    evidence = engine.repo.all_evidence(current_only=not include_history)
    contracts: set[str] = set()

    for claim in claims:
        graph.nodes.append(
            GraphNode(
                id=f"claim:{claim.id}",
                type="claim",
                label=claim.text,
                status=claim.status,
                meta={
                    "kind": claim.kind,
                    "verifier": claim.verifier,
                    "required": claim.required,
                    "contract": claim.contract_id,
                    "source": claim.source,
                },
            )
        )
        if claim.contract_id:
            contracts.add(claim.contract_id)
            graph.nodes.append(
                GraphNode(
                    id=f"contract:{claim.contract_id}",
                    type="contract",
                    label=claim.contract_id,
                    status="required" if claim.required else "optional",
                )
            )
            graph.edges.append(
                GraphEdge(f"contract:{claim.contract_id}", f"claim:{claim.id}", "requires")
            )

    for item in evidence:
        if item.claim_id not in claim_by_id:
            continue
        eid = f"evidence:{item.id}"
        graph.nodes.append(
            GraphNode(
                id=eid,
                type="evidence",
                label=f"{item.verifier}: {item.summary}",
                status="valid" if item.valid else "invalidated",
                meta={
                    "evidence_status": item.status,
                    "valid": item.valid,
                    "superseded": item.superseded,
                    "reason": item.invalidated_reason,
                    "created_at": item.created_at,
                    "commit": item.commit_sha,
                    "confidence": item.confidence,
                },
            )
        )
        graph.edges.append(GraphEdge(f"claim:{item.claim_id}", eid, "supported_by"))
        for path, digest in engine.repo.evidence_deps(item.id).items():
            artifact_id = f"artifact:{path}"
            if not any(n.id == artifact_id for n in graph.nodes):
                graph.nodes.append(
                    GraphNode(id=artifact_id, type="artifact", label=path, status="tracked")
                )
            graph.edges.append(GraphEdge(eid, artifact_id, "depends_on"))

    return graph


def to_dot(graph: EvidenceGraph) -> str:
    colors = {
        "verified": "#2ea043",
        "partially_verified": "#d29922",
        "unverified": "#8b949e",
        "contradicted": "#f85149",
        "stale": "#a371f7",
        "valid": "#2ea043",
        "invalidated": "#f85149",
        "required": "#58a6ff",
        "optional": "#8b949e",
        "tracked": "#8b949e",
    }
    lines = ["digraph verity {", "  rankdir=LR;", '  node [shape=box, style="rounded,filled", fontname="Helvetica"];']
    for node in graph.nodes:
        color = colors.get(node.status, "#8b949e")
        label = node.label.replace('"', "'")
        if len(label) > 80:
            label = label[:77] + "..."
        lines.append(
            f'  "{node.id}" [label="{label}", fillcolor="{color}", fontcolor="#ffffff"];'
        )
    for edge in graph.edges:
        style = "dashed" if edge.relation == "depends_on" else "solid"
        lines.append(f'  "{edge.source}" -> "{edge.target}" [label="{edge.relation}", style="{style}"];')
    lines.append("}")
    return "\n".join(lines)


def to_text(graph: EvidenceGraph) -> str:
    claims = [n for n in graph.nodes if n.type == "claim"]
    if not claims:
        return "No claims in the evidence graph. Run `verity analyze` first."
    outgoing: dict[str, list[tuple[str, str]]] = {}
    for edge in graph.edges:
        outgoing.setdefault(edge.source, []).append((edge.target, edge.relation))
    nodes = {n.id: n for n in graph.nodes}
    lines: list[str] = []
    for claim in claims:
        icon = {
            "verified": "✔",
            "partially_verified": "◐",
            "unverified": "○",
            "contradicted": "✘",
            "stale": "⟳",
        }.get(claim.status, "?")
        lines.append(f"{icon} {claim.label}  [{claim.status.replace('_', ' ')}]")
        supports = [t for t, rel in outgoing.get(claim.id, []) if rel == "supported_by"]
        for i, eid in enumerate(supports):
            ev = nodes.get(eid)
            if not ev:
                continue
            marker = "└─" if i == len(supports) - 1 else "├─"
            status = ev.meta.get("evidence_status", ev.status)
            flag = "INVALID " if ev.status == "invalidated" else ""
            lines.append(f"   {marker} {flag}{status.upper():5s} {ev.label}")
            artifacts = [t for t, rel in outgoing.get(eid, []) if rel == "depends_on"]
            for j, aid in enumerate(artifacts[:4]):
                art = nodes.get(aid)
                if art:
                    branch = "   " if i == len(supports) - 1 else "│  "
                    leaf = "└─" if j == len(artifacts[:4]) - 1 else "├─"
                    lines.append(f"   {branch}  {leaf} {art.label}")
        lines.append("")
    return "\n".join(lines)
