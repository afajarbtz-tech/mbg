import re  # regex untuk filter URL, parsing teks, dan deteksi blok "BACA JUGA"
import time  # delay antar request
import random  # delay acak agar tidak berpola bot
import json  # parse JSON-LD dari halaman detail
from datetime import datetime  # created_at
from zoneinfo import ZoneInfo  # timezone WIB
from urllib.parse import urljoin, urlparse  # normalisasi URL (relative->absolute)

import pandas as pd  # simpan hasil scraping sebagai DataFrame + export CSV
from bs4 import BeautifulSoup  # parsing HTML (list & detail)
from playwright.sync_api import sync_playwright  # render halaman Tempo (JS + popup)


# =========================
# CONFIG
# =========================
BASE = "https://www.tempo.co"  # base domain Tempo
SEARCH_URL = "https://www.tempo.co/search"  # endpoint search
WIB = ZoneInfo("Asia/Jakarta")  # timezone target (WIB)

HEADERS = {  # header agar request terlihat seperti browser
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}


# =========================
# HELPERS (TEXT/URL)
# =========================
def clean_text(s: str) -> str:
    """Rapikan whitespace jadi satu spasi."""
    return re.sub(r"\s+", " ", s or "").strip()

def normalize_url(u: str) -> str:
    """Ubah URL relatif jadi absolut & buang fragment."""
    if not u:
        return ""
    if u.startswith("/"):
        u = urljoin(BASE, u)
    parsed = urlparse(u)
    return parsed._replace(fragment="").geturl()

def is_tempo_article(url: str) -> bool:
    """
    Filter URL artikel Tempo (umumnya berakhiran '-<angka>').
    Contoh: https://www.tempo.co/politik/...-2099723
    """
    if not url:
        return False
    url = normalize_url(url)
    return ("tempo.co/" in url) and bool(re.search(r"-\d{5,}$", url))


