import re  # regex untuk parsing teks list (tanggal/judul) dan fallback author di detail
import time  # sleep/delay agar tidak request terlalu cepat
import random  # delay acak supaya tidak berpola bot
from datetime import datetime  # membuat created_at + parsing ISO timestamp
from zoneinfo import ZoneInfo  # timezone WIB (Asia/Jakarta) untuk created_at & konversi tanggal
from urllib.parse import urljoin, urlparse  # normalisasi URL: relative->absolute dan buang fragment

import pandas as pd  # simpan hasil scraping dalam DataFrame + export ke CSV
from bs4 import BeautifulSoup  # parsing HTML list & detail
from playwright.sync_api import sync_playwright  # browser automation untuk render JS/lazy-load


# =========================
# CONFIG
# =========================
BASE = "https://www.detik.com"  # base domain, dipakai untuk join URL relatif
TAG_URL = "https://www.detik.com/tag/news/makan-bergizi-gratis/"  # halaman tag/news (ada paging: ?page=&sortby=)

WIB = ZoneInfo("Asia/Jakarta")  # definisi timezone WIB untuk konversi datetime

HEADERS = {  # header agar akses terlihat seperti browser normal
    "User-Agent": (  # identitas browser
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",  # prefer bahasa Indonesia
}


# =========================
# HELPERS
# =========================
def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()  # rapikan whitespace jadi 1 spasi + trim

def normalize_url(u: str) -> str:
    if not u:  # kalau href kosong
        return ""  # return string kosong
    if u.startswith("/"):  # kalau URL relatif seperti "/jabar/berita/..."
        u = urljoin(BASE, u)  # gabung dengan BASE jadi URL absolut
    parsed = urlparse(u)  # parsing komponen URL
    return parsed._replace(fragment="").geturl()  # hapus "#fragment" agar dedup URL konsisten

def is_article_url(url: str) -> bool:
    # Artikel detik biasanya punya pola /d-<angka>/ pada URL (mis. .../d-8183382/...)
    return bool(url) and bool(re.search(r"/d-\d+/", url))  # filter link non-artikel

def extract_meta(soup: BeautifulSoup, name=None, prop=None) -> str:
    if prop:  # kalau cari <meta property="...">
        m = soup.find("meta", attrs={"property": prop})  # temukan meta property
        if m and m.get("content"):  # pastikan meta ada dan punya content
            return clean_text(m["content"])  # kembalikan konten meta yang sudah dirapikan
    if name:  # kalau cari <meta name="...">
        m = soup.find("meta", attrs={"name": name})  # temukan meta name
        if m and m.get("content"):  # pastikan ada content
            return clean_text(m["content"])  # kembalikan konten meta
    return ""  # fallback jika meta tidak ditemukan

def iso_to_wib(iso_str: str) -> str:
    """
    Konversi ISO publish time -> "YYYY-mm-dd HH:MM:SS WIB"
    - Support ISO dengan offset: 2025-12-17T01:02:03+00:00
    - Support ISO dengan Z:      2025-12-17T01:02:03Z
    - ISO tanpa timezone dianggap UTC (fallback)
    """
    if not iso_str:  # jika string ISO kosong
        return ""  # tidak bisa konversi

    s = iso_str.strip()  # rapikan spasi di kiri/kanan

    if s.endswith("Z"):  # 'Z' artinya UTC (Zulu time)
        s = s[:-1] + "+00:00"  # ubah jadi format offset agar bisa diparse fromisoformat

    try:
        dt = datetime.fromisoformat(s)  # parse string ISO -> datetime
        if dt.tzinfo is None:  # kalau tidak punya timezone info
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))  # anggap UTC supaya konversi konsisten
        dt_wib = dt.astimezone(WIB)  # konversi timezone ke Asia/Jakarta
        return dt_wib.strftime("%Y-%m-%d %H:%M:%S WIB")  # format output WIB
    except Exception:
        return ""  # jika gagal parsing ISO (format tidak sesuai)


