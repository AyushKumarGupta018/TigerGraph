# From Uncertain Signal to Defensible Action: an Agentic Fraud Investigator on TigerGraph

*Technical blog post - TigerGraph Agentic Fraud Investigation Hackathon (HHGOA team)*

## What we built

A fraud investigation agent that behaves the way a good analyst does: it does not
classify, it *investigates*. Triggered by a risk signal, a customer report or an
analyst, it opens a case, walks the TigerGraph knowledge graph for evidence, scores
risk and its own confidence separately, requests additional evidence through
policy-approved actions when the picture is murky, and only then commits to a next
best action - executing what policy allows and queueing the rest for human approval.
Every case it closes is written back into the graph, so the corpus of investigations
is itself part of the knowledge graph the next investigation traverses.

## Architecture

Five layers, deliberately decoupled:

1. **TigerGraph knowledge graph** - customers, cards, transactions, devices, IPs,
   merchants, email domains, addresses, fraud cases and typologies, modelled from the
   IEEE-CIS-based HHGOA dataset. Transactions are hub vertices so evidence gathering is
   pure traversal.
2. **GSQL query library** - `evidence_subgraph` (2-hop neighbourhood),
   `shared_attribute_ring` (ring detection via shared device/IP/email/address),
   `velocity_check`, `device_history`, and `similar_cases_structural`. These are the
   agent's analytical tools; exposed via TigerGraph MCP in online mode.
3. **Control plane** - a policy engine (action catalog with auto / approval /
   recommend tiers and named approval routes), an uncertainty model (risk and
   confidence as separate axes plus an evidence-sufficiency gate), and a case manager
   whose audit trail records every mutation.
4. **LangGraph workflow** - trigger, gather, assess, and a conditional loop through
   evidence-request rounds before the disposition nodes. The sufficiency gate - not
   the LLM - controls the loop, which keeps runs reproducible.
5. **GraphRAG + LLM** - the LLM writes narratives grounded in a context pack of graph
   findings, similar-prior-case outcomes and retrieved policy/typology excerpts. It
   never sees raw tables and never makes containment decisions.

## How TigerGraph is used

The graph answers the questions that actually decide investigations: *has this
customer ever used this device?* (account takeover), *who else transacts from this
device?* (rings), *how does this burst compare to baseline?* (card testing,
bust-out), and *what happened in connected prior cases?* (memory). Cases are
first-class vertices with `CASE_TXN` / `CASE_CUSTOMER` edges, so "ring member was in
a confirmed-fraud case" is a one-hop traversal, not a join across systems.

## Agentic capabilities

- **Self-directed evidence gathering** with a hard budget (two rounds) and cheapest-
  decisive-evidence-first ordering: customer validation, then step-up auth.
- **Uncertainty-aware recommendations**: the NBA is snapshotted before each evidence
  request and after the response, and it visibly changes when evidence contradicts
  the model score - a hot transaction the customer confirms gets cleared, not blocked.
- **Policy-bounded autonomy**: reversible containment is autonomous; card blocks,
  freezes, refunds and SAR filings queue for named human roles.
- **Case memory**: explainable similarity (shared entities, same ring, same pattern)
  over prior cases, with outcomes shifting the risk prior in both directions.

## What we learned

Separating risk from confidence was the single highest-leverage decision - it is what
lets the agent say "this looks bad but I am not sure yet" and act proportionately.
And putting cases in the graph turned memory from a feature into a property of the
data model.

## With more time

Vector embeddings on FraudCase for semantic similar-case retrieval at scale; GSQL
community detection (weakly connected components, Louvain) scheduled as a batch to
pre-materialise ring candidates; reinforcement of the sufficiency thresholds from
analyst overrides; and a feedback loop that re-scores closed cases when ring members
resolve later.