# =========================
# TIME PARSER -> WIB
# =========================
def to_wib_str(dt: datetime) -> str:
    """Format datetime timezone-aware ke 'YYYY-mm-dd HH:MM:SS' WIB."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=WIB)
    return dt.astimezone(WIB).strftime("%Y-%m-%d %H:%M:%S")

def parse_iso_to_wib(s: str) -> str:
    """Parse ISO datetime (Z / +00:00 / +07:00) -> 'YYYY-mm-dd HH:MM:SS' WIB."""
    if not s:
        return ""
    ss = s.strip()
    if ss.endswith("Z"):
        ss = ss[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(ss)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return to_wib_str(dt)
    except Exception:
        return ""


# =========================
# JSON-LD EXTRACTOR (paling stabil untuk Tempo)
# =========================
def extract_newsarticle_ld(soup: BeautifulSoup) -> dict:
    """
    Ambil metadata dari JSON-LD (NewsArticle):
    - author name
    - datePublished
    - headline
    """
    scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
    for sc in scripts:
        raw = (sc.string or sc.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        candidates = data if isinstance(data, list) else [data]

        expanded = []
        for c in candidates:
            if isinstance(c, dict) and "@graph" in c and isinstance(c["@graph"], list):
                expanded.extend(c["@graph"])
            else:
                expanded.append(c)

        for item in expanded:
            if not isinstance(item, dict):
                continue

            t = item.get("@type")
            types = t if isinstance(t, list) else [t]
            if "NewsArticle" not in [x for x in types if isinstance(x, str)]:
                continue

            headline = item.get("headline") or ""
            date_published = item.get("datePublished") or item.get("dateCreated") or ""
            author_name = ""

            author = item.get("author")
            if isinstance(author, dict):
                author_name = author.get("name") or ""
            elif isinstance(author, list):
                for a in author:
                    if isinstance(a, dict) and a.get("name"):
                        author_name = a.get("name")
                        break
                    if isinstance(a, str) and a.strip():
                        author_name = a.strip()
                        break
            elif isinstance(author, str):
                author_name = author.strip()

            return {
                "headline": clean_text(headline),
                "datePublished": clean_text(date_published),
                "author": clean_text(author_name),
            }

    return {}


# =========================
# POPUP DISMISSER (TEMPO)
# =========================
def dismiss_popups(page):
    """Tutup popup/overlay Tempo: ESC, klik close, fallback remove overlay."""
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
    except Exception:
        pass

    candidates = [
        "button[aria-label='Close']",
        "button[aria-label='Tutup']",
        "button:has-text('Tutup')",
        "button:has-text('×')",
        "button:has-text('✕')",
        "text=Tutup",
        "button:has-text('Mungkin nanti')",
        "text=Mungkin nanti",
        ".close",
        ".btn-close",
        ".modal-close",
        ".popup__close",
        "[class*='close']",
        "[data-testid='close']",
        "div[role='button']:has-text('×')",
        "span:has-text('×')",
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                loc.first.click(timeout=800, force=True)
                page.wait_for_timeout(150)
        except Exception:
            pass

    try:
        page.evaluate("""
        () => {
          const vw = window.innerWidth, vh = window.innerHeight;
          document.documentElement.style.overflow = 'auto';
          document.body.style.overflow = 'auto';

          const els = Array.from(document.querySelectorAll('body *'));
          for (const el of els) {
            const s = window.getComputedStyle(el);
            const z = parseInt(s.zIndex || '0', 10);

            if ((s.position === 'fixed' || s.position === 'sticky') && z >= 999) {
              const r = el.getBoundingClientRect();
              const coverW = r.width >= vw * 0.5;
              const coverH = r.height >= vh * 0.5;
              if (coverW && coverH) el.remove();
            }
          }
        }
        """)
        page.wait_for_timeout(150)
    except Exception:
        pass


# =========================
# PLAYWRIGHT FETCH
# =========================
def fetch_rendered(page, url: str) -> str:
    """Buka URL + tutup popup + scroll kecil + tutup popup lagi."""
    page.goto(url, wait_until="domcontentloaded", timeout=120000)
    dismiss_popups(page)

    try:
        page.wait_for_timeout(600)
        page.mouse.wheel(0, 1200)
        page.wait_for_timeout(600)
        dismiss_popups(page)
    except Exception:
        pass

    return page.content()


# =========================
# SEARCH LIST PARSER
# =========================
def build_search_url(q: str, category: str, access: str, page_no: int) -> str:
    """Bentuk URL search sesuai format Tempo."""
    return f"{SEARCH_URL}?q={q}&category={category}&access={access}&page={page_no}"

def parse_search_page(html: str) -> list[dict]:
    """Ambil daftar URL artikel dari halaman search."""
    soup = BeautifulSoup(html, "lxml")
    rows = []

    for a in soup.select("a[href]"):
        href = normalize_url(a.get("href", ""))
        if not is_tempo_article(href):
            continue

        title = clean_text(a.get_text(" ", strip=True))
        if len(title) < 5:
            title = ""

        rows.append({"title_list": title, "url": href})

    seen = set()
    out = []
    for r in rows:
        if r["url"] in seen:
            continue
        seen.add(r["url"])
        out.append(r)

    return out


# =========================
# DETAIL PARSER (EXCLUDE "BACA JUGA" + CLASS BOX)
# =========================
def pick_article_container(soup: BeautifulSoup):
    """Pilih container artikel (fallback berurutan)."""
    selectors = [
        "article",
        "div.detail",
        "div#main-content",
        "div.content-detail",
        "div[data-testid='content']",
    ]
    for sel in selectors:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            return el
    return soup

def remove_unwanted_blocks(container):
    """
    Buang blok yang tidak boleh masuk ke content:
    1) blok "BACA JUGA" (dan related)
    2) box Tempo class: p-4 my-4 bg-neutral-400 border border-neutral-600
    """
    if not container:
        return

    # (1) Hapus wrapper yang mengandung label BACA JUGA
    targets = container.find_all(string=re.compile(r"\bBACA\s+JUGA\b", re.IGNORECASE))
    for t in targets:
        node = t.parent
        for _ in range(7):
            if not node:
                break
            if node.name in ("section", "aside", "div"):
                block_text = node.get_text(" ", strip=True)
                if re.search(r"\bBACA\s+JUGA\b", block_text, flags=re.IGNORECASE):
                    node.decompose()
                    break
            node = node.parent

    # (1b) Hapus related berdasarkan class umum (opsional)
    for el in container.select(
        "[class*='related'], [class*='recommend'], [class*='rekomend'], "
        "[class*='baca-juga'], [class*='baca_juga'], [data-testid*='related']"
    ):
        try:
            el.decompose()
        except Exception:
            pass

    # (2) Hapus box spesifik Tempo (class persis seperti kamu sebut)
    for el in container.select(".p-4.my-4.bg-neutral-400.border.border-neutral-600"):
        try:
            el.decompose()
        except Exception:
            pass

def parse_tempo_detail(html: str, url: str) -> dict:
    """Parse detail Tempo: judul, author, tanggal, content (tanpa blok yang dikecualikan)."""
    soup = BeautifulSoup(html, "lxml")

    ld = extract_newsarticle_ld(soup)

    # judul
    title = ld.get("headline") or ""
    if not title:
        og = soup.find("meta", attrs={"property": "og:title"})
        if og and og.get("content"):
            title = clean_text(og["content"])
    if not title:
        h1 = soup.find("h1")
        title = clean_text(h1.get_text(" ", strip=True)) if h1 else ""

    if title.endswith(" | tempo.co"):
        title = title[:-len(" | tempo.co")].rstrip()

    # author
    author = ld.get("author") or ""

    # tanggal
    published_wib = parse_iso_to_wib(ld.get("datePublished") or "")

    # content
    container = pick_article_container(soup)
    remove_unwanted_blocks(container)  # >>> buang box yang kamu minta exclude

    paras = []
    for p in container.find_all("p"):
        t = clean_text(p.get_text(" ", strip=True))
        if not t:
            continue
        # filter tambahan
        if re.search(r"\bBACA\s+JUGA\b", t, flags=re.IGNORECASE):
            continue
        if t.lower().startswith("baca juga"):
            continue
        paras.append(t)

    content = "\n\n".join(paras).strip()
    if not content:
        content = clean_text(container.get_text(" ", strip=True))

    return {
        "url": url,
        "title_detail": title,
        "author": author,
        "published_wib": published_wib,
        "content": content,
    }


# =========================
# SCRAPER ORCHESTRATOR
# =========================
def scrape_tempo_search_to_csv(
    q: str = "mbg",
    category: str = "newsAccess",
    access: str = "FREE",
    page_start: int = 1,
    page_end: int = 2,
    delay_min: float = 0.8,
    delay_max: float = 1.8,
    out_csv: str = "mbg_news_tempo.csv",
) -> pd.DataFrame:
    created_at = datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        context = browser.new_context(
            extra_http_headers=HEADERS,
            locale="id-ID",
            timezone_id="Asia/Jakarta",
        )
        page = context.new_page()

        # 1) ambil list URL
        list_rows = []
        for pg in range(page_start, page_end + 1):
            list_url = build_search_url(q=q, category=category, access=access, page_no=pg)
            html = fetch_rendered(page, list_url)
            rows = parse_search_page(html)

            if not rows:
                print(f"Stop: search page {pg} kosong / tidak ada item.")
                break

            filtered_rows = [r for r in rows if "video" not in (r.get("title_list") or "").lower()]

            for r in filtered_rows:
                r["page"] = pg
            list_rows.extend(filtered_rows)

            time.sleep(random.uniform(delay_min, delay_max))

        urls = list(dict.fromkeys([r["url"] for r in list_rows]))
        print(f"Total unique URLs: {len(urls)}")

        # 2) ambil detail
        out = []
        for i, u in enumerate(urls, start=1):
            base = next((x for x in list_rows if x["url"] == u), {})
            judul_default = base.get("title_list") or ""

            try:
                html = fetch_rendered(page, u)
                d = parse_tempo_detail(html, u)

                # Hapus kalimat promosi/iklan dari konten
                content = d.get("content") or ""
                for unwanted in [
                    "Scroll ke bawah untuk melanjutkan membaca",
                    "Baca berita dengan sedikit iklan, klik di sini"
                ]:
                    content = content.replace(unwanted, "")
                # Hapus jika ada kalimat awal "Pilihan Editor: ..."
                content = re.sub(r"^\s*Pilihan Editor:\s*.*?(?:\n|$)", "", content, flags=re.IGNORECASE)

                out.append({
                    "sumber": "tempo",
                    "tanggal": d.get("published_wib") or "",
                    "judul": d.get("title_detail") or judul_default,
                    "content": content.strip(),
                    "author": d.get("author") or "",
                    "url": u,
                    "created_at": created_at,
                })

            except Exception as e:
                print(f"[ERROR] {u} -> {e}")
                out.append({
                    "sumber": "tempo",
                    "tanggal": "",
                    "judul": judul_default,
                    "content": "",
                    "author": "",
                    "url": u,
                    "created_at": created_at,
                })

            time.sleep(random.uniform(delay_min, delay_max))
            if i % 20 == 0:
                print(f"Progress detail: {i}/{len(urls)}")

        context.close()
        browser.close()

    df = pd.DataFrame(out, columns=["sumber", "tanggal", "judul", "content", "author", "url", "created_at"])
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"Saved: {out_csv}")
    return df


if __name__ == "__main__":
    # contoh: https://www.tempo.co/search?q=mbg&category=newsAccess&access=FREE&page=2
    scrape_tempo_search_to_csv(q="mbg", category="newsAccess", access="FREE", page_start=4, page_end=10)
