"""TigerGraph access layer for the HHGOA_IEEE dataset.

Same client, two personalities:
  * ONLINE  - pyTigerGraph against Savanna/Community Edition, calling
    the installed GSQL queries (the same surface tigergraph-mcp exposes).
  * OFFLINE - answers identical questions from the dataset CSVs with
    pandas. The real transactions.csv is ~708MB with 393 columns, so we
    load ONLY the columns the investigation needs (usecols) - this keeps
    memory sane on a laptop.

Every public method bumps `tool_calls`, because the answer format asks
how many graph/retrieval calls each case consumed.

Card identity: the dataset keys cases by card_id like 'C01234-K1' but
transactions carry card1..card6. We derive card_id per customer as the
K-th unique card signature (card1..card6) ordered by first appearance -
stated openly as an assumption, and stable across runs.
"""
import json
from datetime import timedelta
from pathlib import Path

import pandas as pd

from .config import settings

# The only transaction columns the investigation actually uses. The
# remaining ~370 V/C/D/M columns stay on disk.
_TXN_COLS = ["TransactionID", "customer_id", "ts", "TransactionAmt", "ProductCD",
             "card1", "card2", "card3", "card4", "card5", "card6",
             "addr1", "addr2", "P_emaildomain", "R_emaildomain", "risk_score", "channel"]
_ID_COLS = ["TransactionID", "DeviceType", "DeviceInfo", "id_15", "id_23", "id_30", "id_31", "id_33"]


