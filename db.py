import sqlite3

def get_conn():
    conn = sqlite3.connect("mbg_analytics.db")
    conn.row_factory = sqlite3.Row 
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    # Skema baru dengan kolom dual model
    c.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            url TEXT UNIQUE,
            judul TEXT,
            content TEXT,
            published_at TEXT,
            ingested_at TEXT,
            topic TEXT,
            province TEXT,
            sentiment_w11wo TEXT,
            confidence_w11wo REAL,
            sentiment_indobert TEXT,
            confidence_indobert REAL
        )
    """)
    conn.commit()
    conn.close()

def update_article_data(article_id, s1, c1, s2, c2, topic):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        UPDATE articles 
        SET sentiment_w11wo = ?, confidence_w11wo = ?, 
            sentiment_indobert = ?, confidence_indobert = ?, topic = ? 
        WHERE id = ?
    """, (s1, c1, s2, c2, topic, article_id))
    conn.commit()
    conn.close()

def clear_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM articles")
    conn.commit()
    conn.close()

# Tambahkan ini di file db.py
def delete_article_by_id(article_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM articles WHERE id = ?", (article_id,))
    conn.commit()
    conn.close()