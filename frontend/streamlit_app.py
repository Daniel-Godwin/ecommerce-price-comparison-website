"""Interactive Streamlit UI (v2).

Tabs: Compare Prices · Ask AI (chat) · Price History
Sidebar: retailer filters, cache toggle, live LLM cost monitor.

Run with:  streamlit run frontend/streamlit_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import plotly.express as px
import streamlit as st

from app.adapters import all_adapters
from app.core.analytics import top_cheapest
from app.core.orchestrator import search
from app.db.session import db_session, init_db

st.set_page_config(
    page_title="Price Compare — Intelligent E-Commerce Price Comparison",
    page_icon="🛒",
    layout="wide",
)

init_db()

st.title("🛒 Intelligent Price Comparison")
st.caption(
    "Multi-retailer price aggregation with LLM + RAG intelligence — "
    "grounded answers, cited prices, honest failure modes."
)

# ══════════════════════════ sidebar ══════════════════════════
with st.sidebar:
    st.header("Filters")
    adapter_options = {a.name: a.key for a in all_adapters()}
    selected_names = st.multiselect(
        "Retailers",
        options=list(adapter_options),
        default=list(adapter_options),
        help="DemoStore returns sample data so the app works offline.",
    )
    use_cache = st.toggle("Use cache (30 min)", value=True)

    st.divider()
    st.subheader("💵 LLM cost monitor")
    try:
        import os
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import func as sa_func
        from sqlalchemy import select as sa_select

        from app.db.models import LLMCall

        with db_session() as _db:
            since = datetime.now(UTC) - timedelta(days=1)
            spent, calls = _db.execute(
                sa_select(
                    sa_func.coalesce(sa_func.sum(LLMCall.cost_usd), 0.0),
                    sa_func.count(),
                ).where(LLMCall.created_at >= since)
            ).one()
        budget = float(os.getenv("LLM_DAILY_BUDGET_USD", "2.00"))
        st.progress(min(float(spent) / budget, 1.0))
        st.caption(
            f"${float(spent):.4f} of ${budget:.2f} daily budget · "
            f"{int(calls)} calls in 24h"
        )
    except Exception:
        st.caption("cost data unavailable")

# ══════════════════════════ tabs ══════════════════════════
tab_search, tab_ask, tab_history = st.tabs(
    ["🔍 Compare Prices", "💬 Ask AI", "📈 Price History"]
)


def _listing_card(col, listing, rank=None):
    with col, st.container(border=True):
        if listing.image_url:
            st.image(listing.image_url, use_container_width=True)
        title = f"**{rank}. {listing.product}**" if rank else f"**{listing.product}**"
        st.markdown(title)
        st.markdown(f"### {listing.currency} {listing.price:,.0f}")
        st.caption(listing.retailer)
        st.link_button("View deal ↗", listing.url, use_container_width=True)


# ─────────────────────── Compare Prices ───────────────────────
with tab_search:
    query = st.text_input(
        "What product are you looking for?",
        placeholder="e.g. Samsung Galaxy A15, rice cooker, HP laptop…",
    )
    if st.button("Search", type="primary") and query.strip():
        keys = [adapter_options[n] for n in selected_names] or None
        with st.spinner("Searching retailers concurrently…"):
            result = search(query, adapter_keys=keys, use_cache=use_cache)

        cols = st.columns(len(result.sources_status) or 1)
        for col, s in zip(cols, result.sources_status, strict=False):
            icon = "✅" if s.ok else "⚠️"
            col.metric(f"{icon} {s.retailer}", f"{s.listings_found} found",
                       f"{s.elapsed_ms} ms")

        if result.from_cache:
            st.info("⚡ Served from cache.")

        if not result.listings:
            st.warning(
                "No listings found. Live retailers may be blocking automated "
                "traffic — try including DemoStore, or retry later."
            )
        else:
            a = result.analytics
            if a:
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Cheapest", f"{a.currency} {a.min_price:,.0f}")
                m2.metric("Average", f"{a.currency} {a.avg_price:,.0f}")
                m3.metric("Highest", f"{a.currency} {a.max_price:,.0f}")
                m4.metric("Listings", a.count)

            st.subheader("🏆 Top 3 deals")
            top3 = top_cheapest(result.listings, 3)
            for col, (i, item) in zip(
                st.columns(3), enumerate(top3, start=1), strict=False
            ):
                _listing_card(col, item, rank=i)

            with st.expander("📋 All listings", expanded=False):
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
            fig = px.box(
                pd.DataFrame(
                    [{"Retailer": x.retailer, "Price": x.price}
                     for x in result.listings]
                ),
                x="Retailer", y="Price", points="all",
            )
            st.plotly_chart(fig, use_container_width=True)

# ─────────────────────── Ask AI (chat) ───────────────────────
with tab_ask:
    st.markdown(
        "Chat with the AI — answers are **grounded in retrieved listings** "
        "with citations, never invented."
    )
    if "chat" not in st.session_state:
        st.session_state.chat = []

    for msg in st.session_state.chat:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            for c in msg.get("citations", []):
                st.markdown(
                    f"- **[{c['n']}]** [{c['title']}]({c['url']}) — "
                    f"{c['currency']} {c['price']:,.0f} · {c['retailer']}"
                )

    prompt = st.chat_input("e.g. best hp laptop under ₦300,000?")
    if prompt:
        st.session_state.chat.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Retrieving listings and grounding the answer…"):
                from app.llm.rag import ask as rag_ask

                with db_session() as db:
                    result = rag_ask(db, prompt, live_topup=True)

            st.markdown(result.answer)
            citations = [c.model_dump() for c in result.citations]
            for c in citations:
                st.markdown(
                    f"- **[{c['n']}]** [{c['title']}]({c['url']}) — "
                    f"{c['currency']} {c['price']:,.0f} · {c['retailer']}"
                )
            with st.expander("🧠 Parsed intent"):
                st.json(result.intent.model_dump())

        st.session_state.chat.append(
            {"role": "assistant", "content": result.answer,
             "citations": citations}
        )

    if st.session_state.chat and st.button("🗑️ Clear conversation"):
        st.session_state.chat = []
        st.rerun()

# ─────────────────────── Price History ───────────────────────
with tab_history:
    st.markdown(
        "Every search stores a **price snapshot** — over time this builds "
        "real price trends per product and retailer."
    )
    from sqlalchemy import select

    from app.db.models import Product
    from app.db.repository import get_price_history

    with db_session() as db:
        products = db.execute(
            select(Product.id, Product.canonical_title)
            .order_by(Product.id.desc()).limit(200)
        ).all()

    if not products:
        st.info("No products tracked yet — run a search first.")
    else:
        label_by_id = dict(products)
        selected = st.selectbox(
            "Product",
            options=list(label_by_id),
            format_func=lambda pid: f"#{pid} · {label_by_id[pid][:70]}",
        )
        days = st.slider("Window (days)", 1, 90, 30)
        with db_session() as db:
            series = get_price_history(db, selected, days)

        if not series:
            st.info("No snapshots in this window — search this product again.")
        else:
            hist = pd.DataFrame(series)
            hist["captured_at"] = pd.to_datetime(hist["captured_at"])
            c1, c2, c3 = st.columns(3)
            c1.metric("Lowest seen", f"{hist['price'].min():,.0f}")
            c2.metric("Latest", f"{hist.iloc[-1]['price']:,.0f}")
            c3.metric("Snapshots", len(hist))
            fig = px.line(
                hist, x="captured_at", y="price", color="retailer",
                markers=True,
                labels={"captured_at": "Captured", "price": "Price"},
            )
            st.plotly_chart(fig, use_container_width=True)
