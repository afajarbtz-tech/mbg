import re
import json
import time
import random
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode

import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


# =========================
# CONFIG
# =========================
BASE = "https://www.kompas.com"
SEARCH_BASE = "https://search.kompas.com/search"

WIB = ZoneInfo("Asia/Jakarta")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}


# =========================
# HELPERS
# =========================
def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def normalize_url(u: str) -> str:
    if not u:
        return ""
    if u.startswith("/"):
        u = urljoin(BASE, u)

    parsed = urlparse(u)
    parsed = parsed._replace(fragment="")

    # bersihkan query tracking (utm, fbclid, dll) supaya dedup stabil
    if parsed.query:
        kept = []
        for k, v in parse_qsl(parsed.query, keep_blank_values=True):
            lk = k.lower()
            if lk.startswith("utm_") or lk in {"fbclid", "gclid", "yclid"}:
                continue
            kept.append((k, v))
        parsed = parsed._replace(query=urlencode(kept, doseq=True))

    return parsed.geturl()

def is_kompas_article_url(url: str) -> bool:
    # contoh: https://nasional.kompas.com/read/2025/12/17/09463351/....
    return bool(url) and bool(re.search(r"kompas\.com/read/\d{4}/\d{2}/\d{2}/\d+", url))

def extract_meta(soup: BeautifulSoup, name=None, prop=None) -> str:
    if prop:
        m = soup.find("meta", attrs={"property": prop})
        if m and m.get("content"):
            return clean_text(m["content"])
    if name:
        m = soup.find("meta", attrs={"name": name})
        if m and m.get("content"):
            return clean_text(m["content"])
    return ""

def iso_to_wib(iso_str: str) -> str:
    """
    ISO -> 'YYYY-mm-dd HH:MM:SS WIB'
    """
    if not iso_str:
        return ""
    s = iso_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(WIB).strftime("%Y-%m-%d %H:%M:%S WIB")
    except Exception:
        return ""

def parse_kompas_time_text_to_wib(text: str) -> str:
    """
    Fallback parse waktu yang tampil di halaman.
    Output: 'YYYY-mm-dd HH:MM:SS'
    """
    if not text:
        return ""
    s = clean_text(text)

    # format: 17/12/2025, 09:46 WIB
    m = re.search(r"(\d{2})/(\d{2})/(\d{4}),\s*(\d{2}):(\d{2})\s*WIB", s)
    if m:
        dd, mm, yyyy, HH, MM = m.groups()
        dt = datetime(int(yyyy), int(mm), int(dd), int(HH), int(MM), 0, tzinfo=WIB)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    # format: 17 Desember 2025, 09:46 WIB / 17 Des 2025 09:46 WIB
    bulan_map = {
        "januari": 1, "jan": 1,
        "februari": 2, "feb": 2,
        "maret": 3, "mar": 3,
        "april": 4, "apr": 4,
        "mei": 5,
        "juni": 6, "jun": 6,
        "juli": 7, "jul": 7,
        "agustus": 8, "agu": 8,
        "september": 9, "sep": 9,
        "oktober": 10, "okt": 10,
        "november": 11, "nov": 11,
        "desember": 12, "des": 12,
    }
    m = re.search(
        r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4}).*?(\d{2}):(\d{2})\s*WIB",
        s,
        flags=re.I
    )
    if m:
        dd, mon, yyyy, HH, MM = m.groups()
        mon_num = bulan_map.get(mon.lower(), 0)
        if mon_num:
            dt = datetime(int(yyyy), mon_num, int(dd), int(HH), int(MM), 0, tzinfo=WIB)
            return dt.strftime("%Y-%m-%d %H:%M:%S")

    return ""


