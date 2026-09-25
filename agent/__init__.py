"""Agentic fraud investigation package.

Modules are deliberately small and single-purpose so each piece can be
tested (and judged!) on its own:

    config        - environment settings
    llm           - provider-agnostic LLM wrapper with a no-key fallback
    graph_client  - TigerGraph access (live cluster or offline CSV mode)
    policy_engine - what the agent MAY do, and who must approve what
    uncertainty   - risk vs confidence scoring + evidence sufficiency
    case_manager  - case lifecycle, audit trail, answer-file export
    memory        - case memory: learn from prior investigations
    graphrag      - ground the LLM in policy docs + live evidence
    actions       - simulated execution layer (freeze, block, SAR, ...)
    tools         - agent tool registry (MCP-compatible surface)
    investigator  - the LangGraph workflow that ties it all together
"""
