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
    Ganti XLM-R dengan IndoBERT
    Model sekarang: RoBERTa-ID + IndoBERT + Topik Classifier
    """
    device = 0 if torch.cuda.is_available() else -1
    
    # Model 1: RoBERTa Indonesia (tetap)
    model_roberta_id = pipeline(
        "sentiment-analysis",
        model="w11wo/indonesian-roberta-base-sentiment-classifier",
        device=device
    )
    
    # MODEL BARU: Ganti XLM-R dengan IndoBERT
    model_indobert = pipeline(
        "sentiment-analysis",
        model="indolem/indobert-base-uncased",  # IndoBERT base
        device=device
    )
    
    # Model 3: Topik Classification (tetap)
    model_topik = pipeline(
        "zero-shot-classification",
        model="valhalla/distilbart-mnli-12-6",
        device=device
    )
    
    return model_roberta_id, model_indobert, model_topik


# =====================================================
# ANALISIS DUAL MODEL + TOPIK
# =====================================================
def analyze_dual(text, judul=""):
    """
    Analisis dengan DUA model bahasa Indonesia:
    1. RoBERTa-ID (spesifik sentiment)
    2. IndoBERT (umum bahasa Indonesia)
    """
    if not text or len(str(text).strip()) < 15:
        return "NEUTRAL", 0.0, "NEUTRAL", 0.0, "Lainnya"
    
    # Load models (sekarang: RoBERTa, IndoBERT, Topik)
    model_roberta, model_indobert, model_topik = load_models()
    
    try:
        text = str(text)[:512]
        judul = str(judul)[:200]
        
        # ===== 1. RoBERTa Indonesia =====
        r1 = model_roberta(text)[0]
        s1 = r1["label"].upper()  # "positive" → "POSITIVE"
        c1 = round(float(r1["score"]), 4)
        
        # ===== 2. INDOBERT (mengganti XLM-R) =====
        r2 = model_indobert(text)[0]
        
        # IndoBERT biasanya output label lowercase
        # Format bisa: "positif", "negatif", "netral"
        label_indobert = r2["label"].lower()
        
        # Mapping label IndoBERT ke format standar
        label_map_indobert = {
            "positif": "POSITIVE",
            "positive": "POSITIVE",
            "negatif": "NEGATIVE", 
            "negative": "NEGATIVE",
            "netral": "NEUTRAL",
            "neutral": "NEUTRAL",
            "label_0": "NEGATIVE",  # Backup mapping
            "label_1": "NEUTRAL",
            "label_2": "POSITIVE"
        }
        
        s2 = label_map_indobert.get(label_indobert, "NEUTRAL")
        c2 = round(float(r2["score"]), 4)
        
        # ===== 3. Topik =====
        res_t = model_topik(judul, candidate_labels=CANDIDATE_TOPICS)
        topic = res_t["labels"][0] if res_t and "labels" in res_t else "Lainnya"
        
        return s1, c1, s2, c2, topic
        
    except Exception as e:
        # Log error untuk debugging
        print(f"Error in analyze_dual: {e}")
        return "NEUTRAL", 0.0, "NEUTRAL", 0.0, "Lainnya"
