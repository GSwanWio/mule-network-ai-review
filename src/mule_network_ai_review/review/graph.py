from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

import pandas as pd

from mule_network_ai_review.ai.models import SubjectType
from mule_network_ai_review.ingestion import WorkbookPackage
from mule_network_ai_review.review.models import GraphNodeType


class ReviewGraphError(ValueError):
	pass


def _required_text(value: Any, field_name: str) -> str:
	if value is None or pd.isna(value):
		raise ReviewGraphError(f"{field_name} is missing from the protected network graph.")
	text = str(value).strip()
	if not text:
		raise ReviewGraphError(f"{field_name} is blank in the protected network graph.")
	return text


def _optional_text(value: Any) -> str | None:
	if value is None or pd.isna(value):
		return None
	text = str(value).strip()
	return text or None


def _optional_bool(value: Any) -> bool | None:
	if value is None or pd.isna(value):
		return None
	return bool(value)


@dataclass(frozen=True)
class GraphNode:
	node_id: str
	node_type: GraphNodeType
	node_token: str
	node_layer: int
	is_seed_customer: bool
	deterministic_expansion_decision: str | None
	was_expanded: bool | None

	@property
	def subject_type(self) -> SubjectType | None:
		if self.node_type == GraphNodeType.CUSTOMER:
			return SubjectType.CUSTOMER
		if self.node_type == GraphNodeType.COUNTERPARTY:
			return SubjectType.COUNTERPARTY
		return None


@dataclass(frozen=True)
class GraphRelationship:
	relationship_id: str
	relationship_type: str
	source_node_id: str
	target_node_id: str
	relationship_description: str | None


