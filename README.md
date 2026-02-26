

# MBG - Media Analysis



Proyek **MBG (Makan Bergizi Gratis) Media Analysys** adalah sebuah alat otomatis untuk mengumpulkan, memproses, dan menganalisis sentimen dari artikel berita berbahasa Indonesia yang berasal dari berbagai sumber online terkait program Makan Bergizi Gratis. 

## ✨ Fitur Utama

*   **Scraping Berita Multi-Sumber:** Secara otomatis mengumpulkan artikel berita dari portal-portal berita Indonesia terkemuka seperti **Detik.com, Kompas.com, Tempo.co, Tribunnews.com, Republika.co.id, dan PRFM News**.
*   **Analisis Sentimen Berbasis AI:** Menggunakan model AI canggih untuk menganalisis sentimen setiap artikel berita. Model yang digunakan:
    *   **XLM-RoBERTa:** Model multilingual yang di-*fine-tune* untuk analisis sentimen (dalam `migration_xlmr2indobert.py`).
    *   **IndoBERT:** Model BERT yang dikhususkan untuk bahasa Indonesia, digunakan untuk meningkatkan akurasi analisis sentimen pada teks berita lokal.
*   **Penyimpanan Data Terstruktur:** Menyimpan data mentah dan hasil analisis ke dalam dua format:
    *   **CSV:** Untuk kemudahan akses dan analisis data lebih lanjut (contoh: `mbg_articles_*.csv`, `mbg_news_*.csv`).
    *   **SQLite:** Untuk query yang lebih kompleks dan keperluan aplikasi/analitik (file `mbg_analytics.db`).
*   **Proses ETL (Extract, Transform, Load):** Memiliki skrip untuk mentransformasi dan memindahkan data antar format (contoh: `db.py`, `article_transform.csv`, `source_trans.csv`).
*   **Fleksibilitas Skrip:** Setiap sumber berita memiliki skrip scrapingnya sendiri (`mbg_news_detik.py`, `mbg_news_kompas.py`, dll.), memudahkan pemeliharaan dan penambahan sumber baru.
*   **Fitur Pembersihan Data:** Terdapat skrip yang diduga berfungsi untuk membersihkan atau mentransformasi data dari satu format ke format lain, seperti yang terlihat pada `migration_xlmr2indobert.py` yang kemungkinan digunakan untuk migrasi hasil analisis antar model.

## 🗂️ Struktur Proyek

Berikut adalah penjelasan singkat mengenai file-file penting dalam repositori ini:

| Nama File | Deskripsi |
| :--- | :--- |
| **File Utama** |
| `app.py` | **File utama aplikasi.** Berfungsi sebagai *orchestrator* yang mengatur alur kerja scraping dan analisis sentimen secara terpadu. (Penjelasan lebih detail di bawah). |
| `sentiment_engine.py` | Mesin utama untuk analisis sentimen. Memuat model AI (IndoBERT/XLM-R) dan menyediakan fungsi untuk memproses teks dan mengembalikan label sentimen (positif, negatif, netral). |
| `db.py` | Modul untuk menangani semua interaksi dengan basis data SQLite, seperti menyimpan hasil scraping dan analisis. |
| **File Scraping per Sumber** |
| `mbg_news_detik.py` | Scraper khusus untuk portal berita Detik.com. |
| `mbg_news_kompas.py` | Scraper khusus untuk portal berita Kompas.com. |
| `mbg_news_tempo.py` | Scraper khusus untuk portal berita Tempo.co. |
| `mbg_news_tribunnews.py` | Scraper khusus untuk portal berita Tribunnews.com. |
| `mbg_news_republika.py` | Scraper khusus untuk portal berita Republika.co.id. |
| `scrap_pr.py` | Scraper untuk portal berita PRFM News. |
| **File Data & Konfigurasi** |
| `mbg_analytics.db` | Basis data SQLite utama yang menyimpan semua artikel dan hasil analisis sentimennya. |
| `*.csv` | File-file CSV yang berisi data artikel mentah (mentah) maupun data yang sudah ditransformasi. Contoh: `articles_source.csv`, `article_transform.csv`, `mbg_articles_*.csv`. |
| `requirements.txt` | Daftar lengkap pustaka Python yang dibutuhkan untuk menjalankan proyek ini. |
| **File Lainnya** |
| `migration_xlmr2indobert.py` | Skrip untuk migrasi data atau proses ulang analisis sentimen dari model XLM-R ke model IndoBERT. |
| `db ori.py`, `mbg ori.py`, `sentiment_engine ori.py` | Kemungkinan adalah file cadangan (*backup*) atau versi awal dari file utama. |

## ⚙️ Penjelasan Mendalam `app.py`

File `app.py` adalah jantung dari aplikasi ini. Ia bertugas untuk mengoordinasikan seluruh proses dari awal hingga akhir. Berikut adalah fitur-fitur yang diimplementasikan di dalamnya:

1.  **Inisialisasi dan Konfigurasi:**
    *   Mengimpor modul-modul yang diperlukan, seperti `sentiment_engine` untuk analisis, `db` untuk koneksi database, dan scraper-scraper untuk masing-masing sumber berita.
    *   Membaca konfigurasi, seperti daftar sumber berita yang akan dijalankan atau parameter untuk analisis sentimen.

2.  **Orkestrasi Alur Kerja Utama:**
    *   Fungsi utama dalam `app.py` kemungkinan bernama `main()` atau sejenisnya. Fungsi ini akan menjalankan langkah-langkah berikut secara berurutan:
        *   **Scraping:** Memanggil fungsi dari setiap modul scraper (misal: `mbg_news_detik.scrape()`) untuk mendapatkan artikel terbaru.
        *   **Penyimpanan Mentah:** Menyimpan data mentah hasil scraping ke dalam file CSV atau langsung ke tabel sementara di database.
        *   **Analisis Sentimen:** Untuk setiap artikel yang baru masuk, teksnya (judul dan/atau konten) akan dikirim ke fungsi `sentiment_engine.analyze()`.
        *   **Penyimpanan Hasil:** Hasil analisis sentimen (label dan skor) kemudian digabungkan dengan data artikel dan disimpan secara permanen ke dalam tabel utama di `mbg_analytics.db` melalui fungsi dari `db.py`.

3.  **Penanganan Multi-Sumber:**
    *   `app.py` kemungkinan memiliki daftar (*list*) atau konfigurasi yang berisi semua objek scraper. Ia akan melakukan *looping* terhadap daftar tersebut, menjalankan setiap scraper satu per satu, sehingga pengumpulan data dari semua sumber dapat dilakukan dalam satu kali eksekusi program.

4.  **Penjadwalan (Jika Ada):**
    *   Meskipun tidak terlihat dari daftar file, `app.py` dapat dikonfigurasi untuk dijalankan secara berkala (misal setiap jam) menggunakan *cron job* (di Linux/macOS) atau *Task Scheduler* (di Windows). Ini memungkinkan pengumpulan data dilakukan secara otomatis dan berkelanjutan.

5.  **Pencatatan Log (Logging):**
    *   File ini kemungkinan juga mengimplementasikan pencatatan log untuk memantau jalannya proses, mencatat artikel mana yang berhasil di-scrape, dianalisis, serta mencatat jika terjadi error (misal: koneksi internet terputus, website target berubah struktur).

## 🚀 Cara Memulai

Ikuti langkah-langkah berikut untuk menjalankan proyek ini di lingkungan lokal Anda.

### Prasyarat

*   Python 3.8 atau lebih baru
*   `pip` (Python package installer)
*   (Opsional) Virtual environment (seperti `venv` atau `conda`) untuk mengisolasi dependensi proyek.

### Instalasi

1.  **Clone repositori ini:**
    ```bash
    git clone https://github.com/afajarbtz-tech/mbg.git
    cd mbg
    ```

2.  **(Sangat Disarankan) Buat dan aktifkan virtual environment:**
    ```bash
    python -m venv venv
    # Untuk Linux/macOS:
    source venv/bin/activate
    # Untuk Windows:
    .\venv\Scripts\activate
    ```

3.  **Instal dependensi yang diperlukan:**
    ```bash
    pip install -r requirements.txt
    ```
    *Catatan: Pastikan file `requirements.txt` berisi semua pustaka seperti `requests`, `beautifulsoup4`, `transformers`, `torch`, `pandas`, dll.*

### Menjalankan Aplikasi

Untuk memulai proses scraping dan analisis, jalankan file utama:

```bash
python app.py
```

Proses ini mungkin memakan waktu tergantung jumlah artikel yang di-scrape dan kecepatan koneksi internet Anda. Saat pertama kali dijalankan, model AI (IndoBERT/XLM-R) akan diunduh, yang memerlukan koneksi internet stabil dan ruang penyimpanan beberapa ratus MB.

### (Opsional) Menjalankan Scraper Individu

Anda juga dapat menjalankan scraper untuk satu sumber berita saja, misalnya:

```bash
python mbg_news_detik.py
```

Hasilnya akan disimpan dalam file CSV yang sesuai.

## 📊 Hasil dan Output

*   **File CSV:** Artikel dari setiap sesi scraping akan disimpan dalam file CSV dengan format nama `mbg_articles_[rentang]_[timestamp].csv`. File-file ini berisi data mentah artikel.
*   **Database SQLite:** Semua artikel yang berhasil di-scrape dan dianalisis akan tersimpan dalam file `mbg_analytics.db`. Anda dapat menggunakan tools seperti `DB Browser for SQLite` untuk menjelajahi data dan melihat hasil analisis sentimennya.


