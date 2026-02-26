# sentiment_engine.py
import streamlit as st
import torch
from transformers import pipeline

# =====================================================
# KONFIG
# =====================================================
CANDIDATE_TOPICS = [
    "Anggaran", "Kualitas", "Distribusi",
    "Kebijakan", "Sekolah", "Menu Sehat", "Lainnya"
]

# =====================================================
# LOAD MODEL (LAZY + CACHE)  ⬅️ INI KUNCI
# =====================================================
@st.cache_resource
def load_models():
    """
    Model NLP hanya di-load SEKALI dan hanya saat dipanggil.
    Aman untuk Streamlit Cloud (anti 503).
    """
    device = 0 if torch.cuda.is_available() else -1

    model_w11wo = pipeline(
        "sentiment-analysis",
        model="w11wo/indonesian-roberta-base-sentiment-classifier",
        device=device
    )

    model_xlmr = pipeline(
        "sentiment-analysis",
        model="cardiffnlp/twitter-xlm-roberta-base-sentiment-multilingual",
        device=device
    )

    model_topik = pipeline(
        "zero-shot-classification",
        model="valhalla/distilbart-mnli-12-6",
        device=device
    )

    return model_w11wo, model_xlmr, model_topik


# =====================================================
# ANALISIS DUAL MODEL + TOPIK
# =====================================================
def analyze_dual(text, judul=""):
    """
    Dipanggil dari dashboard SAAT tombol ditekan.
    Tidak ada model load di startup.
    """
    if not text or len(str(text).strip()) < 15:
        return "NEUTRAL", 0.0, "NEUTRAL", 0.0, "Lainnya"

    # Ambil model (lazy)
    model_w11wo, model_xlmr, model_topik = load_models()

    try:
        text = str(text)[:512]
        judul = str(judul)[:200]

        # ===== Model 1: RoBERTa Indonesia =====
        r1 = model_w11wo(text)[0]
        s1 = r1["label"].upper()
        c1 = round(float(r1["score"]), 4)

        # ===== Model 2: XLM-R =====
        r2 = model_xlmr(text)[0]
        label_map = {
            "label_0": "NEGATIVE",
            "label_1": "NEUTRAL",
            "label_2": "POSITIVE",
            "negative": "NEGATIVE",
            "neutral": "NEUTRAL",
            "positive": "POSITIVE"
        }
        s2 = label_map.get(r2["label"].lower(), "NEUTRAL")
        c2 = round(float(r2["score"]), 4)

        # ===== Topik =====
        res_t = model_topik(judul, candidate_labels=CANDIDATE_TOPICS)
        topic = res_t["labels"][0] if res_t and "labels" in res_t else "Lainnya"

        return s1, c1, s2, c2, topic

    except Exception as e:
        # Fail-safe (jangan crash app)
        return "NEUTRAL", 0.0, "NEUTRAL", 0.0, "Lainnya"
