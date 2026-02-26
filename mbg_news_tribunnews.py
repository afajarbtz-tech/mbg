import re
import time
import random
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, urljoin, parse_qs, urlencode

import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# =========================
# CONFIG
# =========================
WIB = ZoneInfo("Asia/Jakarta")
TAG_URL = "https://www.tribunnews.com/tag/mbg"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}

# =========================
# HELPERS
# =========================
def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def normalize_url(u: str) -> str:
    if not u: return ""
    if u.startswith("/"):
        u = urljoin("https://www.tribunnews.com", u)
    pu = urlparse(u)
    return pu._replace(fragment="").geturl()

def is_tribun_article_url(url: str) -> bool:
    if not url: return False
    pu = urlparse(url)
    host_ok = pu.netloc.endswith("tribunnews.com")
    id_ok = bool(re.search(r"/\d{5,}/", pu.path))
    bad = any(x in pu.path for x in ["/search", "/tag", "/topic", "/index", "/video"])
    return host_ok and id_ok and (not bad)

def add_page_all(url: str) -> str:
    if not url: return ""
    pu = urlparse(url)
    qs = parse_qs(pu.query)
    if qs.get("page", [""])[0] == "all": return url
    qs["page"] = ["all"]
    new_query = urlencode({k: v[0] for k, v in qs.items()})
    return pu._replace(query=new_query).geturl()

def parse_indo_date(date_text: str) -> str:
    if not date_text: return ""
    months = {
        "Januari": "01", "Februari": "02", "Maret": "03", "April": "04",
        "Mei": "05", "Juni": "06", "Juli": "07", "Agustus": "08",
        "September": "09", "Oktober": "10", "November": "11", "Desember": "12"
    }
    try:
        s = re.sub(r"^[a-zA-Z]+,\s*", "", date_text).replace("WIB", "").strip()
        parts = s.split()
        if len(parts) >= 4:
            day, month, year, time_val = parts[0].zfill(2), months.get(parts[1], "01"), parts[2], parts[3]
            if len(time_val.split(':')) == 2: time_val += ":00"
            return f"{year}-{month}-{day} {time_val}"
    except: pass
    return ""

def iso_to_wib(iso_str: str) -> str:
    if not iso_str: return ""
    try:
        s = iso_str.strip()
        if s.endswith("Z"): s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(WIB).strftime("%Y-%m-%d %H:%M:%S")
    except: return ""

def extract_meta(soup: BeautifulSoup, name=None, prop=None) -> str:
    attr = {"property": prop} if prop else {"name": name}
    m = soup.find("meta", attrs=attr)
    return clean_text(m["content"]) if m and m.get("content") else ""

def pick_main_container(soup: BeautifulSoup):
    selectors = ["article", "div.txt-article", "div#articlebody", "div.side-article"]
    for sel in selectors:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True): return el
    return soup

# =========================
# PARSER: LIST (TAG PAGE)
# =========================
def parse_tag_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    
    # --- BAGIAN INI UNTUK MENGHAPUS SIDEBAR BERITA TERKINI ---
    # Kita hapus div#boxright_fix agar link di dalamnya tidak terdeteksi
    sidebar = soup.select_one("#boxright_fix")
    if sidebar:
        sidebar.decompose() 

    rows = []
    # Cari link hanya di area sisa (konten utama)
    candidates = soup.select("h3 a[href], h2 a[href], a[href]")

    for a in candidates:
        href = normalize_url(a.get("href", ""))
        if not is_tribun_article_url(href):
            continue
        title = clean_text(a.get_text(" ", strip=True))
        if not title or len(title) < 10 or "video" in title.lower():
            continue

        rows.append({"url": href})

    seen = set()
    return [r for r in rows if not (r["url"] in seen or seen.add(r["url"]))]

# =========================
# PARSER: DETAIL
# =========================
def parse_detail_page(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    # Title
    title = extract_meta(soup, prop="og:title") or (soup.h1.get_text(strip=True) if soup.h1 else "")

    # Author
    author = ""
    penulis_el = soup.select_one("#penulis")
    if penulis_el:
        author = clean_text(penulis_el.get_text().replace("Penulis:", ""))
    if not author:
        author = extract_meta(soup, name="author")

    # Tanggal
    published_final = ""
    time_el = soup.select_one("time span")
    if time_el:
        published_final = parse_indo_date(time_el.get_text(strip=True))
    if not published_final:
        published_final = iso_to_wib(extract_meta(soup, prop="article:published_time"))

    # Content
    container = pick_main_container(soup)
    for bad in container.select("script, style, noscript, .ads, .baca-juga"):
        bad.decompose()

    paras = [clean_text(p.get_text(" ", strip=True)) for p in container.find_all("p")]
    content = "\n\n".join([p for p in paras if p and not any(x in p.upper() for x in ["ADVERTISEMENT", "IKLAN", "BACA JUGA"])])

    return {
        "url": url,
        "title_detail": title,
        "author": author,
        "published_final": published_final,
        "content": content.strip(),
    }

# =========================
# ORCHESTRATOR
# =========================
def scrape_tribun_tag_to_csv(page_start=1, page_end=5, out_csv="mbg_news_tribunnews.csv"):
    created_at = datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_extra_http_headers(HEADERS)

        # 1) Collect Link
        all_urls = []
        for pg in range(page_start, page_end + 1):
            list_url = f"{TAG_URL}?page={pg}"
            print(f"[*] Mencari berita di: {list_url}")
            try:
                page.goto(list_url, wait_until="domcontentloaded")
                html = page.content()
                links = parse_tag_page(html)
                if not links: break
                all_urls.extend([add_page_all(l["url"]) for l in links])
            except: break
            time.sleep(random.uniform(1, 2))

        all_urls = list(dict.fromkeys(all_urls))
        print(f"[*] Ditemukan {len(all_urls)} berita unik.")

        # 2) Fetch Detail
        out = []
        for i, u in enumerate(all_urls, 1):
            try:
                page.goto(u, wait_until="domcontentloaded")
                d = parse_detail_page(page.content(), u)
                # Hapus kata "Tribunnews.com" dari judul dan konten
                judul_bersih = d["title_detail"].replace("Tribunnews.com", "").strip()
                content_bersih = d["content"].replace("Tribunnews.com", "").strip()
                out.append({
                    "sumber": "tribunnews",
                    "tanggal": d["published_final"],
                    "judul": judul_bersih,
                    "content": content_bersih,
                    "author": d["author"],
                    "url": u,
                    "created_at": created_at,
                })
                print(f"[{i}/{len(all_urls)}] Sukses: {d['title_detail'][:40]}...")
            except Exception as e:
                print(f"[!] Gagal {u}: {e}")
            time.sleep(random.uniform(0.8, 1.5))

        browser.close()

    df = pd.DataFrame(out)
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n[DONE] Selesai! Data disimpan di {out_csv}")

if __name__ == "__main__":
    scrape_tribun_tag_to_csv(page_start=16, page_end=35)