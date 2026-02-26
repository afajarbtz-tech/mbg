
import os
import re
import sqlite3
import tempfile
import calendar  # TAMBAH INI
from datetime import datetime, timedelta


import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from wordcloud import WordCloud
import matplotlib.pyplot as plt

# Internal modules (existing)
from Sastrawi.StopWordRemover.StopWordRemoverFactory import StopWordRemoverFactory
from db import get_conn, init_db, clear_db, update_article_data, delete_article_by_id
from sentiment_engine import analyze_dual

# =====================================================
# CONFIG
# =====================================================
st.set_page_config(page_title="BGN Public Insight Dashboard – MBG 2025", layout="wide")

# Update nama model untuk UI
MODEL_DISPLAY_NAMES = {
    "roberta_id": "RoBERTa Indonesia",
    "indobert": "IndoBERT"
}

SENTIMENT_COLORS = {"POSITIVE": "#00CC96", "NEGATIVE": "#EF553B", "NEUTRAL": "#636EFA", "PENDING": "#C0C0C0"}
SENTIMENT_ORDER = ["NEGATIVE", "NEUTRAL", "POSITIVE"]

# --- Light UI polish (minimal, safe) ---
st.markdown("""
<style>
/* Make sidebar slightly tighter */
section[data-testid="stSidebar"] .stMarkdown, 
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] p { font-size: 0.92rem; }
.small-muted { color: #6b7280; font-size: 0.9rem; }
.kpi-note { color: #6b7280; font-size: 0.85rem; margin-top: -10px; }
</style>
""", unsafe_allow_html=True)

# =====================================================
# HELPERS
# =====================================================
def get_sentiment_score(label: str) -> float:
    """Convert sentiment label to numeric score for Mood Index."""
    mapping = {"POSITIVE": 1.0, "NEGATIVE": -1.0, "NEUTRAL": 0.0, None: 0.0, np.nan: 0.0}
    return mapping.get(label, 0.0)

# FUNGSI BARU: Mapping untuk kolom database
def get_model_columns(model_choice: str):
    """
    Return kolom database berdasarkan pilihan model
    model_choice: "RoBERTa (w11wo)" atau "IndoBERT (indobert)"
    """
    if "indobert" in model_choice.lower():
        return "sentiment_indobert", "confidence_indobert", "score_indobert"
    else:  # default ke RoBERTa
        return "sentiment_w11wo", "confidence_w11wo", "score_w11wo"