# =========================
# PLAYWRIGHT FETCH
# =========================
def fetch_rendered(page, url: str) -> str:
    page.goto(url, wait_until="domcontentloaded", timeout=120000)  # buka URL, tunggu event load (lebih aman dari networkidle di beberapa situs)

    # scroll sedikit untuk memicu konten lazy-load (kadang teks/komponen muncul setelah scroll)
    try:
        page.mouse.wheel(0, 1200)  # scroll ke bawah
        page.wait_for_timeout(500)  # tunggu 0.5 detik supaya DOM ter-update
    except Exception:
        pass  # kalau gagal scroll, lanjut saja

    return page.content()  # ambil HTML hasil render (setelah JS dieksekusi)


# =========================
# PARSER: LIST (TAG/NEWS)
# =========================
def parse_tag_news_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")  # parse HTML list menggunakan lxml
    rows = []  # simpan daftar item berita dari halaman list

    # Item list biasanya berupa link (<a>) yang teksnya memuat waktu "WIB"
    for a in soup.select("a[href]"):  # iterasi semua <a href="...">
        url = normalize_url(a.get("href", ""))  # normalisasi href jadi full URL
        if not is_article_url(url):  # jika bukan link artikel (menu/footer/iklan/tag)
            continue  # skip

        text = clean_text(a.get_text(" ", strip=True))  # ambil teks link
        if "WIB" not in text:  # filter cepat: item berita di list biasanya mengandung WIB
            continue  # skip link lain

        # contoh format teks list:
        # "detikJabar Rabu, 17 Des 2025 02:03 WIB Judul Berita..."
        m = re.search(
            r"^(?P<channel>\S+)\s+(?P<dow>\S+),\s+(?P<date>\d{1,2}\s+\S+\s+\d{4})\s+"
            r"(?P<time>\d{2}:\d{2})\s+WIB\s+(?P<title>.+)$",
            text
        )
        if not m:  # kalau pola teks berbeda (layout berubah)
            continue  # skip

        rows.append({
            "published_wib": f'{m.group("dow")}, {m.group("date")} {m.group("time")} WIB',  # tanggal versi list
            "title_list": clean_text(m.group("title")),  # judul versi list
            "url": url,  # link artikel
        })

    # dedup by url supaya satu artikel tidak masuk berkali-kali
    seen = set()  # penanda url yang sudah pernah masuk
    out = []  # hasil akhir setelah dedup
    for r in rows:
        if r["url"] in seen:  # kalau url sudah ada
            continue  # skip duplikasi
        seen.add(r["url"])  # tandai url sudah dipakai
        out.append(r)  # simpan item
    return out  # return list dict berisi artikel unik


# =========================
# PARSER: DETAIL
# =========================
def pick_main_container(soup: BeautifulSoup):
    selectors = [  # beberapa selector yang sering dipakai halaman detail detik (beda layout antar kanal)
        "article",
        "div.detail__body-text",
        "div.detail__body",
        "div#detikdetailtext",
        "div.itp_bodycontent",
    ]
    for sel in selectors:  # coba satu per satu
        el = soup.select_one(sel)  # ambil elemen pertama yang cocok
        if el and el.get_text(strip=True):  # pastikan elemen ada dan tidak kosong
            return el  # pakai sebagai container utama
    return soup  # fallback kalau tidak ada yang cocok: gunakan seluruh dokumen

