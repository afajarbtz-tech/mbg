# migration_xlmr_to_indobert.py
import sqlite3

def migrate_database():
    conn = sqlite3.connect('mbg_analytics.db')
    cursor = conn.cursor()
    
    # 1. Tambah kolom baru jika belum ada
    cursor.execute("PRAGMA table_info(articles)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if 'sentiment_indobert' not in columns:
        cursor.execute("ALTER TABLE articles ADD COLUMN sentiment_indobert TEXT")
        print("✓ Kolom sentiment_indobert ditambahkan")
    
    if 'confidence_indobert' not in columns:
        cursor.execute("ALTER TABLE articles ADD COLUMN confidence_indobert REAL")
        print("✓ Kolom confidence_indobert ditambahkan")
    
    # 2. Copy data dari XLM-R ke IndoBERT (untuk data yang sudah ada)
    # Hanya jika kolom xlmr masih ada
    if 'sentiment_xlmr' in columns:
        cursor.execute("""
            UPDATE articles 
            SET sentiment_indobert = sentiment_xlmr,
                confidence_indobert = confidence_xlmr
            WHERE sentiment_indobert IS NULL
        """)
        print("✓ Data XLM-R dicopy ke IndoBERT (temporary)")
    
    # 3. Opsional: Hapus kolom lama jika ingin
    # cursor.execute("ALTER TABLE articles DROP COLUMN sentiment_xlmr")
    # cursor.execute("ALTER TABLE articles DROP COLUMN confidence_xlmr")
    
    conn.commit()
    conn.close()
    print("✓ Migrasi database selesai")

if __name__ == "__main__":
    migrate_database()