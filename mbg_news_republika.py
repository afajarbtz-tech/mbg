import re
import time
import random
import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, urljoin, quote_plus, parse_qs, urlencode
from typing import Optional, Tuple, List, Dict, Any

import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# =========================
# KONSTANTA DAN KONFIGURASI
# =========================
WIB = ZoneInfo("Asia/Jakarta")
BASE_URL = "https://republika.co.id"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# =========================
# FUNGSI UTILITAS
# =========================
def clean_text(text: str) -> str:
    """Membersihkan teks dari karakter tidak diinginkan"""
    if not text:
        return ""
    # Normalisasi whitespace
    text = re.sub(r'\s+', ' ', text)
    # Hapus karakter non-ASCII yang tidak diinginkan, pertahankan tanda baca umum
    text = re.sub(r'[^\x00-\x7F]+', ' ', text)  # Hapus karakter non-ASCII
    # Hapus karakter kontrol
    text = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', text)
    # Hapus karakter khusus yang tidak diinginkan
    text = re.sub(r'[^\w\s.,!?;:()\-—–"\']', '', text)
    # Bersihkan spasi ganda
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def normalize_url(u: str, base_url: str = BASE_URL) -> str:
    """Normalisasi URL"""
    if not u:
        return ""
    if u.startswith("/"):
        u = urljoin(base_url, u)
    pu = urlparse(u)
    return pu._replace(fragment="", query="").geturl()

def generate_article_id(url: str) -> str:
    """Generate unique article_id from URL"""
    return hashlib.md5(url.encode()).hexdigest()[:16]

def generate_search_id(keyword: str, startdate: str, enddate: str) -> str:
    """Generate unique search_id based on inputs"""
    input_str = f"{keyword}_{startdate}_{enddate}"
    return hashlib.md5(input_str.encode()).hexdigest()[:16]

def parse_indo_date(date_text: str) -> Optional[datetime]:
    """Parse tanggal Indonesia ke datetime object"""
    if not date_text:
        return None
    
    # Mapping bulan Indonesia ke angka
    months = {
        "Januari": 1, "Februari": 2, "Maret": 3, "April": 4,
        "Mei": 5, "Juni": 6, "Juli": 7, "Agustus": 8,
        "September": 9, "Oktober": 10, "November": 11, "Desember": 12
    }
    
    try:
        # Pattern 1: "15 Maret 2024, 14:30"
        pattern1 = r"(\d{1,2})\s+(\w+)\s+(\d{4}),?\s+(\d{1,2}):(\d{2})"
        match1 = re.search(pattern1, date_text)
        
        if match1:
            day, month_str, year, hour, minute = match1.groups()
            month = months.get(month_str)
            if month:
                return datetime(
                    int(year), month, int(day),
                    int(hour), int(minute)
                ).replace(tzinfo=WIB)
        
        # Pattern 2: "2024-03-15T14:30:00Z" (ISO format)
        pattern2 = r"(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2}):(\d{2})"
        match2 = re.search(pattern2, date_text)
        
        if match2:
            year, month, day, hour, minute, second = match2.groups()
            return datetime(
                int(year), int(month), int(day),
                int(hour), int(minute), int(second)
            ).replace(tzinfo=WIB)
    
    except Exception:
        pass
    
    return None

def extract_meta(soup: BeautifulSoup, name: str = None, prop: str = None) -> str:
    """Ekstrak metadata dari tag meta"""
    attr = {"property": prop} if prop else {"name": name}
    m = soup.find("meta", attrs=attr)
    return clean_text(m["content"]) if m and m.get("content") else ""