def parse_detail_page(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")  # parse HTML detail

    # --- Judul ---
    title = extract_meta(soup, prop="og:title")  # prefer: meta og:title (lebih stabil)
    if not title:  # fallback kalau meta tidak ada
        h1 = soup.find("h1")  # ambil judul dari tag <h1>
        title = clean_text(h1.get_text(" ", strip=True)) if h1 else ""  # kalau h1 tidak ada -> kosong

    # --- Author ---
    author = extract_meta(soup, name="author")  # prefer: meta name=author
    if not author:  # fallback kalau meta author kosong
        # cari pola "Nama - detikX" dari teks halaman
        m = re.search(r"([A-Za-z .'-]+)\s*-\s*detik\w+", soup.get_text(" ", strip=True))
        if m:
            author = clean_text(m.group(1))  # ambil nama

    # --- Published time (ISO) ---
    published_iso = (
        extract_meta(soup, prop="article:published_time")  # biasanya ada di artikel modern
        or extract_meta(soup, name="publishdate")  # fallback
        or extract_meta(soup, name="date")  # fallback
    )

    # --- Content ---
    container = pick_main_container(soup)  # tentukan area utama konten artikel
    paras = []  # kumpulkan paragraf valid
    for p in container.find_all("p"):  # ambil semua <p>
        t = clean_text(p.get_text(" ", strip=True))  # teks paragraf
        if not t:  # kosong
            continue  # skip
        if t == "ADVERTISEMENT":  # label iklan
            continue  # skip
        if t.lower().startswith("baca juga:"):  # paragraf rekomendasi
            continue  # skip
        paras.append(t)  # simpan paragraf

    content = "\n\n".join(paras).strip()  # gabungkan paragraf jadi konten utama
    if not content:  # fallback kalau tidak ada <p> terbaca
        content = clean_text(container.get_text(" ", strip=True))  # ambil teks full dari container

    return {
        "url": url,  # url artikel
        "title_detail": title,  # judul hasil parse detail
        "author": author,  # author hasil parse detail
        "published_time_iso": published_iso,  # publish time ISO
        "content": content,  # isi artikel
    }


# =========================
# SCRAPER ORCHESTRATOR
# =========================
def scrape_tag_news_to_csv(
    page_start: int = 1,  # mulai dari page berapa
    page_end: int = 10,  # sampai page berapa (CATATAN: di bawah __main__ kamu memanggil page_end=5)
    sortby: str = "time",  # sorting list
    delay_min: float = 0.8,  # delay minimum antar request
    delay_max: float = 1.8,  # delay maksimum antar request
    out_csv: str = "mbg_news_detik.csv",  # nama output file
) -> pd.DataFrame:
    created_at = datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S")  # timestamp scraping dalam WIB

    with sync_playwright() as p:  # start Playwright
        browser = p.chromium.launch(headless=True)  # jalankan Chromium headless
        page = browser.new_page()  # buat satu tab/page
        page.set_extra_http_headers(HEADERS)  # set header (User-Agent, Accept-Language)

        # =========================
        # 1) Collect URLs from list pages
        # =========================
        list_rows = []  # menampung hasil artikel dari semua page list
        for pg in range(page_start, page_end + 1):  # loop halaman list
            list_url = f"{TAG_URL}?sortby={sortby}&page={pg}"  # bentuk URL paging
            html = fetch_rendered(page, list_url)  # render & ambil HTML
            rows = parse_tag_news_page(html)  # parse list -> daftar artikel

            if not rows:  # kalau kosong berarti paging habis / struktur berubah
                print(f"Stop: page {pg} kosong / tidak ada item.")
                break  # stop loop paging

            # Filter: skip judul yang mengandung kata 'video' (case-insensitive)
            filtered_rows = [
                r for r in rows
                if "video" not in (r.get("title_list") or "").lower()
            ]

            for r in filtered_rows:
                r["page"] = pg  # simpan nomor page asal item (opsional)
            list_rows.extend(filtered_rows)  # gabungkan hasil page ini ke list_rows

            time.sleep(random.uniform(delay_min, delay_max))  # jeda acak sebelum next page

        # Dedup URLs agar satu artikel hanya diambil sekali (tetap urutan kemunculan)
        urls = list(dict.fromkeys([r["url"] for r in list_rows]))
        print(f"Total unique URLs: {len(urls)}")  # log jumlah artikel unik
        print("Daftar link artikel:")
        for u in urls:
            print(u)

        # =========================
        # 2) Fetch details
        # =========================
        out = []  # menampung output final sesuai kolom yang diminta
        for i, u in enumerate(urls, start=1):  # loop tiap url artikel
            base = next((x for x in list_rows if x["url"] == u), {})  # cari data list untuk fallback

            try:
                html = fetch_rendered(page, u)  # render halaman detail
                d = parse_detail_page(html, u)  # parse detail
                sources = "detik"  # sumber situs

                # tanggal: prefer meta ISO -> konversi WIB, fallback ke tanggal versi list (published_wib)
                iso_wib = iso_to_wib(d.get("published_time_iso", ""))
                if iso_wib:
                    # Ambil format YYYY-MM-DD H:i:s dari hasil iso_to_wib (tanpa WIB)
                    tanggal = iso_wib.replace(" WIB", "")
                else:
                    # Fallback ke tanggal versi list (format: "Rabu, 17 Des 2025 02:03 WIB")
                    # Coba parsing ke format YYYY-MM-DD H:i:s jika memungkinkan
                    tgl_list = base.get("published_wib", "")
                    m = re.search(r"\b(\d{1,2}) (\w+) (\d{4}) (\d{2}):(\d{2}) WIB\b", tgl_list)
                    if m:
                        bulan_map = {
                            "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
                            "Mei": "05", "Jun": "06", "Jul": "07", "Agu": "08",
                            "Sep": "09", "Okt": "10", "Nov": "11", "Des": "12"
                        }
                        hari, bln, thn, jam, menit = m.groups()
                        bln_num = bulan_map.get(bln[:3], "01")
                        # Buat objek datetime dan konversi ke WIB
                        dt = datetime(
                            int(thn), int(bln_num), int(hari), int(jam), int(minut := menit), 0,
                            tzinfo=WIB
                        )
                        tanggal = dt.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        tanggal = tgl_list

                out.append({  # simpan row output final
                    "sumber": sources,  # sumber situs
                    "tanggal": tanggal, 
                    "judul": base.get("title_list", "") or "",  # fallback judul list
                    # Hilangkan "SCROLL TO CONTINUE WITH CONTENT" dari content jika ada
                    "content": (d.get("content") or "").replace("SCROLL TO CONTINUE WITH CONTENT", ""),  # isi artikel
                    "author": d.get("author") or "",  # author
                    "url": u,  # url
                    "created_at": created_at,  # waktu scraping (WIB)
                })

            except Exception as e:
                print(f"[ERROR] {u} -> {e}")  # log error
                out.append({  # tetap simpan minimal info agar url tidak hilang
                    "sumber": sources,  # sumber situs
                    "tanggal": base.get("published_wib", "") or "",  # fallback tanggal list
                    "judul": base.get("title_list", "") or "",  # fallback judul list
                    "content": "",  # kosong karena gagal parse detail
                    "author": "",  # kosong karena gagal parse detail
                    "url": u,  # url
                    "created_at": created_at,  # waktu scraping (WIB)
                })

            time.sleep(random.uniform(delay_min, delay_max))  # jeda acak antar artikel
            if i % 20 == 0:  # progress log tiap 20 artikel
                print(f"Progress detail: {i}/{len(urls)}")

        browser.close()  # tutup browser setelah selesai scraping

    # Bangun DataFrame sesuai kolom yang diminta
    df = pd.DataFrame(out, columns=["sumber","tanggal", "judul", "content", "author", "url", "created_at"])
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")  # export CSV (utf-8-sig cocok untuk Excel)
    print(f"Saved: {out_csv}")  # log output
    return df  # return agar bisa dipakai lanjut (analisis, dashboard, dll)


if __name__ == "__main__":
    # Atur range page sesuai kebutuhan kamu
    scrape_tag_news_to_csv(page_start=1, page_end=75, sortby="time")  # NOTE: ini override default page_end=2 di fungsi