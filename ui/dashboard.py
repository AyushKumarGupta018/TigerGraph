# Streamlit dashboard for fraud analyst case review, evidence graph, and approval queues
import json
from pathlib import Path

import networkx as nx
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from agent.config import settings
from agent.investigator import Investigator

st.set_page_config(page_title="TigerGraph Fraud Investigator", layout="wide")

# Subtle clean styling
st.markdown("""
<style>
    .metric-box {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 12px 14px;
        text-align: left;
    }
    .metric-title {
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #64748b;
        margin-bottom: 4px;
    }
    .metric-val {
        font-size: 18px;
        font-weight: 700;
        color: #0f172a;
    }
    
    .evidence-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-left: 4px solid #3b82f6;
        border-radius: 6px;
        padding: 10px 14px;
        margin-bottom: 8px;
    }
    .evidence-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 4px;
    }
    .evidence-claim {
        font-size: 13.5px;
        color: #1e293b;
        line-height: 1.45;
    }
    .evidence-ref {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        font-size: 11px;
        color: #64748b;
        background: #f1f5f9;
        padding: 2px 6px;
        border-radius: 4px;
    }
</style>
""", unsafe_allow_html=True)

st.title("TigerGraph Fraud Investigation Dashboard")

# Initialize stateful investigator session
if "investigator" not in st.session_state:
    st.session_state.investigator = Investigator()
    st.session_state.runs = []
    st.session_state.approvals = {}
inv: Investigator = st.session_state.investigator

# Sidebar case selection
with st.sidebar:
    st.header("Select Case")
    pack_path = settings.data_dir / "case_pack.csv"
    options = {}
    if pack_path.exists():
        for _, r in pd.read_csv(pack_path, dtype=str).iterrows():
            options[f"{r['case_id']} - {str(r['trigger_text'])[:48]}"] = r.to_dict()
    choice = st.selectbox("Case", list(options.keys())) if options else None
    
    if st.button("Run Investigation", type="primary") and choice:
        with st.spinner("Investigating transaction graph..."):
            answer, case = inv.run_case(options[choice])
        st.session_state.runs.append((answer, case))
        st.success(f"Investigation complete: {answer['case_id']} ({answer['case']['verdict'].upper()})")

if not st.session_state.runs:
    st.info("Select a case in the sidebar to run the fraud investigation pipeline.")
    st.stop()

labels = [f"{a['case_id']} | {a['case']['pattern']} | {a['case']['verdict'].upper()}" for a, _ in st.session_state.runs]
idx = st.selectbox("Investigation", range(len(labels)), format_func=lambda i: labels[i],
                   index=len(labels) - 1)
answer, case = st.session_state.runs[idx]
c = answer["case"]

tab_detail, tab_graph, tab_nba, tab_queue, tab_raw = st.tabs(
    ["Case detail", "Evidence graph", "Next best action", "Approval queue", "Answer file"])

with tab_detail:

    col_gauge, col_stats = st.columns([1.1, 1.9])
    
    with col_gauge:
        st.plotly_chart(go.Figure(go.Indicator(
            mode="gauge+number", value=round(c["fraud_probability"] * 100),
            title={"text": f"Fraud probability ({c['verdict'].upper()})", "font": {"size": 16}},
            gauge={"axis": {"range": [0, 100]}, "bar": {"color": "crimson" if c["verdict"] == "fraud" else "seagreen" if c["verdict"] == "legitimate" else "goldenrod"},
                   "steps": [{"range": [0, 30], "color": "#d4edda"},
                             {"range": [30, 70], "color": "#fff3cd"},
                             {"range": [70, 100], "color": "#f8d7da"}]})), use_container_width=True)
    
    with col_stats:
        s1, s2, s3 = st.columns(3)
        with s1:
            st.markdown(f"""
            <div class="metric-box">
                <div class="metric-title">Pattern</div>
                <div class="metric-val" style="font-size: 15px; font-family: monospace;">{c['pattern']}</div>
            </div>
            """, unsafe_allow_html=True)
            st.write("")
            st.markdown(f"""
            <div class="metric-box">
                <div class="metric-title">Exposure (USD)</div>
                <div class="metric-val">${c['exposure_usd']:,.2f}</div>
            </div>
            """, unsafe_allow_html=True)
        with s2:
            st.markdown(f"""
            <div class="metric-box">
                <div class="metric-title">Status</div>
                <div class="metric-val" style="font-size: 16px;">{c['status']}</div>
            </div>
            """, unsafe_allow_html=True)
            st.write("")
            sar_color = "crimson" if answer["sar"]["file"] else "seagreen"
            st.markdown(f"""
            <div class="metric-box">
                <div class="metric-title">SAR Filed</div>
                <div class="metric-val" style="color: {sar_color};">{'Yes' if answer['sar']['file'] else 'No'}</div>
            </div>
            """, unsafe_allow_html=True)
        with s3:
            st.markdown(f"""
            <div class="metric-box">
                <div class="metric-title">Graph Tool Calls</div>
                <div class="metric-val">{answer['tool_calls']}</div>
            </div>
            """, unsafe_allow_html=True)
            st.write("")
            st.markdown(f"""
            <div class="metric-box">
                <div class="metric-title">Latency</div>
                <div class="metric-val">{answer['latency_s']}s</div>
            </div>
            """, unsafe_allow_html=True)

    st.write("")
    st.subheader("Case summary")
    st.info(c["summary"])
    if c.get("pattern_description"):
        st.warning(f"**Undocumented pattern:** {c['pattern_description']}")
    st.caption(f"**Stop reason:** {answer['stop_reason']}")

    st.write("")
    colA, colB = st.columns([3, 2])
    with colA:
        st.subheader("Evidence (with provenance)")
        for e in c["evidence"]:
            src = e["source"].upper()
            tag_color = "#1e40af" if src == "GRAPH" else "#166534" if src == "CUSTOMER" else "#92400e"
            tag_bg = "#dbeafe" if src == "GRAPH" else "#dcfce7" if src == "CUSTOMER" else "#fef3c7"
            ref_badge = f"<span class='evidence-ref'>{e['ref']}</span>" if e.get("ref") else ""
            
            st.markdown(f"""
            <div class="evidence-card" style="border-left-color: {tag_color};">
                <div class="evidence-header">
                    <span style="font-size: 11px; font-weight: 700; color: {tag_color}; background: {tag_bg}; padding: 2px 6px; border-radius: 4px;">{src}</span>
                    {ref_badge}
                </div>
                <div class="evidence-claim">{e['claim']}</div>
            </div>
            """, unsafe_allow_html=True)
            
        if c["similar_prior_cases"]:
            st.markdown(f"**Similar prior cases:** `{', '.join(c['similar_prior_cases'])}`")
            
    with colB:
        st.subheader("Investigation timeline")
        if case.timeline:
            st.dataframe(pd.DataFrame(case.timeline), use_container_width=True, height=260)
        else:
            st.info("Direct benchmark run case record.")
        if answer.get("evidence_requests"):
            st.subheader("Evidence requests (simulated)")
            st.dataframe(pd.DataFrame(answer["evidence_requests"]), use_container_width=True)

    if answer["sar"]["file"]:
        st.write("")
        st.subheader("Suspicious Activity Report (SAR)")
        sar = answer["sar"]
        st.markdown(f"**Reason:** {sar['reason']}")
        st.markdown(f"**Activity Dates:** {sar['activity_dates'][0]} to {sar['activity_dates'][1]} | **Total Amount:** ${sar['total_amount_usd']:,.2f}")
        st.markdown(f"**Subjects:** {', '.join(sar['subjects'])}")
        st.info(f"**Narrative:**\n\n{sar['narrative']}")