# =========================
# FUNGSI SCRAPING ARTIKEL DETAIL
# =========================
def extract_republika_article(url: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Fungsi utama untuk scraping artikel Republika.co.id menggunakan Playwright
    """
    try:
        print(f"🔍 Mengakses URL: {url}")
        
        with sync_playwright() as p:
            # Launch browser dengan konfigurasi yang lebih optimal
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                ]
            )
            
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                viewport={'width': 1920, 'height': 1080},
                extra_http_headers=HEADERS
            )
            
            page = context.new_page()
            page.set_default_timeout(30000)  # 30 detik timeout
            
            try:
                # Navigasi ke URL
                page.goto(url, wait_until="domcontentloaded")
                time.sleep(random.uniform(2, 3))  # Tunggu loading
                
                # Scroll sedikit untuk trigger lazy content
                page.evaluate("window.scrollBy(0, 500)")
                time.sleep(random.uniform(1, 1.5))
                
                # Ambil HTML setelah page fully loaded
                html = page.content()
                soup = BeautifulSoup(html, 'html.parser')
                
                # Generate article_id
                article_id = generate_article_id(url)
                
                # Inisialisasi metadata
                metadata = {
                    'article_id': article_id,
                    'judul': '',
                    'waktu_terbit': '',
                    'editor': '',
                    'konten': '',
                    'url': url,
                    'panjang_konten': 0,
                    'author': '',
                    'thumbnail': '',
                    'kategori': '',
                    'created_at': datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S")
                }
                
                # ===== EKSTRAKSI JUDUL =====
                # Coba dari berbagai sumber
                title = ""
                
                # 1. Dari JSON-LD (paling akurat)
                ld_script = soup.find("script", {"type": "application/ld+json"})
                if ld_script:
                    try:
                        data = json.loads(ld_script.string.strip())
                        if isinstance(data, list):
                            data = data[0]
                        if data.get("@type") == "NewsArticle":
                            title = data.get("headline", "")
                    except json.JSONDecodeError:
                        pass
                
                # 2. Dari OpenGraph
                if not title:
                    title = extract_meta(soup, prop="og:title")
                
                # 3. Dari HTML structure
                if not title:
                    title_selectors = [
                        'h1.article-title',
                        'h1.title',
                        'h1.headline',
                        'div.article-header h1',
                        '.main-content h1',
                        'article h1',
                        'h1'
                    ]
                    
                    for selector in title_selectors:
                        title_elem = soup.select_one(selector)
                        if title_elem:
                            title = clean_text(title_elem.get_text())
                            if title:
                                break
                
                metadata['judul'] = title or "Judul tidak ditemukan"
                
                # ===== EKSTRAKSI TANGGAL =====
                # 1. Dari JSON-LD
                publish_date = ""
                if ld_script:
                    try:
                        data = json.loads(ld_script.string.strip())
                        if isinstance(data, list):
                            data = data[0]
                        if data.get("@type") == "NewsArticle":
                            publish_date = data.get("datePublished", "")
                    except json.JSONDecodeError:
                        pass
                
                # 2. Dari meta tags
                if not publish_date:
                    publish_date = extract_meta(soup, prop="article:published_time")
                
                # 3. Dari HTML
                if not publish_date:
                    date_selectors = [
                        'time.date',
                        '.article-date',
                        '.publish-date',
                        '.date-published',
                        'span.date',
                        '.timestamp'
                    ]
                    
                    for selector in date_selectors:
                        date_elem = soup.select_one(selector)
                        if date_elem:
                            date_text = date_elem.get_text(strip=True)
                            if date_text:
                                publish_date = date_text
                                break
                
                # Konversi ke format yang konsisten
                if publish_date:
                    dt = parse_indo_date(publish_date)
                    if dt:
                        metadata['waktu_terbit'] = dt.strftime("%Y-%m-%d %H:%M:%S")
                
                # ===== EKSTRAKSI PENULIS/EDITOR =====
                # 1. Dari JSON-LD
                author = ""
                if ld_script:
                    try:
                        data = json.loads(ld_script.string.strip())
                        if isinstance(data, list):
                            data = data[0]
                        
                        authors_data = data.get("author")
                        if isinstance(authors_data, list):
                            authors = []
                            for a in authors_data:
                                if isinstance(a, dict):
                                    authors.append(a.get("name", ""))
                                elif isinstance(a, str):
                                    authors.append(a)
                            author = ", ".join(filter(None, authors))
                        elif isinstance(authors_data, dict):
                            author = authors_data.get("name", "")
                    except json.JSONDecodeError:
                        pass
                
                # 2. Dari meta tags
                if not author:
                    author = extract_meta(soup, name="author") or extract_meta(soup, prop="article:author")
                
                # 3. Dari HTML
                if not author:
                    author_selectors = [
                        '.article-author',
                        '.author-name',
                        '.writer',
                        'span.author',
                        '.byline',
                        '.penulis'
                    ]
                    
                    for selector in author_selectors:
                        author_elem = soup.select_one(selector)
                        if author_elem:
                            author_text = author_elem.get_text(strip=True)
                            # Bersihkan label seperti "Penulis:", "Reporter:", dll
                            author_text = re.sub(r'^(Penulis|Reporter|Editor|Writer):\s*', '', author_text, flags=re.IGNORECASE)
                            if author_text:
                                author = author_text
                                break
                
                metadata['editor'] = clean_text(author)
                metadata['author'] = clean_text(author)  # Tambah field author untuk konsistensi
                
                # ===== EKSTRAKSI THUMBNAIL =====
                thumbnail = ""
                
                # 1. Dari OpenGraph
                thumbnail = extract_meta(soup, prop="og:image")
                
                # 2. Dari JSON-LD
                if not thumbnail and ld_script:
                    try:
                        data = json.loads(ld_script.string.strip())
                        if isinstance(data, list):
                            data = data[0]
                        
                        image_data = data.get("image")
                        if isinstance(image_data, dict):
                            thumbnail = image_data.get("url", "")
                        elif isinstance(image_data, list) and len(image_data) > 0:
                            first_img = image_data[0]
                            if isinstance(first_img, dict):
                                thumbnail = first_img.get("url", "")
                            elif isinstance(first_img, str):
                                thumbnail = first_img
                        elif isinstance(image_data, str):
                            thumbnail = image_data
                    except json.JSONDecodeError:
                        pass
                
                # 3. Dari HTML
                if not thumbnail:
                    img_selectors = [
                        'meta[property="og:image"]',
                        'img.article-thumbnail',
                        'img.featured-image',
                        '.article-content img:first-child',
                        'figure img',
                        '.main-content img'
                    ]
                    
                    for selector in img_selectors:
                        img_elem = soup.select_one(selector)
                        if img_elem:
                            thumbnail = img_elem.get('src') or img_elem.get('data-src') or ""
                            if thumbnail:
                                break
                
                metadata['thumbnail'] = normalize_url(thumbnail)
                
                # ===== EKSTRAKSI KATEGORI =====
                kategori = ""
                
                # 1. Dari Breadcrumb
                breadcrumb_selectors = [
                    '.breadcrumb',
                    '.category',
                    '.section',
                    'nav[aria-label="breadcrumb"]',
                    '.article-category'
                ]
                
                for selector in breadcrumb_selectors:
                    cat_elem = soup.select_one(selector)
                    if cat_elem:
                        kategori_text = cat_elem.get_text(strip=True)
                        # Ambil kategori terakhir dari breadcrumb
                        if "»" in kategori_text:
                            kategori = kategori_text.split("»")[-1].strip()
                        elif ">" in kategori_text:
                            kategori = kategori_text.split(">")[-1].strip()
                        else:
                            kategori = kategori_text
                        
                        if kategori:
                            break
                
                metadata['kategori'] = clean_text(kategori)
                
                # ===== EKSTRAKSI KONTEN UTAMA =====
                content = ""
                content_selectors = [
                    'div.article-content',
                    'article .content',
                    '.main-content .content',
                    '.detail-text',
                    '.article-body',
                    '#article-content',
                    '.post-content'
                ]
                
                content_div = None
                for selector in content_selectors:
                    content_div = soup.select_one(selector)
                    if content_div:
                        break
                
                if not content_div:
                    # Fallback: cari div dengan class yang mengandung 'content'
                    content_div = soup.find('div', class_=re.compile(r'content'))
                
                if content_div:
                    # Hapus elemen yang tidak diinginkan
                    for unwanted in content_div.select('script, style, .ads, .baca-juga, .recommended, .social-share, iframe, .comments'):
                        unwanted.decompose()
                    
                    # Ambil semua paragraf dan elemen teks penting
                    text_elements = []
                    
                    # Prioritaskan paragraf
                    paragraphs = content_div.find_all(['p', 'div', 'span'], recursive=True)
                    
                    for elem in paragraphs:
                        text = clean_text(elem.get_text(" ", strip=True))
                        if text and len(text) > 30:  # Filter teks terlalu pendek
                            # Filter konten yang tidak diinginkan
                            if not any(x in text.lower() for x in ["baca juga", "iklan", "advertisement", "baca:", "lihat juga:"]):
                                text_elements.append(text)
                    
                    # Gabungkan semua teks
                    if text_elements:
                        content = "\n\n".join(text_elements)
                    else:
                        # Fallback: ambil semua teks dari content_div
                        content = clean_text(content_div.get_text(separator='\n', strip=True))
                else:
                    # Fallback ekstrim: coba ambil dari body
                    content = clean_text(soup.get_text(separator='\n', strip=True))
                    # Hapus header dan footer
                    lines = content.split('\n')
                    filtered_lines = []
                    in_content = False
                    
                    for line in lines:
                        line_lower = line.lower()
                        if any(x in line_lower for x in [metadata['judul'].lower(), "berita terkait", "komentar", "copyright"]):
                            continue
                        if len(line.strip()) > 50:  # Anggap sebagai konten utama
                            filtered_lines.append(line)
                    
                    content = "\n".join(filtered_lines)
                
                metadata['konten'] = content
                metadata['panjang_konten'] = len(content)
                
                # ===== VALIDASI DATA =====
                if not metadata['konten'] or len(metadata['konten']) < 100:
                    print(f"⚠️  Konten terlalu pendek: {len(metadata['konten'])} karakter")
                    # Bisa return None atau tetap return metadata dengan flag
                
                return metadata, None
                
            finally:
                # Cleanup
                context.close()
                browser.close()
                
    except Exception as e:
        error_msg = f"Error scraping artikel {url}: {str(e)}"
        print(f"❌ {error_msg}")
        return None, error_msg

# =========================
# FUNGSI SCRAPING PENCARIAN
# =========================
def scrape_republika_search(
    keyword: str, 
    startdate: str, 
    enddate: str,
    max_pages: int = 50,
    progress_callback = None
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Scrape semua halaman dari pencarian Republika.co.id menggunakan Playwright
    """
    all_results = []
    page = 1
    status_msgs = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            extra_http_headers=HEADERS
        )
        page_obj = context.new_page()
        page_obj.set_default_timeout(30000)
        
        try:
            while page <= max_pages:
                try:
                    # Update progress
                    if progress_callback:
                        progress_callback((page-1)/max_pages, f"Scraping halaman {page}")
                    
                    # Encode keyword untuk URL
                    q = quote_plus(keyword)
                    url = f"{BASE_URL}/search/v3/all/{page}/?q={q}&latest_date=custom&startdate={startdate}&enddate={enddate}"
                    
                    print(f"🔍 Scraping page {page}: {url}")
                    
                    # Navigasi ke halaman pencarian
                    page_obj.goto(url, wait_until="domcontentloaded")
                    time.sleep(random.uniform(2, 3))
                    
                    # Scroll untuk trigger lazy loading
                    page_obj.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(random.uniform(1, 1.5))
                    
                    # Ambil HTML
                    html = page_obj.content()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # ===== CARI ELEMEN HASIL =====
                    results_section = None
                    
                    # Coba berbagai selector untuk menemukan hasil
                    selectors = [
                        "div.results-section",
                        ".results-section",
                        "#search .results-section",
                        "div.search-results",
                        "div.result-list",
                        "main .container > div"
                    ]
                    
                    for selector in selectors:
                        results_section = soup.select_one(selector)
                        if results_section:
                            print(f"✅ Found results with selector: {selector}")
                            break
                    
                    if not results_section:
                        status_msgs.append(f"❌ Results section not found on page {page}. Stopping.")
                        break
                    
                    # ===== EKSTRAKSI ITEM =====
                    items = []
                    
                    # Coba berbagai cara untuk menemukan item
                    item_selectors = [
                        'div.news-item',
                        'article.card',
                        'div.max-card',
                        '.search-item',
                        '.result-item',
                        'div[class*="card"]',
                        'div[class*="item"]'
                    ]
                    
                    for selector in item_selectors:
                        items = results_section.select(selector)
                        if items:
                            print(f"✅ Found {len(items)} items with selector: {selector}")
                            break
                    
                    # Fallback: cari semua link artikel
                    if not items:
                        items = results_section.find_all('a', href=re.compile(r'/berita/|/reads/|/news/'))
                        print(f"✅ Found {len(items)} items using fallback regex")
                    
                    if not items:
                        status_msgs.append(f"✅ No more results on page {page}. Stopping.")
                        break
                    
                    # ===== PROSES SETIAP ITEM =====
                    page_results = []
                    
                    for item in items:
                        try:
                            # Ekstrak URL
                            href = item.get('href', '')
                            if not href:
                                # Coba cari link di dalam elemen
                                link = item.find('a')
                                if link:
                                    href = link.get('href', '')
                            
                            if not href:
                                continue
                            
                            # Normalisasi URL
                            if href.startswith('/'):
                                full_url = urljoin(BASE_URL, href)
                            else:
                                full_url = href
                            
                            full_url = normalize_url(full_url)
                            
                            # Skip jika bukan URL artikel
                            if not re.search(r'/berita/|/reads/|/news/', full_url):
                                continue
                            
                            # Ekstrak judul
                            title = ""
                            
                            # Coba dari berbagai elemen
                            title_selectors = [
                                'h1', 'h2', 'h3', 'h4',
                                '.title', '.headline',
                                'div.news-title',
                                '.card-title'
                            ]
                            
                            for selector in title_selectors:
                                title_elem = item.select_one(selector)
                                if title_elem:
                                    title = clean_text(title_elem.get_text(strip=True))
                                    if title:
                                        break
                            
                            # Fallback: ambil teks dari item
                            if not title:
                                title = clean_text(item.get_text(strip=True))
                            
                            if not title or len(title) < 10:
                                continue
                            
                            # Ekstrak tanggal
                            date_text = ""
                            date_selectors = [
                                '.date', '.time',
                                '.timestamp',
                                '.news-source',
                                'span[class*="date"]',
                                'time'
                            ]
                            
                            for selector in date_selectors:
                                date_elem = item.select_one(selector)
                                if date_elem:
                                    date_text = date_elem.get_text(strip=True)
                                    # Cari pattern tanggal dalam teks
                                    date_match = re.search(
                                        r'(\d{1,2}\s+\w+\s+\d{4},?\s+\d{1,2}:\d{2})', 
                                        date_text
                                    )
                                    if date_match:
                                        date_text = date_match.group(1)
                                        break
                            
                            # Parse tanggal
                            published_date = ""
                            if date_text:
                                dt = parse_indo_date(date_text)
                                if dt:
                                    published_date = dt.strftime("%Y-%m-%d %H:%M:%S")
                            
                            # Ekstrak ringkasan (jika ada)
                            summary = ""
                            summary_selectors = ['.summary', '.description', '.news-description', 'p']
                            for selector in summary_selectors:
                                summary_elem = item.select_one(selector)
                                if summary_elem:
                                    summary = clean_text(summary_elem.get_text(strip=True))
                                    if summary:
                                        break
                            
                            # Ekstrak thumbnail (jika ada)
                            thumbnail = ""
                            img_selectors = ['img', '.news-image img', '.thumbnail img']
                            for selector in img_selectors:
                                img_elem = item.select_one(selector)
                                if img_elem:
                                    thumbnail = img_elem.get('src') or img_elem.get('data-src') or ""
                                    if thumbnail:
                                        thumbnail = normalize_url(thumbnail)
                                        break
                            
                            page_results.append({
                                'search_id': generate_search_id(keyword, startdate, enddate),
                                'title': title[:300],
                                'summary': summary[:500],
                                'date': published_date,
                                'date_text': date_text,
                                'url': full_url,
                                'thumbnail': thumbnail,
                                'keyword': keyword,
                                'search_date': datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S"),
                                'page': page
                            })
                            
                        except Exception as e:
                            print(f"⚠️  Error processing item: {e}")
                            continue
                    
                    # Jika tidak ada hasil yang valid, berhenti
                    if not page_results:
                        status_msgs.append(f"❌ No valid results on page {page}. Stopping.")
                        break
                    
                    # Tambahkan ke total hasil
                    all_results.extend(page_results)
                    status_msgs.append(f"✅ Found {len(page_results)} valid results on page {page}")
                    
                    # ===== CEK HALAMAN SELANJUTNYA =====
                    # Cek apakah ada tombol next
                    has_next_page = False
                    
                    # Cek di HTML
                    next_selectors = [
                        'a.next',
                        'a[rel="next"]',
                        '.pagination .next',
                        'a:contains("Selanjutnya")',
                        'a:contains("Next")',
                        'button:contains("Selanjutnya")'
                    ]
                    
                    for selector in next_selectors:
                        next_elem = soup.select_one(selector)
                        if next_elem:
                            has_next_page = True
                            break
                    
                    # Cek dengan JavaScript
                    if not has_next_page:
                        try:
                            has_next = page_obj.evaluate("""
                                () => {
                                    const nextBtns = document.querySelectorAll('a.next, a[rel="next"], .pagination .next');
                                    return nextBtns.length > 0;
                                }
                            """)
                            has_next_page = bool(has_next)
                        except:
                            pass
                    
                    if not has_next_page:
                        status_msgs.append("✅ No next page found. Stopping.")
                        break
                    
                    # Lanjut ke halaman berikutnya
                    page += 1
                    time.sleep(random.uniform(2, 4))  # Delay untuk menghindari blocking
                    
                except Exception as e:
                    error_msg = f"❌ Error on page {page}: {str(e)}"
                    print(error_msg)
                    status_msgs.append(error_msg)
                    break
            
        finally:
            # Cleanup
            context.close()
            browser.close()
    
    # Remove duplicates berdasarkan URL
    seen_urls = set()
    unique_results = []
    
    for result in all_results:
        if result['url'] not in seen_urls:
            seen_urls.add(result['url'])
            unique_results.append(result)
    
    status_msgs.append(f"\n📊 FINAL: Found {len(unique_results)} unique articles from {page-1} pages")
    
    return unique_results, "\n".join(status_msgs)