class GraphClient:
    def __init__(self, data_dir: Path | None = None):
        self.offline = settings.offline_mode
        self.data_dir = Path(data_dir or settings.data_dir)
        self.tool_calls = 0          # per-case metric; investigator resets it
        self._conn = None
        self._txns = None
        self._identity = None
        self._closed = None
        self._card_map = None        # TransactionID -> derived card_id
        self._case_store = self.data_dir / "_written_cases.json"

    def reset_metrics(self) -> None:
        self.tool_calls = 0

    def _count(self) -> None:
        self.tool_calls += 1

    # ------------------------------------------------------------------
    # Connection plumbing (online mode)
    # ------------------------------------------------------------------
    def _connection(self):
        if self._conn is None:
            import pyTigerGraph as tg
            self._conn = tg.TigerGraphConnection(
                host=settings.tg_host, graphname=settings.tg_graph,
                username=settings.tg_username, password=settings.tg_password)
            if settings.tg_secret:
                self._conn.getToken(settings.tg_secret)
        return self._conn

    # ------------------------------------------------------------------
    # Offline loading - lean and lazy
    # ------------------------------------------------------------------
    def _load(self):
        if self._txns is not None:
            return
        tx_path = self.data_dir / "transactions.csv"
        # usecols with a callable tolerates files that lack some columns
        # (the committed sample is a slim subset of the real 393).
        self._txns = pd.read_csv(tx_path, usecols=lambda c: c in _TXN_COLS, dtype=str)
        self._txns["TransactionAmt"] = self._txns["TransactionAmt"].astype(float)
        self._txns["risk_score"] = self._txns["risk_score"].astype(float)
        self._txns["ts"] = pd.to_datetime(self._txns["ts"])
        self._txns.sort_values("ts", inplace=True)

        id_path = self.data_dir / "identity.csv"
        self._identity = (pd.read_csv(id_path, usecols=lambda c: c in _ID_COLS, dtype=str)
                          .set_index("TransactionID") if id_path.exists() else pd.DataFrame())

        cc_path = self.data_dir / "closed_cases_history.csv"
        self._closed = pd.read_csv(cc_path, dtype=str) if cc_path.exists() else pd.DataFrame()

        self._build_card_map()

    def _build_card_map(self):
        """Derive card_id (C01234-K1 style): K-th unique card signature
        per customer, ordered by first appearance. Documented assumption."""
        sig_cols = [c for c in ("card1", "card2", "card3", "card4", "card5", "card6") if c in self._txns.columns]
        df = self._txns
        sig = df[sig_cols].fillna("").agg("|".join, axis=1)
        first_seen = {}
        card_ids = []
        counters = {}
        for cust, s in zip(df["customer_id"], sig):
            key = (cust, s)
            if key not in first_seen:
                counters[cust] = counters.get(cust, 0) + 1
                first_seen[key] = f"{cust}-K{counters[cust]}"
            card_ids.append(first_seen[key])
        df["card_id"] = card_ids
        self._card_map = dict(zip(df["TransactionID"], card_ids))

    def _ident(self, txn_id: str) -> dict:
        if self._identity.empty or txn_id not in self._identity.index:
            return {}
        row = self._identity.loc[txn_id]
        if isinstance(row, pd.DataFrame):   # duplicate index safety
            row = row.iloc[0]
        return row.to_dict()

    @staticmethod
    def device_profile(ident: dict) -> str:
        """Device profile per the README: DeviceInfo + OS + browser + screen."""
        if not ident or pd.isna(ident.get("DeviceInfo", None)):
            return ""
        parts = [str(ident.get(k, "") or "") for k in ("DeviceInfo", "id_30", "id_31", "id_33")]
        return " | ".join(p for p in parts)

    # ------------------------------------------------------------------
    # Investigative queries - each mirrors a GSQL/MCP tool
    # ------------------------------------------------------------------
    def case_context(self, txn_id: str) -> dict:
        """The flagged transaction with its full identity context - the
        analyst's opening view (evidence_subgraph in GSQL terms)."""
        self._count()
        self._load()
        row = self._txns[self._txns["TransactionID"] == str(txn_id)]
        if row.empty:
            return {"found": False}
        r = row.iloc[0]
        ident = self._ident(str(txn_id))
        return {
            "found": True, "txn_id": str(txn_id), "ts": r["ts"],
            "amount": float(r["TransactionAmt"]), "product_cd": r.get("ProductCD", ""),
            "risk_score": float(r["risk_score"]), "channel": r.get("channel", ""),
            "customer_id": r["customer_id"], "card_id": r["card_id"],
            "addr1": r.get("addr1", ""), "addr2": r.get("addr2", ""),
            "email_domain": r.get("P_emaildomain", ""),
            "device_profile": self.device_profile(ident),
            "device_new": (str(ident.get("id_15", "")).lower() == "new") if ident else None,
            "proxy": str(ident.get("id_23", "")).lower() in ("anonymous", "hidden") if ident else False,
        }

    def card_window(self, card_id: str, center_ts, hours: int = 24) -> list[dict]:
        """All transactions on a card around a timestamp - the raw
        material for testing-sequence and burst detection."""
        self._count()
        self._load()
        lo, hi = center_ts - timedelta(hours=hours), center_ts + timedelta(hours=hours)
        w = self._txns[(self._txns["card_id"] == card_id) & (self._txns["ts"] >= lo) & (self._txns["ts"] <= hi)]
        return [{"txn_id": r["TransactionID"], "ts": r["ts"], "amount": float(r["TransactionAmt"]),
                 "product_cd": r.get("ProductCD", ""), "channel": r.get("channel", ""),
                 "addr1": r.get("addr1", "")} for _, r in w.iterrows()]

    def card_history(self, card_id: str, before_ts) -> dict:
        """Behavioural baseline for a card: what 'normal' looks like."""
        self._count()
        self._load()
        h = self._txns[(self._txns["card_id"] == card_id) & (self._txns["ts"] < before_ts)]
        if h.empty:
            return {"txn_count": 0, "avg_amount": 0.0, "max_amount": 0.0,
                    "product_codes": [], "regions": [], "channels": []}
        return {"txn_count": int(len(h)), "avg_amount": float(h["TransactionAmt"].mean()),
                "max_amount": float(h["TransactionAmt"].max()),
                "product_codes": sorted(h["ProductCD"].dropna().unique().tolist()),
                "regions": sorted(h["addr1"].dropna().unique().tolist()),
                "channels": sorted(h["channel"].dropna().unique().tolist())}

    def region_activity(self, card_id: str, region: str, around_ts, days: int = 7) -> dict:
        """Out-of-region analysis: prior history in this region, days of
        activity there in the window (trip detection), and whether
        home-region activity continued in parallel (clone tell)."""
        self._count()
        self._load()
        card_txns = self._txns[self._txns["card_id"] == card_id]
        prior_in_region = int(((card_txns["addr1"] == region) & (card_txns["ts"] < around_ts - timedelta(days=days))).sum())
        lo, hi = around_ts - timedelta(days=days), around_ts + timedelta(days=days)
        window = card_txns[(card_txns["ts"] >= lo) & (card_txns["ts"] <= hi)]
        in_region = window[window["addr1"] == region]
        elsewhere = window[(window["addr1"] != region) & window["addr1"].notna()]
        return {"prior_txns_in_region": prior_in_region,
                "days_active_in_region": int(in_region["ts"].dt.date.nunique()),
                "home_activity_continues": bool(len(elsewhere) > 0),
                "region_txn_ids": in_region["TransactionID"].tolist()}

    def device_neighbors(self, device_profile: str, around_ts, days: int = 30) -> list[dict]:
        """Other cards using the same device profile in the window - the
        shared-origin signal behind R6 and the undocumented patterns."""
        self._count()
        self._load()
        if not device_profile or self._identity.empty:
            return []
        # Compute profiles for identity rows once, then match.
        prof = self._identity.apply(lambda r: self.device_profile(r.to_dict()), axis=1)
        txn_ids = self._identity.index[prof == device_profile].tolist()
        hits = self._txns[self._txns["TransactionID"].isin(txn_ids)]
        lo, hi = around_ts - timedelta(days=days), around_ts + timedelta(days=days)
        hits = hits[(hits["ts"] >= lo) & (hits["ts"] <= hi)]
        out = []
        for card_id, grp in hits.groupby("card_id"):
            out.append({"card_id": card_id, "customer_id": grp["customer_id"].iloc[0],
                        "txn_ids": grp["TransactionID"].tolist()})
        return out

    def recurring_pattern(self, card_id: str, amount: float, ts) -> bool:
        """R7 check: does this charge match the card's own recurring
        rhythm - similar amount (within 10%) roughly monthly?"""
        self._count()
        self._load()
        h = self._txns[(self._txns["card_id"] == card_id) & (self._txns["ts"] < ts)]
        similar = h[abs(h["TransactionAmt"] - amount) <= max(1.0, 0.10 * amount)]
        if len(similar) < 2:
            return False
        gaps = similar["ts"].sort_values().diff().dropna().dt.days
        return bool(((gaps >= 25) & (gaps <= 35)).any())

    # ------------------------------------------------------------------
    # Case memory: closed cases + cases we write back
    # ------------------------------------------------------------------
    def closed_cases(self) -> list[dict]:
        self._count()
        self._load()
        return self._closed.to_dict("records") if not self._closed.empty else []

    def upsert_case(self, case: dict) -> None:
        """Write the case into the graph (FraudCase vertex + edges) or
        the offline JSON store the dashboard and memory read."""
        if not self.offline:
            conn = self._connection()
            conn.upsertVertex("FraudCase", case["graph_case_id"], {
                "status": case.get("status", ""), "pattern": case.get("pattern", ""),
                "confidence": case.get("fraud_probability", 0.0),
                "summary": str(case.get("summary", ""))[:4000],
                "outcome": case.get("verdict", "")})
            for t in case.get("affected_txn_ids", []):
                conn.upsertEdge("FraudCase", case["graph_case_id"], "CASE_TXN", "Transaction", t)
            if case.get("customer_id"):
                conn.upsertEdge("FraudCase", case["graph_case_id"], "CASE_CUSTOMER", "Customer", case["customer_id"])
            return
        existing = json.loads(self._case_store.read_text()) if self._case_store.exists() else []
        existing = [c for c in existing if c.get("graph_case_id") != case.get("graph_case_id")] + [case]
        self._case_store.write_text(json.dumps(existing, indent=2, default=str))

    def written_cases(self) -> list[dict]:
        if self.offline and self._case_store.exists():
            return json.loads(self._case_store.read_text())
        return []