# =========================
# AUTHOR EXTRACTOR (ROBUST)
# JSON-LD -> CREDIT BLOCK -> META
# =========================
def extract_author_kompas(soup: BeautifulSoup) -> str:
    def norm(name: str) -> str:
        name = clean_text(name)
        bad = {"tim redaksi", "editor", "kompas.com", "kompas", "redaksi"}
        if not name or name.lower() in bad:
            return ""
        return name

    # (A) PRIORITAS: JSON-LD (paling stabil)
    authors = []
    for sc in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (sc.string or sc.get_text(strip=True) or "").strip()
        if not raw:
            continue

        try:
            data = json.loads(raw)
        except Exception:
            continue

        candidates = data if isinstance(data, list) else [data]

        def pick_author(obj):
            if not isinstance(obj, dict):
                return []

            # kadang data ada di @graph
            if "@graph" in obj and isinstance(obj["@graph"], list):
                res = []
                for x in obj["@graph"]:
                    res.extend(pick_author(x))
                return res

            a = obj.get("author") or obj.get("creator") or obj.get("contributor")
            res = []
            if isinstance(a, str):
                n = norm(a)
                if n:
                    res.append(n)
            elif isinstance(a, dict):
                n = norm(a.get("name", ""))
                if n:
                    res.append(n)
            elif isinstance(a, list):
                for it in a:
                    if isinstance(it, str):
                        n = norm(it)
                        if n:
                            res.append(n)
                    elif isinstance(it, dict):
                        n = norm(it.get("name", ""))
                        if n:
                            res.append(n)
            return res

        for obj in candidates:
            authors.extend(pick_author(obj))

    authors = list(dict.fromkeys([a for a in authors if a]))
    if authors:
        return ", ".join(authors)

    # (B) FALLBACK: BLOK CREDIT (header) - kalau JSON-LD kosong
    credit = (
        soup.select_one(".read__credit")
        or soup.select_one("div[class*='read__credit']")
        or soup.select_one("[class*='credit']")
    )
    if credit:
        names = []

        # 1) prioritas elemen yang kemungkinan besar berisi nama
        name_selectors = [
            ".read__credit__name",
            "[class*='credit__name']",
            "a[href*='/author/']",
            "a[rel='author']",
            "[itemprop='author']",
        ]
        for sel in name_selectors:
            for el in credit.select(sel):
                t = norm(el.get_text(" ", strip=True))
                if t:
                    names.append(t)

        # 2) kalau nama ada di <a> biasa (seperti screenshot)
        if not names:
            for a in credit.select("a"):
                t = norm(a.get_text(" ", strip=True))
                if t:
                    names.append(t)

        names = list(dict.fromkeys([n for n in names if n]))
        if names:
            return ", ".join(names)

        # 3) fallback: teks sebelum "Tim Redaksi/Editor"
        txt = clean_text(credit.get_text(" ", strip=True))
        txt = re.split(r"\bTim Redaksi\b|\bEditor\b", txt, flags=re.I)[0]
        txt = norm(txt)
        if txt:
            return txt

    # (C) LAST: META
    author = extract_meta(soup, name="author") or extract_meta(soup, prop="article:author")
    return norm(author)


# =========================
# PLAYWRIGHT FETCH
# =========================
def fetch_rendered(page, url: str) -> str:
    page.goto(url, wait_until="domcontentloaded", timeout=120000)

    # tunggu sedikit untuk elemen hasil search/article header ke-render
    try:
        page.wait_for_timeout(600)
        page.mouse.wheel(0, 1600)
        page.wait_for_timeout(500)
    except Exception:
        pass

    return page.content()


# =========================
# PARSER: SEARCH (LIST)
# =========================
def parse_kompas_search_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows = []

    # scan semua link dan ambil yang match /read/YYYY/MM/DD/
    for a in soup.select("a[href]"):
        url = normalize_url(a.get("href", ""))
        if not is_kompas_article_url(url):
            continue

        title = clean_text(a.get_text(" ", strip=True))
        if len(title) < 8:
            continue
        if title.lower().startswith("baca juga"):
            continue

        rows.append({"title_list": title, "url": url})

    # dedup
    seen = set()
    out = []
    for r in rows:
        if r["url"] in seen:
            continue
        seen.add(r["url"])
        out.append(r)
    return out


# =========================
# PARSER: DETAIL
# =========================
def pick_main_container(soup: BeautifulSoup):
    selectors = [
        "div.read__content",
        "div.read__content__text",
        "article",
        "div.read__article",
    ]
    for sel in selectors:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            return el
    return soup