# =========================
# FUNGSI BATCH PROCESSING
# =========================
def scrape_republika_batch(
    urls: List[str],
    max_workers: int = 3,
    progress_callback = None
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Scrape multiple articles in batch dengan threading
    """
    import concurrent.futures
    from concurrent.futures import ThreadPoolExecutor
    
    all_articles = []
    errors = []
    
    def process_url(url):
        """Process single URL"""
        try:
            if progress_callback:
                progress_callback(0, f"Processing: {url[:50]}...")
            
            metadata, error = extract_republika_article(url)
            
            if metadata:
                return metadata
            else:
                return {"error": error, "url": url}
                
        except Exception as e:
            return {"error": str(e), "url": url}
    
    # Gunakan ThreadPoolExecutor untuk parallel processing
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit semua task
        future_to_url = {
            executor.submit(process_url, url): url 
            for url in urls
        }
        
        # Kumpulkan hasil
        for i, future in enumerate(concurrent.futures.as_completed(future_to_url)):
            url = future_to_url[future]
            
            if progress_callback:
                progress_callback(
                    (i+1)/len(urls), 
                    f"Processed {i+1}/{len(urls)} articles"
                )
            
            try:
                result = future.result()
                if "error" in result:
                    errors.append(f"{url}: {result['error']}")
                else:
                    all_articles.append(result)
            except Exception as e:
                errors.append(f"{url}: {str(e)}")
    
    return all_articles, errors

# =========================
# FUNGSI UNTUK APPEND KE CSV
# =========================
def append_to_csv(df: pd.DataFrame, filename: str) -> None:
    """Append DataFrame to existing CSV file or create new one"""
    try:
        if os.path.exists(filename):
            # Load existing data untuk cek duplikat
            try:
                existing_df = pd.read_csv(filename)
                # Cek kolom untuk deduplikasi
                if 'url' in df.columns and 'url' in existing_df.columns:
                    # Filter out URLs yang sudah ada
                    existing_urls = set(existing_df['url'].dropna().astype(str))
                    new_df = df[~df['url'].astype(str).isin(existing_urls)]
                    
                    if len(new_df) == 0:
                        print(f"ℹ️  Semua data sudah ada di {filename}")
                        return
                    
                    df = new_df
                
                # Append tanpa header
                df.to_csv(filename, mode='a', header=False, index=False, encoding='utf-8-sig')
                print(f"✅ {len(df)} data appended to {filename} (deduplicated)")
                
            except Exception as e:
                print(f"⚠️  Error checking duplicates: {e}. Appending all data.")
                df.to_csv(filename, mode='a', header=False, index=False, encoding='utf-8-sig')
                print(f"✅ Data appended to {filename}")
        else:
            # File tidak ada, buat baru dengan header
            df.to_csv(filename, index=False, encoding='utf-8-sig')
            print(f"✅ New file created: {filename}")
            
    except Exception as e:
        print(f"❌ Error saving to CSV: {e}")
        # Fallback: save dengan format berbeda
        try:
            backup_name = f"{filename}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            df.to_csv(backup_name, index=False, encoding='utf-8-sig')
            print(f"⚠️  Data saved to backup: {backup_name}")
        except:
            print("❌ Critical: Could not save data at all!")

# =========================
# CONTOH PENGGUNAAN
# =========================
if __name__ == "__main__":
    import os
    
    # Contoh 1: Scraping artikel tunggal
    print("=" * 50)
    print("CONTOH 1: Scraping Artikel Tunggal")
    print("=" * 50)
    
    test_url = "https://republika.co.id/berita/syqh4p384/sah-berjalan-di-tahun-2025-ini-harga-tiket-umrah"
    metadata, error = extract_republika_article(test_url)
    
    if metadata:
        print(f"✅ Berhasil scraping: {metadata['judul']}")
        print(f"   Tanggal: {metadata['waktu_terbit']}")
        print(f"   Penulis: {metadata['editor']}")
        print(f"   Panjang: {metadata['panjang_konten']} karakter")
        print(f"   Thumbnail: {metadata['thumbnail'][:50]}...")
    else:
        print(f"❌ Gagal: {error}")
    
    # Contoh 2: Scraping pencarian
    print("\n" + "=" * 50)
    print("CONTOH 2: Scraping Pencarian")
    print("=" * 50)
    
    def simple_progress(progress, desc):
        print(f"   Progress: {progress*100:.1f}% - {desc}")
    
    results, status = scrape_republika_search(
        keyword="teknologi",
        startdate="2024-01-01",
        enddate="2024-03-15",
        max_pages=2,
        progress_callback=simple_progress
    )
    
    print(f"\n📊 Hasil pencarian:")
    print(f"   Total artikel: {len(results)}")
    if results:
        print(f"   Contoh artikel pertama:")
        print(f"   Judul: {results[0]['title']}")
        print(f"   URL: {results[0]['url']}")
        print(f"   Tanggal: {results[0]['date']}")
    
    # Contoh 3: Batch processing
    print("\n" + "=" * 50)
    print("CONTOH 3: Batch Processing Artikel")
    print("=" * 50)
    
    if len(results) > 3:
        sample_urls = [r['url'] for r in results[:3]]
        
        articles, errors = scrape_republika_batch(
            urls=sample_urls,
            max_workers=2,
            progress_callback=simple_progress
        )
        
        print(f"\n📊 Hasil batch processing:")
        print(f"   Artikel sukses: {len(articles)}")
        print(f"   Error: {len(errors)}")
        
        # Save to CSV
        if articles:
            df = pd.DataFrame(articles)
            append_to_csv(df, "republika_articles.csv")
            
            # Tampilkan preview
            print(f"\n📋 Preview data:")
            print(df[['judul', 'waktu_terbit', 'panjang_konten']].head())
    
    print("\n" + "=" * 50)
    print("SCRAPER SIAP DIGUNAKAN!")
    print("=" * 50)