def normalize_text(s: str) -> str:
    s = str(s or "")
    s = re.sub(r"http\S+|www\.\S+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def ingest_csv_safe(file_path: str) -> dict:
    """
    Safer ingest: INSERT OR IGNORE to avoid UNIQUE(url) failures.
    Returns dict: {"total_rows": int, "inserted": int}
    """
    df = pd.read_csv(file_path)
    mapping = {'tanggal': 'published_at', 'judul': 'judul', 'sumber': 'source', 'content': 'content', 'url': 'url'}
    df = df.rename(columns=mapping)

    # --- PERBAIKAN DI SINI (Konversi Tanggal) ---
    if 'published_at' in df.columns:
        # Ubah string tanggal menjadi objek datetime (otomatis mendeteksi format)
        df['published_at'] = pd.to_datetime(df['published_at'], errors='coerce')
        
        # Ubah format menjadi String ISO (YYYY-MM-DD HH:MM:SS) agar diterima Database
        df['published_at'] = df['published_at'].dt.strftime('%Y-%m-%d %H:%M:%S')
        
        # Ganti NaT (Not a Time) menjadi None agar jadi NULL di SQL
        df['published_at'] = df['published_at'].replace({pd.NaT: None})
    # ---------------------------------------------

    # Minimal required columns
    required = {"url", "judul", "content"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Kolom wajib belum ada: {', '.join(sorted(missing))}")

    df["ingested_at"] = datetime.now().isoformat()

    # Ensure AI columns exist
    ai_cols = ['topic', 'province', 'sentiment_w11wo', 'confidence_w11wo', 
               'sentiment_indobert', 'confidence_indobert']
    for col in ai_cols:
        if col not in df.columns:
            df[col] = None

    # Normalize source a bit
    if "source" in df.columns:
        df["source"] = df["source"].astype(str).str.strip().str.lower()

    # Clean NaN -> None
    df = df.where(pd.notnull(df), None)

    cols = ["source", "url", "judul", "content", "published_at", "ingested_at"] + ai_cols
    cols = [c for c in cols if c in df.columns]
    data = df[cols].to_dict(orient="records")

    conn = get_conn()
    cur = conn.cursor()
    inserted = 0
    for r in data:
        cur.execute("""
            INSERT OR IGNORE INTO articles
            (source, url, judul, content, published_at, ingested_at,
             topic, province, sentiment_w11wo, confidence_w11wo, sentiment_xlmr, confidence_xlmr)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            r.get("source"), r.get("url"), r.get("judul"), r.get("content"),
            r.get("published_at"), r.get("ingested_at"),
            r.get("topic"), r.get("province"),
            r.get("sentiment_w11wo"), r.get("confidence_w11wo"),
            r.get("sentiment_indobert"), r.get("confidence_indobert"),
        ))
        inserted += cur.rowcount
    conn.commit()
    conn.close()
    return {"total_rows": len(data), "inserted": inserted}

@st.cache_data(show_spinner=False)
def load_data() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM articles ORDER BY published_at DESC", conn)
    conn.close()

    if df.empty:
        return df

    # Datetime parsing
    df["published_at"] = pd.to_datetime(df["published_at"], errors="coerce")
    df["ingested_at"] = pd.to_datetime(df.get("ingested_at"), errors="coerce")

    # Make sure expected columns exist
    for c in ["topic", "province", "sentiment_w11wo", "sentiment_xlmr", "confidence_w11wo", "confidence_xlmr"]:
        if c not in df.columns:
            df[c] = None

    # Mood index scores
    df["score_w11wo"] = df["sentiment_w11wo"].apply(get_sentiment_score)
    df["score_indobert"] = df["sentiment_indobert"].apply(get_sentiment_score)
    return df

@st.cache_data(show_spinner=False)
def compute_wordcloud_figure(text_hash: str, text: str):
    factory = StopWordRemoverFactory()
    stop_ind = set(factory.get_stop_words())
    stop_ind.update(["dan", "yang", "mbg", "program", "makan", "gratis", "bgn", "gizi"])
    wc = WordCloud(width=1200, height=350, background_color="white", stopwords=stop_ind).generate(text)
    fig, ax = plt.subplots(figsize=(12, 3.5))
    ax.imshow(wc)
    ax.axis("off")
    return fig

def hash_text(s: str) -> str:
    import hashlib
    return hashlib.md5(s.encode("utf-8", errors="ignore")).hexdigest()

def format_filters_summary(model_name: str, date_range, sources, topics, sentiment_filter, q):
    # UPDATE: Mapping nama model untuk display
    model_display = {
        "RoBERTa Indonesia": "RoBERTa-ID",
        "IndoBERT": "IndoBERT"
    }
    display_name = model_display.get(model_name, model_name)
    
    bits = [f"**Model:** {display_name}"]  # Update label
    if date_range and len(date_range) == 2:
        bits.append(f"**Tanggal:** {date_range[0]} → {date_range[1]}")
    if sources:
        bits.append(f"**Sumber:** {', '.join(sources[:3])}{'…' if len(sources) > 3 else ''}")
    if topics:
        bits.append(f"**Topik:** {', '.join(topics[:3])}{'…' if len(topics) > 3 else ''}")
    if sentiment_filter and sentiment_filter != "Semua":
        bits.append(f"**Sentimen:** {sentiment_filter}")
    if q:
        bits.append(f"**Cari:** “{q}”")
    return " | ".join(bits)

# Tambahkan fungsi helper ini setelah fungsi format_filters_summary
def calculate_monthly_trend(df, date_col="published_at", sentiment_col="sentiment"):
    """
    Hitung trend bulanan dari dataframe
    Returns: DataFrame dengan kolom bulan, total, dan breakdown sentimen
    """
    if df.empty:
        return pd.DataFrame()
    
    df = df.copy()
    df["month"] = df[date_col].dt.to_period("M")
    
    monthly_stats = df.groupby("month").agg(
        total=("id", "count"),
        positive=("id", lambda x: (df.loc[x.index, sentiment_col] == "POSITIVE").sum()),
        negative=("id", lambda x: (df.loc[x.index, sentiment_col] == "NEGATIVE").sum()),
        neutral=("id", lambda x: (df.loc[x.index, sentiment_col] == "NEUTRAL").sum())
    ).reset_index()
    
    # Konversi period ke datetime
    monthly_stats["month_date"] = monthly_stats["month"].dt.to_timestamp()
    
    # Hitung persentase
    monthly_stats["positive_pct"] = (monthly_stats["positive"] / monthly_stats["total"] * 100).round(1)
    monthly_stats["negative_pct"] = (monthly_stats["negative"] / monthly_stats["total"] * 100).round(1)
    monthly_stats["neutral_pct"] = (monthly_stats["neutral"] / monthly_stats["total"] * 100).round(1)
    
    # Hitung mood index
    monthly_stats["mood_index"] = ((monthly_stats["positive"] - monthly_stats["negative"]) / 
                                   monthly_stats["total"]).round(3)
    
    return monthly_stats.sort_values("month_date")


# =====================================================
# INIT DB
# =====================================================
init_db()
df_raw = load_data()

# =====================================================
# SIDEBAR (FILTER + DATA OPS + AI)
# =====================================================
with st.sidebar:
    st.header("🔎 Filter")

    # Model choice (for display only; no rerun unless you click run for pending)
    model_choice = st.radio("Gunakan label sentimen", ["RoBERTa Indonesia", "IndoBERT"], horizontal=False)
    sent_col = "sentiment_w11wo" if "w11wo" in model_choice.lower() else "sentiment_indobert"
    conf_col = "confidence_w11wo" if "w11wo" in model_choice.lower() else "confidence_indobert"

    q = st.text_input("Cari judul/konten", "", placeholder="mis. 'keracunan', 'menu', 'sekolah'")

    # Date range
    date_range = None
    if not df_raw.empty and df_raw["published_at"].notna().any():
        default_start = datetime(2025, 1, 1).date()
        # min_date = df_raw["published_at"].min().date()
        min_date=default_start
        max_date = df_raw["published_at"].max().date()
        date_range = st.date_input("Rentang tanggal", [min_date, max_date])


    # Source filter
    sources_opt = sorted([s for s in df_raw["source"].dropna().unique().tolist()]) if not df_raw.empty else []
    sources = st.multiselect("Sumber", options=sources_opt)

    # Topic filter
    topics_opt = sorted([t for t in df_raw["topic"].dropna().unique().tolist()]) if not df_raw.empty else []
    topics = st.multiselect("Topik", options=topics_opt)

    sentiment_filter = st.selectbox("Sentimen", ["Semua"] + SENTIMENT_ORDER)

    st.divider()
    with st.expander("📥 Upload & Ingest Data", expanded=True):
        uploaded_files = st.file_uploader("Upload CSV berita/survei", type=["csv"], accept_multiple_files=True)
        st.caption("CSV minimal punya kolom: url, judul, content. (tanggal/sumber opsional)")
        if uploaded_files and st.button("🚀 Load CSV"):
            inserted_total = 0
            total_rows = 0
            for file in uploaded_files:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
                    tmp.write(file.getbuffer())
                    tmp_path = tmp.name
                try:
                    res = ingest_csv_safe(tmp_path)
                    total_rows += res["total_rows"]
                    inserted_total += res["inserted"]
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)

            st.success(f"Upload selesai. Masuk DB: {inserted_total} dari {total_rows} baris (sisanya duplikat/diabaikan).")
            st.cache_data.clear()
            st.rerun()

    with st.expander("🧠 Sentiment Analysis (Hanya data baru)", expanded=True):
        pending_mask = None
        pending = 0
        if not df_raw.empty:
            pending_mask = (
                df_raw["sentiment_w11wo"].isna() |
                df_raw["sentiment_indobert"].isna() |
                df_raw["topic"].isna()
            )
            pending = int(pending_mask.sum())

        st.caption(f"Pending AI: **{pending}** baris")
        if st.button("▶️ Jalankan untuk Pending", disabled=(pending == 0)):
            todo = df_raw[pending_mask].copy()
            progress = st.progress(0.0)
            status = st.empty()

            for i, row in enumerate(todo.itertuples(index=False), start=1):
                # Only run for missing fields (but analyze_dual returns all fields)
                s1, c1, s2, c2, topic = analyze_dual(getattr(row, "content", ""), getattr(row, "judul", ""))
                update_article_data(getattr(row, "id"), s1, c1, s2, c2, topic)
                if i % 5 == 0 or i == len(todo):
                    progress.progress(i / len(todo))
                    status.write(f"Memproses {i}/{len(todo)}...")

            st.success("Selesai memproses data pending.")
            st.cache_data.clear()
            st.rerun()

    with st.expander("🧹 Database", expanded=False):
        if st.button("🗑️ Clear DB (Hapus Semua)"):
            clear_db()
            st.cache_data.clear()
            st.rerun()

    st.divider()
    if st.button("Reset filter"):
        for k in ["q", "sources", "topics", "sentiment_filter"]:
            if k in st.session_state:
                del st.session_state[k]
        st.rerun()

# =====================================================
# APPLY FILTERS (NO RE-RUN MODEL)
# =====================================================
df = df_raw.copy()
if not df.empty:
    # Search across title + content
    if q:
        mask = (
            df["judul"].astype(str).str.contains(q, case=False, na=False) |
            df["content"].astype(str).str.contains(q, case=False, na=False)
        )
        df = df[mask]

    # Date filter
    if date_range and len(date_range) == 2 and df["published_at"].notna().any():
        df = df[(df["published_at"].dt.date >= date_range[0]) & (df["published_at"].dt.date <= date_range[1])]

    if sources:
        df = df[df["source"].isin(sources)]

    if topics:
        df = df[df["topic"].isin(topics)]

    if sentiment_filter != "Semua":
        df = df[df[sent_col] == sentiment_filter]

# =====================================================
# MAIN LAYOUT
# =====================================================
st.markdown("## BGN Public Insight Dashboard – MBG 2025")
st.caption("Analisis opini publik untuk mendukung monitoring proses & perbaikan layanan program MBG.")

filters_summary = format_filters_summary(model_choice, date_range, sources, topics, sentiment_filter, q)
st.markdown(f"<div class='small-muted'>{filters_summary}</div>", unsafe_allow_html=True)

if df_raw.empty:
    st.info("Database kosong. Upload CSV dulu lewat sidebar.")
    st.stop()

tabs = st.tabs(["📌 Ringkasan Eksekutif", "📈 Tren & Sentimen", "🏷️ Topik & Kata Kunci", "📰 Media", "🗂️ Data Detail"])

# =====================================================
# TAB 1 — EXEC SUMMARY
# =====================================================
with tabs[0]:
    total = len(df)
    neg = int((df[sent_col] == "NEGATIVE").sum()) if total else 0
    pos = int((df[sent_col] == "POSITIVE").sum()) if total else 0
    neu = int((df[sent_col] == "NEUTRAL").sum()) if total else 0
    neg_rate = (neg / total * 100.0) if total else 0.0
    mood = ((pos - neg) / total) if total else 0.0

    top_topic = "-"
    if "topic" in df.columns and df["topic"].notna().any():
        top_topic = df["topic"].mode().iloc[0]

    top_source = "-"
    if "source" in df.columns and df["source"].notna().any():
        top_source = df["source"].mode().iloc[0]

    # KPI
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total feedback", f"{total:,}")
    k2.metric("Negativity rate", f"{neg_rate:.1f}%")
    k3.metric("Mood index", f"{mood:.2f}")
    k4.metric("Top topic", top_topic)
    k5.metric("Top source", top_source)

    st.markdown("<div class='kpi-note'>Mood index = (POS - NEG) / total. Fokus eksekutif: lihat tren NEG dan topik pemicu.</div>", unsafe_allow_html=True)

    st.divider()

    # Trend preview + Composition
    left, right = st.columns([2, 1])
    with left:
        tmp = df.dropna(subset=["published_at"]).copy()
        if not tmp.empty:
            tmp["date"] = tmp["published_at"].dt.date
            trend = tmp.groupby(["date", sent_col]).size().reset_index(name="count")
            # Keep order
            trend[sent_col] = pd.Categorical(trend[sent_col], categories=SENTIMENT_ORDER, ordered=True)
            fig = px.line(trend, x="date", y="count", color=sent_col, markers=True,
                          color_discrete_map=SENTIMENT_COLORS)
            fig.update_layout(legend_title_text="Sentimen", margin=dict(l=10, r=10, t=30, b=10))
            st.plotly_chart(fig, use_container_width=True, key="plotly_1")
        else:
            st.info("Tidak ada data bertanggal untuk membuat tren.")

    with right:
        comp = df[sent_col].value_counts(dropna=False).reindex(SENTIMENT_ORDER).fillna(0).reset_index()
        comp.columns = ["sentiment", "count"]
        fig2 = px.pie(comp, values="count", names="sentiment", hole=0.6,
                      color="sentiment", color_discrete_map=SENTIMENT_COLORS)
        fig2.update_layout(margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig2, use_container_width=True, key="plotly_2")

    # Crisis brief
    st.subheader("🚨 Crisis Brief (Ringkas)")
    c1, c2 = st.columns([1.4, 1])
    with c1:
        neg_df = df[df[sent_col] == "NEGATIVE"].copy()
        show_cols = ["published_at", "source", "topic", "judul", conf_col]
        show_cols = [c for c in show_cols if c in neg_df.columns]
        st.dataframe(
            neg_df.sort_values(["published_at"], ascending=False).head(10)[show_cols],
            use_container_width=True,
            hide_index=True
        )
    with c2:
        if not df.dropna(subset=["published_at"]).empty:
            tmp = df.dropna(subset=["published_at"]).copy()
            tmp["date"] = tmp["published_at"].dt.date
            neg_daily = tmp[tmp[sent_col] == "NEGATIVE"].groupby("date").size().reset_index(name="neg_count")
            if not neg_daily.empty:
                thr = float(neg_daily["neg_count"].mean() + 2 * neg_daily["neg_count"].std()) if len(neg_daily) > 7 else 20.0
                fig_spike = px.line(neg_daily, x="date", y="neg_count", markers=True)
                fig_spike.add_hline(y=thr, line_dash="dash", annotation_text="Alert threshold")
                fig_spike.update_layout(title="Lonjakan berita NEG per hari", margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig_spike, use_container_width=True, key="plotly_3")
            else:
                st.info("Belum ada item NEG untuk menghitung lonjakan.")
        else:
            st.info("Data tidak punya tanggal yang valid.")

# =====================================================
# TAB 2 — TREND & SENTIMENT
# =====================================================
with tabs[1]:
    st.subheader("📈 Tren Sentimen (lebih detail)")

    tmp = df.dropna(subset=["published_at"]).copy()
    if tmp.empty:
        st.info("Tidak ada data bertanggal untuk tren.")
    else:
        gran = st.radio("Agregasi", ["Harian", "Mingguan", "Bulanan"], horizontal=True)
        tmp["date"] = tmp["published_at"].dt.to_period("W").dt.start_time.dt.date if gran == "Mingguan" else tmp["published_at"].dt.date

        # UPDATE: Tambah logika untuk agregasi bulanan
        if gran == "Bulanan":
            tmp["period"] = tmp["published_at"].dt.to_period("M").dt.to_timestamp()
            period_label = "Bulan"
        elif gran == "Mingguan":
            tmp["period"] = tmp["published_at"].dt.to_period("W").dt.start_time.dt.date
            tmp["period"] = pd.to_datetime(tmp["period"])  # Convert to datetime untuk konsistensi
            period_label = "Minggu"
        else:  # Harian
            tmp["period"] = tmp["published_at"].dt.date
            tmp["period"] = pd.to_datetime(tmp["period"])  # Convert to datetime
            period_label = "Hari"
        
       # Hitung trend dengan period
        trend = tmp.groupby(["period", sent_col]).size().reset_index(name="count")
        trend[sent_col] = pd.Categorical(trend[sent_col], categories=SENTIMENT_ORDER, ordered=True)

        # Plot area chart
        fig = px.area(trend, x="period", y="count", color=sent_col, groupnorm="",
                      color_discrete_map=SENTIMENT_COLORS)
        fig.update_layout(
            legend_title_text="Sentimen", 
            margin=dict(l=10, r=10, t=30, b=10),
            xaxis_title=f"Periode ({period_label.lower()})",
            yaxis_title="Jumlah Artikel"
        )

        # Format x-axis berbeda untuk bulanan
        if gran == "Bulanan":
            fig.update_xaxes(
                tickformat="%b %Y",  # Format: Jan 2025, Feb 2025, dst
                dtick="M1",  # Satu bulan interval
                tickangle=45
            )
        elif gran == "Mingguan":
            fig.update_xaxes(
                tickformat="%b %Y",  # Format: 01 Jan
                dtick="M1",  # Satu minggu interval
                tickangle=45
            )
        else:  # Harian
            fig.update_xaxes(
                tickformat="%b %Y",
                dtick="M1",  # 7 hari interval untuk readability
                tickangle=45
            )
        st.plotly_chart(fig, use_container_width=True, key="plotly_4")

        st.divider()

        # =====================================================
        # TREN BULANAN DETAIL (Tabel dan Chart Komparasi)
        # =====================================================
        st.subheader("📊 Analisis Bulanan Detail")
        
        # Buat data agregasi bulanan khusus
        tmp_monthly = tmp.copy()
        tmp_monthly["month"] = tmp_monthly["published_at"].dt.to_period("M")
        tmp_monthly["month_str"] = tmp_monthly["published_at"].dt.strftime("%Y-%m")  # Format: 2025-01
        
        # Hitung metrik bulanan
        monthly_stats = tmp_monthly.groupby("month").agg(
            total_articles=("id", "count"),
            positive=("id", lambda x: int((tmp_monthly.loc[x.index, sent_col] == "POSITIVE").sum())),
            negative=("id", lambda x: int((tmp_monthly.loc[x.index, sent_col] == "NEGATIVE").sum())),
            neutral=("id", lambda x: int((tmp_monthly.loc[x.index, sent_col] == "NEUTRAL").sum()))
        ).reset_index()
        
        # Konversi period ke datetime untuk plotting
        monthly_stats["month_date"] = monthly_stats["month"].dt.to_timestamp()
        
        # Hitung persentase
        monthly_stats["positive_pct"] = (monthly_stats["positive"] / monthly_stats["total_articles"] * 100).round(1)
        monthly_stats["negative_pct"] = (monthly_stats["negative"] / monthly_stats["total_articles"] * 100).round(1)
        monthly_stats["neutral_pct"] = (monthly_stats["neutral"] / monthly_stats["total_articles"] * 100).round(1)
        
        # Hitung mood index bulanan
        monthly_stats["mood_index"] = ((monthly_stats["positive"] - monthly_stats["negative"]) / 
                                       monthly_stats["total_articles"]).round(3)
        
        # Urutkan dari bulan terbaru
        monthly_stats = monthly_stats.sort_values("month_date", ascending=False)
        
        # Tampilkan dalam 2 kolom
        col_month1, col_month2 = st.columns([1.5, 1])
        
        with col_month1:
            st.markdown("**📈 Trend Persentase Sentimen Bulanan**")
            
            # Stacked area chart untuk persentase
            fig_monthly_pct = px.area(
                monthly_stats, 
                x="month_date", 
                y=["positive_pct", "neutral_pct", "negative_pct"],
                labels={"value": "Persentase (%)", "month_date": "Bulan", "variable": "Sentimen"},
                color_discrete_map={
                    "positive_pct": SENTIMENT_COLORS["POSITIVE"],
                    "neutral_pct": SENTIMENT_COLORS["NEUTRAL"],
                    "negative_pct": SENTIMENT_COLORS["NEGATIVE"]
                }
            )
            
            fig_monthly_pct.update_layout(
                xaxis_title="Bulan",
                yaxis_title="Persentase (%)",
                margin=dict(l=10, r=10, t=30, b=10),
                legend_title="Sentimen",
                hovermode="x unified"
            )
            
            fig_monthly_pct.update_xaxes(
                tickformat="%b %Y",
                dtick="M1",
                tickangle=45
            )
            
            fig_monthly_pct.for_each_trace(lambda t: t.update(name=t.name.replace("_pct", "").title()))
            
            st.plotly_chart(fig_monthly_pct, use_container_width=True, key="plotly_monthly_pct")
        
        with col_month2:
            st.markdown("**📊 Tabel Ringkasan Bulanan**")
            
            # Buat tabel ringkasan
            summary_table = monthly_stats.copy()
            summary_table["Bulan"] = summary_table["month"].dt.strftime("%b %Y")
            summary_table = summary_table[["Bulan", "total_articles", "positive_pct", "negative_pct", "mood_index"]]
            summary_table.columns = ["Bulan", "Total", "Pos%", "Neg%", "Mood"]
            
            # Format untuk display
            display_table = summary_table.copy()
            display_table["Pos%"] = display_table["Pos%"].apply(lambda x: f"{x}%")
            display_table["Neg%"] = display_table["Neg%"].apply(lambda x: f"{x}%")
            display_table["Mood"] = display_table["Mood"].apply(lambda x: f"{x:.3f}")
            
            st.dataframe(
                display_table,
                use_container_width=True,
                hide_index=True,
                height=400
            )
            
            # Download button untuk data bulanan
            csv_monthly = summary_table.to_csv(index=False, encoding="utf-8-sig")
            st.download_button(
                "📥 Download Data Bulanan",
                data=csv_monthly,
                file_name="sentimen_bulanan.csv",
                mime="text/csv",
                key="download_monthly"
            )
        
        st.divider()
        
        # =====================================================
        # HEATMAP TREN BULANAN (Visualisasi Matrix)
        # =====================================================
        st.subheader("🌡️ Heatmap Tren Bulanan")
        
        # Siapkan data untuk heatmap (hari dalam bulan)
        tmp_heatmap = tmp.copy()
        tmp_heatmap["year_month"] = tmp_heatmap["published_at"].dt.strftime("%Y-%m")
        tmp_heatmap["day_of_month"] = tmp_heatmap["published_at"].dt.day
        tmp_heatmap["week_of_month"] = ((tmp_heatmap["published_at"].dt.day - 1) // 7 + 1).astype(int)
        
        # Pilih bulan untuk heatmap
        available_months = sorted(tmp_heatmap["year_month"].unique(), reverse=True)
        selected_month = st.selectbox(
            "Pilih bulan untuk heatmap:",
            options=available_months,
            index=0 if available_months else None,
            key="heatmap_month"
        )
        
        if selected_month:
            month_data = tmp_heatmap[tmp_heatmap["year_month"] == selected_month].copy()
            
            # Hitung sentimen per hari
            daily_sentiment = pd.crosstab(
                month_data["day_of_month"], 
                month_data[sent_col],
                normalize="index"
            ).fillna(0) * 100
            
            # Reindex untuk semua hari dalam bulan
            import calendar
            year, month = map(int, selected_month.split("-"))
            days_in_month = calendar.monthrange(year, month)[1]
            all_days = pd.DataFrame(index=range(1, days_in_month + 1))
            daily_sentiment = all_days.join(daily_sentiment, how="left").fillna(0)
            
            # Buat heatmap
            fig_heatmap = px.imshow(
                daily_sentiment.T,  # Transpose agar hari sebagai kolom
                labels=dict(x="Hari dalam Bulan", y="Sentimen", color="Persentase (%)"),
                x=list(range(1, days_in_month + 1)),
                y=daily_sentiment.columns.tolist(),
                aspect="auto",
                color_continuous_scale="RdYlGn",  # Red-Yellow-Green scale
                zmin=0,
                zmax=100
            )
            
            fig_heatmap.update_layout(
                title=f"Heatmap Sentimen Harian - {selected_month}",
                xaxis_title="Hari",
                yaxis_title="Sentimen",
                margin=dict(l=10, r=10, t=50, b=10)
            )
            
            st.plotly_chart(fig_heatmap, use_container_width=True, key="plotly_heatmap")
            
            # Tabel detail di bawah heatmap
            with st.expander("📋 Detail Data Harian"):
                daily_summary = month_data.groupby("day_of_month").agg(
                    total=("id", "count"),
                    positive=("id", lambda x: (month_data.loc[x.index, sent_col] == "POSITIVE").sum()),
                    negative=("id", lambda x: (month_data.loc[x.index, sent_col] == "NEGATIVE").sum()),
                    neutral=("id", lambda x: (month_data.loc[x.index, sent_col] == "NEUTRAL").sum())
                ).reset_index()
                
                daily_summary["day_of_month"] = daily_summary["day_of_month"].astype(int)
                daily_summary = daily_summary.sort_values("day_of_month")
                
                # Hitung persentase
                daily_summary["positive_pct"] = (daily_summary["positive"] / daily_summary["total"] * 100).round(1)
                daily_summary["negative_pct"] = (daily_summary["negative"] / daily_summary["total"] * 100).round(1)
                daily_summary["neutral_pct"] = (daily_summary["neutral"] / daily_summary["total"] * 100).round(1)
                
                daily_summary.columns = ["Hari", "Total", "Positif", "Negatif", "Netral", "Pos%", "Neg%", "Net%"]
                
                st.dataframe(
                    daily_summary,
                    use_container_width=True,
                    hide_index=True
                )
        
        st.divider()
        
        # =====================================================
        # MOOD INDEX COMPARISON (Tetap pertahankan)
        # =====================================================
        st.subheader("🔄 Public Mood Index (RoBERTa vs IndoBERT)")
        mood = tmp.groupby("period")[["score_w11wo", "score_indobert"]].mean().reset_index()
        
        # Rename untuk display
        mood_display = mood.rename(columns={
            "score_w11wo": "RoBERTa Indonesia",
            "score_indobert": "IndoBERT",
            "period": "Periode"
        })
        
        figm = px.line(mood_display, x="Periode", y=["RoBERTa Indonesia", "IndoBERT"], markers=True)
        figm.update_layout(
            margin=dict(l=10, r=10, t=30, b=10),
            yaxis_title="Mood Index",
            legend_title="Model",
            xaxis_title=f"Periode ({period_label.lower()})"
        )
        
        # Format x-axis sesuai granularity
        if gran == "Bulanan":
            figm.update_xaxes(tickformat="%b %Y", dtick="M1", tickangle=45)
        elif gran == "Mingguan":
            figm.update_xaxes(tickformat="%d %b", dtick="W1")
        
        st.plotly_chart(figm, use_container_width=True, key="plotly_5")
        
        st.divider()
        
        st.subheader("✅ Model Agreement (ringkas)")
        a = tmp[["sentiment_w11wo", "sentiment_indobert"]].dropna()
        if not a.empty:
            agree = pd.crosstab(a["sentiment_w11wo"], a["sentiment_indobert"])
            fig_h = px.imshow(agree, text_auto=True, aspect="auto")
            fig_h.update_layout(
                margin=dict(l=10, r=10, t=30, b=10),
                xaxis_title="IndoBERT",
                yaxis_title="RoBERTa Indonesia"
            )
            st.plotly_chart(fig_h, use_container_width=True)
        else:
            st.info("Tidak cukup data untuk menghitung agreement.")

# =====================================================
# TAB 3 — TOPIC & KEYWORDS
# =====================================================
with tabs[2]: 
    # Urutan topik fixed (sesuai sentiment_engine)
    TOPIC_ORDER = ["Anggaran", "Kualitas", "Distribusi", "Kebijakan", "Sekolah", "Menu Sehat", "Lainnya"]

    st.subheader("🏷️ Topik Dominan (Semua Label)")

    if "topic" not in df.columns or df["topic"].isna().all():
        st.info("Kolom topik masih kosong / belum diproses.")
    else:
        # Hitung semua topik, pastikan yang 0 tetap muncul
        topic_counts = (
            df["topic"]
            .fillna("Lainnya")
            .value_counts()
            .reindex(TOPIC_ORDER, fill_value=0)
            .reset_index()
        )
        topic_counts.columns = ["topic", "count"]

        figt = px.bar(
            topic_counts,
            x="topic",
            y="count",
            category_orders={"topic": TOPIC_ORDER},
            text="count"
        )
        figt.update_layout(
            xaxis_title="Topik",
            yaxis_title="Jumlah",
            margin=dict(l=10, r=10, t=30, b=10)
        )
        st.plotly_chart(figt, use_container_width=True, key="plotly_8")

    st.divider()
    with st.expander("☁️ Word Cloud (Global Keywords)", expanded=False):
        text = " ".join(df["content"].dropna().astype(str).map(normalize_text).tolist())
        if text.strip():
            fig_wc = compute_wordcloud_figure(hash_text(text), text)
            st.pyplot(fig_wc)
        else:
            st.info("Konten kosong, tidak bisa membuat wordcloud.")

# =====================================================
# TAB 4 — MEDIA
# =====================================================
with tabs[3]:
    st.subheader("📰 Media Leaderboard")

    if "source" not in df.columns or df["source"].isna().all():
        st.info("Kolom source kosong.")
    else:
        # Leaderboard with NEG rate
        agg = df.groupby("source").agg(
            mentions=("judul", "count"),
            neg=("id", lambda x: int((df.loc[x.index, sent_col] == "NEGATIVE").sum())),
        ).reset_index()

        agg["neg_rate"] = np.where(agg["mentions"] > 0, (agg["neg"] / agg["mentions"]) * 100.0, 0.0)

        # Top topic per source (optional)
        if "topic" in df.columns and df["topic"].notna().any():
            top_topic_by_src = df.groupby("source")["topic"].agg(lambda x: x.mode().iloc[0] if not x.mode().empty else "-").reset_index()
            agg = agg.merge(top_topic_by_src, on="source", how="left")
        else:
            agg["topic"] = "-"

        c1, c2 = st.columns([1.6, 1])
        with c1:
            st.dataframe(
                agg.sort_values("mentions", ascending=False),
                use_container_width=True,
                hide_index=True
            )
        with c2:
            figm = px.bar(agg.sort_values("mentions", ascending=False).head(15), x="source", y="mentions")
            figm.update_layout(xaxis_title="", margin=dict(l=10, r=10, t=30, b=10))
            st.plotly_chart(figm, use_container_width=True, key="plotly_9")

# =====================================================
# TAB 5 — DATA DETAIL
# =====================================================
# =====================================================
# TAB 5 — DATA DETAIL
# =====================================================
with tabs[4]:
    st.subheader("🗂️ Data Detail (Filter-aware)")

     # UPDATE: Tambah kolom indobert
    target_cols = ["id", "published_at", "ingested_at", "source", "topic", 
                   sent_col, conf_col, "judul", "url"]
    view_cols = [c for c in target_cols if c in df.columns]

    # UPDATE: Untuk tampilan lengkap, tambah kedua kolom sentimen
    if st.checkbox("Tampilkan kedua hasil model", False):
        extra_cols = ["sentiment_w11wo", "confidence_w11wo", 
                     "sentiment_indobert", "confidence_indobert"]  # Ganti xlmr jadi indobert
        for col in extra_cols:
            if col in df.columns and col not in view_cols:
                view_cols.append(col)

    # 1. Update list kolom agar mencakup 'ingested_at'
    # 'ingested_at' kita taruh setelah 'published_at' agar mudah dibandingkan
    target_cols = ["id", "published_at", "ingested_at", "source", "topic", sent_col, conf_col, "judul", "url"]
    view_cols = [c for c in target_cols if c in df.columns]
    
    show = df.copy()

    # 2. Format tanggal agar lebih rapi (Hilangkan detik/mikrodetik yang tidak perlu)
    if "published_at" in show.columns:
        show["published_at"] = pd.to_datetime(show["published_at"]).dt.strftime("%Y-%m-%d")
    
    # Format ingested_at (Tampilkan jam menit karena ini waktu sistem proses)
    if "ingested_at" in show.columns:
        show["ingested_at"] = pd.to_datetime(show["ingested_at"]).dt.strftime("%Y-%m-%d %H:%M")

    table = show[view_cols].copy()
    table.insert(0, "Hapus", False)

    edited = st.data_editor(
        table,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Hapus": st.column_config.CheckboxColumn("Hapus", help="Centang untuk menghapus baris ini dari DB"),
            "ingested_at": st.column_config.TextColumn("Waktu Masuk (System)", help="Waktu data dicatat oleh sistem"),
            "published_at": st.column_config.TextColumn("Tanggal Terbit (Pub)", help="Waktu berita/opini tayang"),
            "url": st.column_config.LinkColumn("Link") # Bonus: URL jadi bisa diklik langsung
        },
        disabled=[c for c in table.columns if c not in ["Hapus"]],
        height=420
    )

    colA, colB, colC = st.columns([1, 1, 2])
    with colA:
        if st.button("🔥 Hapus Baris Terpilih"):
            selected_ids = edited[edited["Hapus"] == True]["id"].tolist()
            if selected_ids:
                for d_id in selected_ids:
                    delete_article_by_id(int(d_id))
                st.success(f"Berhasil menghapus {len(selected_ids)} baris.")
                st.cache_data.clear()
                st.rerun()
            else:
                st.warning("Pilih setidaknya satu baris untuk dihapus.")

    with colB:
        # Download CSV tetap bersih tanpa kolom score mood index
        csv = df.drop(columns=["score_w11wo", "score_xlmr"], errors="ignore").to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Download CSV (hasil filter)", data=csv, file_name="mbg_filtered.csv", mime="text/csv")

    with colC:
        st.caption("Klik judul di bawah untuk melihat konten lengkap (drill-down).")
        pick = st.selectbox("Detail item", options=df["judul"].fillna("-").astype(str).head(200).tolist())
        
        row = df[df["judul"].astype(str) == str(pick)].head(1)
        if not row.empty:
            r = row.iloc[0]
            
            # Tampilan Detail Card
            with st.container():
                st.markdown(f"### {r.get('judul', '-')}")
                c_info1, c_info2 = st.columns(2)
                with c_info1:
                    st.markdown(f"**Published:** {r.get('published_at')}")
                    st.markdown(f"**Ingested (System):** {r.get('ingested_at', '-')}") # Ditambahkan di sini juga
                    st.markdown(f"**Sumber:** {r.get('source', '-')}")
                with c_info2:
                    st.markdown(f"**Topik:** {r.get('topic', '-')}")
                    st.markdown(f"**Sentimen:** {r.get(sent_col, '-')}" + (f" (conf {float(r.get(conf_col, 0) or 0):.2f})" if conf_col in row.columns else ""))
                    st.markdown(f"[Buka Link Asli]({r.get('url', '#')})")
                
                st.divider()
                st.markdown("**Konten Lengkap:**")
                st.info(r.get("content", ""))