def parse_detail_page(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    # --- Judul ---
    title = extract_meta(soup, prop="og:title")
    if not title:
        h1 = soup.find("h1")
        title = clean_text(h1.get_text(" ", strip=True)) if h1 else ""

    # --- Author ---
    author = extract_author_kompas(soup)

    # --- Published time ---
    published_iso = (
        extract_meta(soup, prop="article:published_time")
        or extract_meta(soup, name="content_PublishedDate")
        or extract_meta(soup, name="publishdate")
        or extract_meta(soup, name="date")
    )
    published_text = ""
    time_el = soup.select_one(".read__time, div.read__time")
    if time_el:
        published_text = clean_text(time_el.get_text(" ", strip=True))

    # --- Content ---
    container = pick_main_container(soup)

    # buang blok non-isi (kalau ada)
    for sel in [
        "div.read__related", "div.related", "div.baca-juga", "div#bacajuga",
        "div[class*='related']", "div[class*='baca']",
        "script", "style"
    ]:
        for x in container.select(sel):
            x.decompose()

    # HILANGKAN LINK: unwrap <a> agar teks tetap ada tapi URL hilang
    for a in container.find_all("a"):
        a.unwrap()

    paras = []
    for p in container.find_all("p"):
        t = clean_text(p.get_text(" ", strip=True))
        if not t:
            continue

        low = t.lower()
        if t == "ADVERTISEMENT":
            continue
        if low.startswith("baca juga"):
            continue
        if low.startswith("lihat juga"):
            continue

        t = t.replace("SCROLL TO CONTINUE WITH CONTENT", "").strip()
        if not t:
            continue

        paras.append(t)

    content = "\n\n".join(paras).strip()
    if not content:
        content = clean_text(container.get_text(" ", strip=True))

    return {
        "url": url,
        "title_detail": title,
        "author": author,
        "published_time_iso": published_iso,
        "published_time_text": published_text,
        "content": content,
    }


# =========================
# SCRAPER ORCHESTRATOR
# =========================
def scrape_kompas_search_to_csv(
    query: str = "mbg",
    page_start: int = 1,
    page_end: int = 10,
    sort: str = "latest",
    site_id: str = "all",
    last_date: str = "all",
    delay_min: float = 0.8,
    delay_max: float = 1.8,
    out_csv: str = "kompas_mbg_news.csv",
) -> pd.DataFrame:
    created_at = datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S")
    sumber = "kompas"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_extra_http_headers(HEADERS)

        # 1) Collect URLs
        list_rows = []
        for pg in range(page_start, page_end + 1):
            list_url = (
                f"{SEARCH_BASE}?q={query}&sort={sort}"
                f"&site_id={site_id}&last_date={last_date}&page={pg}"
            )

            html = fetch_rendered(page, list_url)
            rows = parse_kompas_search_page(html)

            if not rows:
                print(f"Stop: page {pg} kosong / struktur berubah.")
                break

            for r in rows:
                r["page"] = pg
            list_rows.extend(rows)

            time.sleep(random.uniform(delay_min, delay_max))

        urls = list(dict.fromkeys([r["url"] for r in list_rows]))
        print(f"Total unique URLs: {len(urls)}")

        # 2) Fetch details
        out = []
        for i, u in enumerate(urls, start=1):
            base = next((x for x in list_rows if x["url"] == u), {})

            try:
                html = fetch_rendered(page, u)
                d = parse_detail_page(html, u)

                # tanggal: prefer ISO -> WIB, fallback ke waktu yang tampil
                iso_wib = iso_to_wib(d.get("published_time_iso", ""))
                if iso_wib:
                    tanggal = iso_wib.replace(" WIB", "")
                else:
                    tanggal = parse_kompas_time_text_to_wib(d.get("published_time_text", ""))

                out.append({
                    "sumber": sumber,
                    "tanggal": tanggal,
                    "judul": d.get("title_detail") or base.get("title_list") or "",
                    "content": (
                        (d.get("content") or "")
                        .replace("SCROLL TO CONTINUE WITH CONTENT", "")
                        .replace("KOMPAS.com", "")
                    ),
                    "author": d.get("author") or "",
                    "url": u,
                    "created_at": created_at,
                })

                # debug ringan kalau author kosong (biar kamu cepat tahu URL mana yang gagal)
                if not (d.get("author") or "").strip():
                    print("[WARN] AUTHOR EMPTY:", u)

            except Exception as e:
                print(f"[ERROR] {u} -> {e}")
                out.append({
                    "sumber": sumber,
                    "tanggal": "",
                    "judul": base.get("title_list", "") or "",
                    "content": "",
                    "author": "",
                    "url": u,
                    "created_at": created_at,
                })

            time.sleep(random.uniform(delay_min, delay_max))
            if i % 20 == 0:
                print(f"Progress detail: {i}/{len(urls)}")

        browser.close()

    df = pd.DataFrame(out, columns=["sumber", "tanggal", "judul", "content", "author", "url", "created_at"])
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"Saved: {out_csv}")
    return df


if __name__ == "__main__":
    scrape_kompas_search_to_csv(
        query="mbg",
        page_start=1,
        page_end=5,
        sort="latest",
        site_id="all",
        last_date="all",
        out_csv="mbg_news_kompas.csv",
    )