@dataclass(frozen=True)
class NetworkGraphIndex:
	data_snapshot_id: str
	network_id: str
	seed_node_id: str
	nodes: dict[str, GraphNode]
	relationships: dict[str, GraphRelationship]
	adjacency: dict[str, frozenset[str]]
	distances: dict[str, int]
	predecessors: dict[str, tuple[str, ...]]
	forward_children: dict[str, tuple[str, ...]]
	incident_relationship_ids: dict[str, tuple[str, ...]]
	identity_customer_node_ids: frozenset[str]

	@classmethod
	def from_package(
		cls,
		package: WorkbookPackage,
		network_id: str,
	) -> NetworkGraphIndex:
		resolved_network_id = network_id.strip()
		if not resolved_network_id:
			raise ReviewGraphError("network_id cannot be blank.")

		node_frame = package.sheet("nodes").loc[
			lambda frame: frame["network_id"].astype(str) == resolved_network_id
		]
		if node_frame.empty:
			raise ReviewGraphError("The selected network does not exist in the workbook.")

		nodes = {
			_required_text(row["node_id"], "node_id"): cls._node_from_row(row)
			for _, row in node_frame.iterrows()
		}
		seed_nodes = [node.node_id for node in nodes.values() if node.is_seed_customer]
		if len(seed_nodes) != 1:
			raise ReviewGraphError(
				f"The selected network must contain exactly one seed node; found {len(seed_nodes)}."
			)
		seed_node_id = seed_nodes[0]

		relationship_frame = package.sheet("relationships").loc[
			lambda frame: frame["network_id"].astype(str) == resolved_network_id
		]
		relationships = {
			_required_text(row["relationship_id"], "relationship_id"): cls._relationship_from_row(
				row
			)
			for _, row in relationship_frame.iterrows()
		}
		adjacency_sets = {node_id: set() for node_id in nodes}
		incident_sets = {node_id: set() for node_id in nodes}
		predecessor_sets = {node_id: set() for node_id in nodes}
		forward_child_sets = {node_id: set() for node_id in nodes}

		for relationship in relationships.values():
			if relationship.source_node_id not in nodes:
				raise ReviewGraphError(
					f"Relationship {relationship.relationship_id} has an unknown source node."
				)
			if relationship.target_node_id not in nodes:
				raise ReviewGraphError(
					f"Relationship {relationship.relationship_id} has an unknown target node."
				)
			adjacency_sets[relationship.source_node_id].add(relationship.target_node_id)
			adjacency_sets[relationship.target_node_id].add(relationship.source_node_id)
			incident_sets[relationship.source_node_id].add(relationship.relationship_id)
			incident_sets[relationship.target_node_id].add(relationship.relationship_id)
			parent_node_id, child_node_id = cls._directed_endpoints(
				relationship,
				nodes,
			)
			forward_child_sets[parent_node_id].add(child_node_id)
			predecessor_sets[child_node_id].add(parent_node_id)

		identity_customer_node_ids: set[str] = set()
		for node_id, node in nodes.items():
			if node.node_type != GraphNodeType.EID:
				continue
			linked_customer_node_ids = {
				neighbour_id
				for neighbour_id in adjacency_sets[node_id]
				if nodes[neighbour_id].node_type == GraphNodeType.CUSTOMER
			}
			if len(linked_customer_node_ids) > 1:
				identity_customer_node_ids.update(linked_customer_node_ids)

		distances = cls._distances(seed_node_id, forward_child_sets)
		unreachable = sorted(set(nodes) - set(distances))
		if unreachable:
			raise ReviewGraphError(
				"The selected network contains "
				f"{len(unreachable)} nodes disconnected from its seed."
			)

		return cls(
			data_snapshot_id=package.validation_summary.export_run_id,
			network_id=resolved_network_id,
			seed_node_id=seed_node_id,
			nodes=nodes,
			relationships=relationships,
			adjacency={
				node_id: frozenset(neighbours)
				for node_id, neighbours in adjacency_sets.items()
			},
			distances=distances,
			predecessors={
				node_id: tuple(sorted(predecessor_node_ids))
				for node_id, predecessor_node_ids in predecessor_sets.items()
			},
			forward_children={
				node_id: tuple(sorted(forward_child_node_ids))
				for node_id, forward_child_node_ids in forward_child_sets.items()
			},
			incident_relationship_ids={
				node_id: tuple(sorted(relationship_ids))
				for node_id, relationship_ids in incident_sets.items()
			},
			identity_customer_node_ids=frozenset(identity_customer_node_ids),
		)

	@staticmethod
	def _node_from_row(row: pd.Series) -> GraphNode:
		node_type = GraphNodeType(_required_text(row["node_type"], "node_type"))
		token_column = {
			GraphNodeType.CUSTOMER: "customer_token",
			GraphNodeType.EID: "eid_token",
			GraphNodeType.COUNTERPARTY: "counterparty_token",
		}[node_type]
		return GraphNode(
			node_id=_required_text(row["node_id"], "node_id"),
			node_type=node_type,
			node_token=_required_text(row[token_column], token_column),
			node_layer=int(row["node_layer"]),
			is_seed_customer=bool(row["is_seed_customer"]),
			deterministic_expansion_decision=_optional_text(
				row["deterministic_expansion_decision"]
			),
			was_expanded=_optional_bool(row["was_expanded"]),
		)

	@staticmethod
	def _relationship_from_row(row: pd.Series) -> GraphRelationship:
		return GraphRelationship(
			relationship_id=_required_text(row["relationship_id"], "relationship_id"),
			relationship_type=_required_text(row["relationship_type"], "relationship_type"),
			source_node_id=_required_text(row["source_node_id"], "source_node_id"),
			target_node_id=_required_text(row["target_node_id"], "target_node_id"),
			relationship_description=_optional_text(row["relationship_description"]),
		)

	@staticmethod
	def _directed_endpoints(
		relationship: GraphRelationship,
		nodes: dict[str, GraphNode],
	) -> tuple[str, str]:
		source = nodes[relationship.source_node_id]
		target = nodes[relationship.target_node_id]
		if source.node_type != GraphNodeType.CUSTOMER:
			raise ReviewGraphError(
				f"Relationship {relationship.relationship_id} must start from a customer row."
			)
		if target.node_type not in {GraphNodeType.EID, GraphNodeType.COUNTERPARTY}:
			raise ReviewGraphError(
				f"Relationship {relationship.relationship_id} has an invalid target type."
			)
		if target.node_layer == source.node_layer + 1:
			return source.node_id, target.node_id
		if target.node_layer == source.node_layer:
			return target.node_id, source.node_id
		raise ReviewGraphError(
			f"Relationship {relationship.relationship_id} cannot be placed in the "
			"deterministic discovery sequence."
		)

	@staticmethod
	def _distances(
		seed_node_id: str,
		adjacency: dict[str, set[str]],
	) -> dict[str, int]:
		distances = {seed_node_id: 0}
		queue = deque([seed_node_id])
		while queue:
			node_id = queue.popleft()
			for neighbour in sorted(adjacency[node_id]):
				if neighbour in distances:
					continue
				distances[neighbour] = distances[node_id] + 1
				queue.append(neighbour)
		return distances

	def relationship_ids_between(self, first_node_id: str, second_node_id: str) -> tuple[str, ...]:
		shared_ids = set(self.incident_relationship_ids[first_node_id]).intersection(
			self.incident_relationship_ids[second_node_id]
		)
		return tuple(sorted(shared_ids))
