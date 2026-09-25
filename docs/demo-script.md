# Demo Script (3-5 minutes)

A tight walkthrough that hits every judging criterion once, in order.

## 0:00 - Setup (one line)
"This is an agentic fraud investigator on TigerGraph: it investigates, decides when it
knows enough, acts within policy, and remembers." Show the architecture diagram from
the README for ~10 seconds.

## 0:20 - Trigger the ambiguous case first (BM-02)
In the dashboard sidebar pick **BM-02** (elevated score, suspected shared
infrastructure) and click Investigate.
- Show the **evidence graph** tab: the transaction hub, and the ring node - another
  customer on the *same device*.
- Show **Case detail**: risk gauge high-ish, confidence gauge middling.

## 1:10 - The agentic moment: it asks before it acts
Open the **Next best action** tab:
- Point at the *before additional evidence* snapshot: hold (reversible), not block -
  and the recorded reasoning about the confidence gap.
- Point at the *after additional evidence* snapshot: customer response received,
  risk and confidence moved, recommendation updated. "Same agent, new evidence,
  different answer - that is the point."

## 2:10 - Policy and humans in the loop
Open the **Approval queue**: card block waits for the fraud analyst, the SAR draft
waits for compliance. Approve one, show the decision land in the case timeline.
"The agent can recommend anything; it can only execute what policy allows."

## 2:50 - Memory
Run **BM-01** (new-device takeover signal). In Case detail, point at the case-memory
evidence line: a similar prior case for this customer was *cleared* (travelling
customer), and that outcome tempered the recommendation.

## 3:30 - The benchmark output
Terminal: `python -m benchmark.run_benchmark` - show the per-case summary lines and
open one answer JSON: full case record, SAR section, NBA before/after blocks.

## 3:55 - Close
"Every case just became part of the graph - the next investigation starts smarter."
