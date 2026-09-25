"""Agent tool registry.

This is the surface the reasoning layer sees. Two ways to wire it up:

  1. TigerGraph MCP (https://github.com/tigergraph/tigergraph-mcp) -
     run the MCP server against your Savanna/CE instance and the agent
     calls the installed GSQL queries as MCP tools. The tool names and
     parameters below match the installed query names 1:1 on purpose,
     so switching transports changes nothing about agent behaviour.
  2. Direct (this module) - thin wrappers around GraphClient, used in
     offline mode and in tests.

Either way, every tool returns plain dicts/lists so results drop
straight into the evidence bundle and the audit trail.
"""
from .graph_client import GraphClient


class ToolBelt:
    """The complete set of tools the investigator can call."""

    def __init__(self, graph: GraphClient | None = None):
        self.graph = graph or GraphClient()

    # Each method mirrors an installed GSQL query / MCP tool.
    def evidence_subgraph(self, txn_id: str) -> dict:
        """2-hop neighbourhood of a transaction (customer, card, device, IP, merchant...)."""
        return self.graph.evidence_subgraph(txn_id)

    def shared_attribute_ring(self, customer_id: str) -> list[dict]:
        """Other customers sharing device/IP/email/address infrastructure."""
        return self.graph.shared_attribute_ring(customer_id)

    def velocity_check(self, card_id: str, before_dt: str, window_hours: int = 24) -> dict:
        """Txn count + amount for the card in a trailing window."""
        return self.graph.velocity_check(card_id, before_dt, window_hours)

    def device_history(self, customer_id: str, device_id: str, before_dt: str) -> int:
        """Prior uses of this device by this customer (0 = brand new)."""
        return self.graph.device_history(customer_id, device_id, before_dt)

    def customer_baseline(self, customer_id: str, before_dt: str) -> dict:
        """Spending baseline: count, average and max amount before this txn."""
        return self.graph.customer_baseline(customer_id, before_dt)
