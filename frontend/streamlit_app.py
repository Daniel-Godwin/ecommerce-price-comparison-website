"""Phase 1 Streamlit UI (design doc §13, Phase 1).

Run with:  streamlit run frontend/streamlit_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# make `app` importable when run via `streamlit run frontend/...`
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import plotly.express as px
import streamlit as st

from app.adapters import all_adapters
from app.core.analytics import top_cheapest
from app.core.orchestrator import search

st.set_page_config(
    page_title="Price Compare — Intelligent E-Commerce Price Comparison",
    page_icon="🛒",
    layout="wide",
)

st.title("🛒 Intelligent Price Comparison")
st.caption(
    "Compare product prices across multiple retailers in real time. "
    "Phase 1 core — LLM & RAG intelligence arrives in Phase 3."
)

# ---------------- sidebar ----------------
with st.sidebar:
    st.header("Filters")
    adapter_options = {a.name: a.key for a in all_adapters()}
    selected_names = st.multiselect(
        "Retailers",
        options=list(adapter_options),
        default=list(adapter_options),
        help="DemoStore returns sample data so you can try the app offline.",
    )
    use_cache = st.toggle("Use cache (30 min)", value=True)
    st.divider()
    st.markdown(
        "**Source health** is shown after each search. A failing retailer "
        "never blocks results from the others."
    )

tab_search, tab_ask = st.tabs(["🔍 Compare Prices", "💬 Ask AI"])

# ---------------- search ----------------
with tab_search:
    query = st.text_input(
        "What product are you looking for?",
        placeholder="e.g. Samsung Galaxy A15, rice cooker, HP laptop…",
    )

    if st.button("Search", type="primary") and query.strip():
        keys = [adapter_options[n] for n in selected_names] or None
        with st.spinner("Searching retailers concurrently…"):
            result = search(query, adapter_keys=keys, use_cache=use_cache)

        # source status row
        cols = st.columns(len(result.sources_status) or 1)
        for col, s in zip(cols, result.sources_status, strict=False):
            icon = "✅" if s.ok else "⚠️"
            col.metric(
                f"{icon} {s.retailer}",
                f"{s.listings_found} found",
                f"{s.elapsed_ms} ms",
            )

        if result.from_cache:
            st.info("Served from cache.")

        if not result.listings:
            st.warning(
                "No listings found. Live retailers may be blocking automated "
                "traffic — try including DemoStore, or retry later."
            )
        else:
            a = result.analytics
            if a:
                st.subheader("Price analysis")
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Cheapest", f"{a.currency} {a.min_price:,.0f}")
                m2.metric("Average", f"{a.currency} {a.avg_price:,.0f}")
                m3.metric("Highest", f"{a.currency} {a.max_price:,.0f}")
                m4.metric("Listings", a.count)
                st.success(
                    f"💰 Best deal: **{a.best_deal.product}** at "
                    f"{a.currency} {a.best_deal.price:,.0f} "
                    f"from {a.best_deal.retailer} — [open product page]({a.best_deal.url})"
                )

            st.subheader("Top 3 cheapest")
            for i, item in enumerate(top_cheapest(result.listings, 3), start=1):
                st.markdown(
                    f"**{i}. [{item.product}]({item.url})** — "
                    f"{item.currency} {item.price:,.0f} · {item.retailer}"
                )

            st.subheader("All listings")
            df = pd.DataFrame(
                [
                    {
                        "Product": x.product,
                        "Price": x.price,
                        "Currency": x.currency,
                        "Retailer": x.retailer,
                        "Link": x.url,
                    }
                    for x in result.listings
                ]
            )
            st.dataframe(
                df,
                use_container_width=True,
                column_config={"Link": st.column_config.LinkColumn("Link")},
            )

            st.subheader("Price distribution by retailer")
            fig = px.box(df, x="Retailer", y="Price", points="all")
            st.plotly_chart(fig, use_container_width=True)


# ---------------- Ask AI (Phase 3 RAG) ----------------
with tab_ask:
    st.markdown(
        "Ask a natural-language question — the answer is **grounded in real "
        "retrieved listings** with citations, never invented."
    )
    question = st.text_input(
        "Your question",
        placeholder="e.g. best hp laptop under ₦300,000?",
        key="ask_q",
    )
    live_topup = st.toggle(
        "Search retailers live before answering", value=True,
        help="Runs a fresh search on the parsed intent to top up retrieval.",
    )
    if st.button("Ask", type="primary", key="ask_btn") and question.strip():
        from app.db.session import db_session, init_db
        from app.llm.rag import ask as rag_ask

        init_db()
        with st.spinner("Retrieving listings and generating a grounded answer…"):
            with db_session() as db:
                result = rag_ask(db, question, live_topup=live_topup)

        if result.grounded:
            st.success(result.answer)
        else:
            st.warning(result.answer)

        if result.citations:
            st.subheader("Citations")
            for c in result.citations:
                st.markdown(
                    f"**[{c.n}]** [{c.title}]({c.url}) — "
                    f"{c.currency} {c.price:,.0f} · {c.retailer}"
                )
        with st.expander("Parsed intent (how the AI understood you)"):
            st.json(result.intent.model_dump())