with tab_graph:
    G = nx.Graph()
    hub = f"TXN {answer['case_id']}"
    flagged = c["affected_txn_ids"] or ["flagged"]
    G.add_node(hub, kind="transaction")
    for t in flagged[:8]:
        G.add_node(f"txn {t}", kind="txn")
        G.add_edge(hub, f"txn {t}")
    for card in c["connected_card_ids"][:8]:
        G.add_node(f"card {card}", kind="ring")
        G.add_edge(hub, f"card {card}")
    for dp in c["connected_device_profiles"][:2]:
        G.add_node(f"device {dp[:24]}...", kind="device")
        G.add_edge(hub, f"device {dp[:24]}...")
    for cc in c["similar_prior_cases"][:4]:
        G.add_node(f"case {cc}", kind="memory")
        G.add_edge(hub, f"case {cc}")
    pos = nx.spring_layout(G, seed=7)
    ex, ey = [], []
    for a_, b_ in G.edges():
        ex += [pos[a_][0], pos[b_][0], None]
        ey += [pos[a_][1], pos[b_][1], None]
    colors = {"transaction": "crimson", "txn": "darkorange", "ring": "gold", "device": "purple", "memory": "royalblue"}
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=ex, y=ey, mode="lines", line=dict(color="#bbb"), hoverinfo="none"))
    fig.add_trace(go.Scatter(x=[pos[n][0] for n in G.nodes()], y=[pos[n][1] for n in G.nodes()],
                             mode="markers+text", text=list(G.nodes()), textposition="top center",
                             marker=dict(size=18, color=[colors.get(G.nodes[n].get("kind"), "seagreen") for n in G.nodes()])))
    fig.update_layout(showlegend=False, height=500, margin=dict(l=10, r=10, t=10, b=10),
                      xaxis=dict(visible=False), yaxis=dict(visible=False))
    st.plotly_chart(fig, use_container_width=True)

with tab_nba:
    nba = answer["next_best_actions"]
    st.markdown("#### Initial (before requested evidence)")
    st.dataframe(pd.DataFrame(nba["initial"]), use_container_width=True)
    st.markdown("#### Final (after assumed responses)")
    st.dataframe(pd.DataFrame(nba["final"]), use_container_width=True)
    st.info(f"**What changed:** {nba['what_changed']}")

with tab_queue:
    pending = []
    for a_, case_ in st.session_state.runs:
        for rec in a_["next_best_actions"]["final"]:
            if rec["route"] in ("L1", "L2"):
                pending.append((a_["case_id"], rec))
    if not pending:
        st.success("Nothing waiting for approval.")
    for case_id, rec in pending:
        key = f"{case_id}:{rec['action']}"
        col1, col2, col3 = st.columns([4, 1, 1])
        col1.markdown(f"**{rec['action']}** on `{case_id}` - route `{rec['route']}` - {rec['reason']}")
        if key in st.session_state.approvals:
            col2.write(f"Status: **{st.session_state.approvals[key].upper()}**")
        else:
            if col2.button("Approve", key=f"ap-{key}"):
                st.session_state.approvals[key] = "approved"
                st.rerun()
            if col3.button("Reject", key=f"rj-{key}"):
                st.session_state.approvals[key] = "rejected"
                st.rerun()

with tab_raw:
    st.json(json.loads(json.dumps(answer, default=str)))

