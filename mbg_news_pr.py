import re
import time
import random
import hashlib
import json
import logging
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, urljoin, quote_plus, urlencode
from typing import Optional, Tuple, List, Dict, Any

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# =========================
# KONFIGURASI & KONSTANTA
# =========================
WIB = ZoneInfo("Asia/Jakarta")
BASE_URL = "https://www.pikiran-rakyat.com"
SEARCH_TEMPLATE = "https://www.pikiran-rakyat.com/search?q={query}&gsc.page={page}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Referer": "https://www.google.com/"
}

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
LOG = logging.getLogger("pikiran_rakyat_scraper")

# =========================
# FUNGSI UTILITAS
# =========================
def clean_text(text: str) -> str:
    """Membersihkan teks dari karakter tidak diinginkan dan spasi berlebihan."""
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^\x00-\x7F]+', ' ', text)
    text = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', text)
    text = re.sub(r'[^\w\s.,!?;:()\-—–"\']', '', text)
    return text.strip()

def normalize_url(url: str, base_url: str = BASE_URL) -> str:
    """Normalisasi URL."""
    if not url:
        return ""
    if url.startswith("/"):
        url = urljoin(base_url, url)
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()

def generate_article_id(url: str) -> str:
    """Generate unique article_id dari URL."""
    return hashlib.md5(url.encode()).hexdigest()[:16]

def parse_pikiran_date(date_str: str) -> Optional[datetime]:
    """
    Parse tanggal dari format Pikiran-Rakyat.
    Format: '2 Februari 2026, 05:34 WIB'
    """
    if not date_str:
        return None
    
    months = {
        "Januari": 1, "Februari": 2, "Maret": 3, "April": 4,
        "Mei": 5, "Juni": 6, "Juli": 7, "Agustus": 8,
        "September": 9, "Oktober": 10, "November": 11, "Desember": 12
    }
    
    try:
        # Hilangkan 'WIB' dan bersihkan
        date_str = date_str.replace("WIB", "").strip()
        
        # Pattern untuk format: '2 Februari 2026, 05:34'
        pattern = r"(\d{1,2})\s+(\w+)\s+(\d{4}),\s+(\d{1,2}):(\d{2})"
        match = re.search(pattern, date_str)
        
        if match:
            day, month_str, year, hour, minute = match.groups()
            month = months.get(month_str)
            if month:
                return datetime(
                    int(year), month, int(day),
                    int(hour), int(minute)
                ).replace(tzinfo=WIB)
    except Exception as e:
        LOG.warning(f"Gagal parse tanggal '{date_str}': {e}")
    
    return None

def extract_meta(soup: BeautifulSoup, name: str = None, property: str = None) -> str:
    """Ekstrak metadata dari tag meta."""
    attr = {"property": property} if property else {"name": name}
    meta = soup.find("meta", attrs=attr)
    return clean_text(meta["content"]) if meta and meta.get("content") else ""

# =========================
# PARSER HASIL PENCARIAN (DIUPDATE BERDASARKAN STRUKTUR BARU)
# =========================
def parse_search_results(html: str, keyword: str, page_num: int) -> List[Dict[str, Any]]:
    """
    Parse halaman hasil pencarian Pikiran-Rakyat.
    DIUPDATE berdasarkan struktur baru.
    """
    soup = BeautifulSoup(html, 'html.parser')
    results = []
    
    LOG.info(f"Mengurai hasil pencarian halaman {page_num}")
    
    # ===== STRATEGI UTAMA: Cari semua item dengan latest__title =====
    latest_titles = soup.find_all('h2', class_='latest__title')
    
    if latest_titles:
        LOG.info(f"Ditemukan {len(latest_titles)} judul artikel dengan class latest__title")
        
        for title_elem in latest_titles:
            try:
                # 1. Cari link artikel dari parent atau sibling
                link = None
                
                # Coba cari link dalam title_elem
                link = title_elem.find('a')
                
                # Jika tidak ada, cari di parent container
                if not link:
                    parent = title_elem.parent
                    if parent:
                        link = parent.find('a', href=True)
                
                # Jika masih tidak ada, cari di grandparent
                if not link:
                    grandparent = title_elem.find_parent('div', class_='latest__item')
                    if grandparent:
                        link = grandparent.find('a', href=True)
                
                if not link or not link.get('href'):
                    continue
                
                href = link.get('href', '')
                url = normalize_url(href)
                
                # Validasi URL
                if not url or BASE_URL not in url:
                    continue
                
                # 2. Ekstrak judul dari latest__title
                title = clean_text(title_elem.get_text())
                if not title or len(title) < 10:
                    continue
                
                # 3. Ekstrak tanggal dari latest__date
                date_text = ""
                
                # Cari elemen tanggal di sekitar judul
                # Pertama, cari di parent container
                parent_container = title_elem.find_parent(['div', 'article'])
                if parent_container:
                    date_elem = parent_container.find('date', class_='latest__date')
                    if not date_elem:
                        date_elem = parent_container.find('span', class_='latest__date')
                    if not date_elem:
                        date_elem = parent_container.find('time', class_='latest__date')
                    
                    if date_elem:
                        date_text = clean_text(date_elem.get_text())
                
                # Jika tidak ditemukan, cari di seluruh halaman dengan class latest__date
                if not date_text:
                    all_date_elems = soup.find_all(['date', 'span', 'time'], class_='latest__date')
                    for date_elem in all_date_elems:
                        # Cek apakah date_elem ini terkait dengan judul yang sedang diproses
                        if title_elem in date_elem.find_parent().find_all():
                            date_text = clean_text(date_elem.get_text())
                            break
                
                # Parse tanggal
                date_parsed = ""
                if date_text:
                    publish_date = parse_pikiran_date(date_text)
                    date_parsed = publish_date.strftime("%Y-%m-%d %H:%M:%S") if publish_date else ""
                
                # 4. Ekstrak kategori
                category = ""
                category_elem = parent_container.find('h4', class_='latest__subtitle') if parent_container else None
                if category_elem:
                    category_link = category_elem.find('a')
                    if category_link:
                        category = clean_text(category_link.get_text())
                
                # 5. Ekstrak ringkasan
                summary = ""
                if parent_container:
                    summary_elem = parent_container.find(['p', 'div'], class_=re.compile(r'summary|excerpt|desc'))
                    if summary_elem:
                        summary = clean_text(summary_elem.get_text())[:200]
                
                # 6. Ekstrak gambar
                image_url = ""
                if parent_container:
                    img_elem = parent_container.find('div', class_='latest__img')
                    if img_elem:
                        img_tag = img_elem.find('img')
                        if img_tag and img_tag.get('src'):
                            image_url = normalize_url(img_tag.get('src'))
                
                results.append({
                    'search_keyword': keyword,
                    'search_page': page_num,
                    'title': title[:300],
                    'url': url,
                    'category': category,
                    'date_text': date_text[:100],
                    'date_parsed': date_parsed,
                    'summary': summary,
                    'image_url': image_url,
                    'found_via': 'latest__title_element',
                    'has_latest_date': bool(date_text)
                })
                
                LOG.debug(f"Artikel ditemukan: {title[:50]}... | {date_text}")
                
            except Exception as e:
                LOG.debug(f"Error parsing latest__title item: {e}")
                continue
    
    # ===== STRATEGI ALTERNATIF: Cari melalui latest__item =====
    if len(results) == 0:
        LOG.warning("latest__title tidak ditemukan, mencoba latest__item...")
        
        latest_items = soup.find_all('div', class_='latest__item')
        
        for item in latest_items:
            try:
                # 1. Cari link
                link = item.find('a', href=True)
                if not link:
                    continue
                
                href = link.get('href', '')
                url = normalize_url(href)
                
                if not url or BASE_URL not in url:
                    continue
                
                # 2. Ekstrak judul dari latest__title
                title_elem = item.find('h2', class_='latest__title')
                if not title_elem:
                    continue
                
                title = clean_text(title_elem.get_text())
                if not title or len(title) < 10:
                    continue
                
                # 3. Ekstrak tanggal dari latest__date
                date_text = ""
                date_elem = item.find('date', class_='latest__date')
                if not date_elem:
                    date_elem = item.find('span', class_='latest__date')
                if not date_elem:
                    date_elem = item.find('time', class_='latest__date')
                
                if date_elem:
                    date_text = clean_text(date_elem.get_text())
                
                # Parse tanggal
                date_parsed = ""
                if date_text:
                    publish_date = parse_pikiran_date(date_text)
                    date_parsed = publish_date.strftime("%Y-%m-%d %H:%M:%S") if publish_date else ""
                
                # 4. Ekstrak informasi lainnya
                category = ""
                category_elem = item.find('h4', class_='latest__subtitle')
                if category_elem:
                    category_link = category_elem.find('a')
                    if category_link:
                        category = clean_text(category_link.get_text())
                
                # 5. Ekstrak gambar
                image_url = ""
                img_elem = item.find('div', class_='latest__img')
                if img_elem:
                    img_tag = img_elem.find('img')
                    if img_tag and img_tag.get('src'):
                        image_url = normalize_url(img_tag.get('src'))
                
                results.append({
                    'search_keyword': keyword,
                    'search_page': page_num,
                    'title': title[:300],
                    'url': url,
                    'category': category,
                    'date_text': date_text[:100],
                    'date_parsed': date_parsed,
                    'summary': "",
                    'image_url': image_url,
                    'found_via': 'latest__item_container',
                    'has_latest_date': bool(date_text)
                })
                
            except Exception as e:
                LOG.debug(f"Error parsing latest__item: {e}")
                continue
    
    # ===== STRATEGI FALLBACK: Cari semua link yang mungkin artikel =====
    if len(results) < 3:
        LOG.info("Hasil masih sedikit, menggunakan metode fallback...")
        
        # Pattern URL artikel Pikiran-Rakyat
        article_patterns = [
            r'/[\w\-]+/pr-\d+/',  # /category/pr-12345678/
            r'/\d{4}/\d{2}/\d{2}/',  # /2024/12/31/
            r'-\d+\.html$',  # -123456.html
        ]
        
        all_links = soup.find_all('a', href=True)
        for link in all_links:
            href = link.get('href', '')
            
            # Skip jika bukan URL yang relevan
            if not href or any(x in href for x in ['#', 'javascript:', 'mailto:', 'tel:']):
                continue
            
            # Cek pattern artikel
            is_article = False
            for pattern in article_patterns:
                if re.search(pattern, href):
                    is_article = True
                    break
            
            if not is_article and ('pikiran-rakyat.com' not in href or href.startswith('/')):
                # Cek jika URL memiliki struktur artikel umum
                if re.search(r'/\w+-\w+/', href) and not re.search(r'/(search|tag|category|author)/', href):
                    is_article = True
            
            if is_article:
                url = normalize_url(href)
                
                # Skip jika sudah ada
                if any(r['url'] == url for r in results):
                    continue
                
                # Ekstrak judul
                title = clean_text(link.get_text())
                if not title or len(title) < 10:
                    # Coba cari judul di parent
                    parent = link.parent
                    if parent:
                        title_elem = parent.find(['h1', 'h2', 'h3', 'h4'])
                        if title_elem:
                            title = clean_text(title_elem.get_text())
                
                if title and len(title) >= 10:
                    results.append({
                        'search_keyword': keyword,
                        'search_page': page_num,
                        'title': title[:300],
                        'url': url,
                        'category': "",
                        'date_text': "",
                        'date_parsed': "",
                        'summary': "",
                        'image_url': "",
                        'found_via': 'link_pattern_fallback',
                        'pattern_matched': True
                    })
    
    # ===== DEDUPLIKASI BERDASARKAN URL =====
    unique_results = []
    seen_urls = set()
    
    for result in results:
        url = result.get('url', '')
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_results.append(result)
    
    LOG.info(f"Total {len(unique_results)} artikel unik ditemukan di halaman {page_num}")
    
    # Tampilkan hasil untuk debugging
    if unique_results:
        LOG.info(f"Contoh hasil dari halaman {page_num}:")
        for i, result in enumerate(unique_results[:3]):
            LOG.info(f"  {i+1}. {result['title'][:50]}... | {result['date_text']}")
    
    return unique_results

# =========================
# PARSER ARTIKEL DETAIL (DIUPDATE BERDASARKAN STRUKTUR BARU)
# =========================
def parse_article_page(html: str, url: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Parse halaman artikel individual Pikiran-Rakyat.
    DIUPDATE berdasarkan struktur: <article class="read__content clearfix">
    """
    try:
        soup = BeautifulSoup(html, 'html.parser')
        article_id = generate_article_id(url)

        LOG.info(f"Memulai parsing artikel: {url}")
        
        # ===== 1. EKSTRAKSI JUDUL =====
        title = ""
        
        # STRATEGI 1: Cari dari read__title (berdasarkan HTML)
        title_div = soup.select_one('div.read__title')
        if title_div:
            h1_elem = title_div.find('h1')
            if h1_elem:
                title = clean_text(h1_elem.get_text())
                LOG.info(f"Judul ditemukan via read__title: {title[:50]}...")
        
        # STRATEGI 2: Cari h1 langsung dengan berbagai class
        if not title:
            h1_selectors = [
                'h1.read__title',
                'h1.title',
                'h1.entry-title',
                'h1.headline',
                'h1.article-title'
            ]
            
            for selector in h1_selectors:
                h1_elem = soup.select_one(selector)
                if h1_elem:
                    title = clean_text(h1_elem.get_text())
                    LOG.info(f"Judul ditemukan via {selector}: {title[:50]}...")
                    break
        
        # STRATEGI 3: Dari meta tag
        if not title:
            title = extract_meta(soup, property="og:title")
            if title:
                LOG.info(f"Judul ditemukan via og:title: {title[:50]}...")
        
        # STRATEGI 4: Cari h1 pertama
        if not title:
            h1_elem = soup.find('h1')
            if h1_elem:
                title = clean_text(h1_elem.get_text())
                LOG.info(f"Judul ditemukan via h1 pertama: {title[:50]}...")
        
        if not title:
            title = "Judul tidak ditemukan"
            LOG.warning("Judul tidak ditemukan di halaman artikel")

        # ===== 2. EKSTRAKSI WAKTU TERBIT =====
        publish_date = None
        
        # STRATEGI 1: Cari dari read__content > span.date_detail
        read_content_div = soup.select_one('div.read__content')
        if read_content_div:
            date_span = read_content_div.find('span', class_='date_detail')
            if date_span:
                date_text = clean_text(date_span.get_text())
                publish_date = parse_pikiran_date(date_text)
                if publish_date:
                    LOG.info(f"Tanggal ditemukan via date_detail: {date_text}")
        
        # STRATEGI 2: Cari dari meta tag
        if not publish_date:
            meta_date = extract_meta(soup, property="article:published_time")
            if meta_date:
                try:
                    # Parse ISO format
                    publish_date = datetime.fromisoformat(meta_date.replace('Z', '+00:00'))
                    if publish_date.tzinfo is None:
                        publish_date = publish_date.replace(tzinfo=WIB)
                    LOG.info(f"Tanggal ditemukan via meta tag: {meta_date}")
                except Exception as e:
                    LOG.debug(f"Gagal parse meta date: {e}")
        
        # STRATEGI 3: Cari pola tanggal di seluruh halaman
        if not publish_date:
            date_pattern = re.compile(r'(\d{1,2}\s+\w+\s+\d{4},?\s+\d{1,2}:\d{2}\s+WIB)')
            for element in soup.find_all(text=date_pattern):
                if element and date_pattern.search(str(element)):
                    date_match = date_pattern.search(str(element))
                    date_text = date_match.group(1) if date_match else ""
                    if date_text:
                        publish_date = parse_pikiran_date(date_text)
                        if publish_date:
                            LOG.info(f"Tanggal ditemukan via regex: {date_text}")
                            break
        
        final_date = publish_date.strftime("%Y-%m-%d %H:%M:%S") if publish_date else ""
        
        if not final_date:
            LOG.warning("Tanggal tidak ditemukan di halaman artikel")

        # ===== 3. EKSTRAKSI KONTEN UTAMA =====
        content_parts = []
        
        # STRATEGI UTAMA: Cari article dengan class read__content clearfix
        article_content = soup.select_one('article.read__content.clearfix')
        
        if not article_content:
            # Coba variasi selector
            article_content = soup.select_one('article.read__content')
        
        if article_content:
            LOG.info("Menggunakan article.read__content untuk ekstraksi konten")
            
            # HAPUS elemen yang tidak diinginkan sebelum ekstraksi
            elements_to_remove = [
                'script', 'style', 'iframe', 'noscript',
                '.ads', '.iklan', '.advertisement', '.google-auto-placed',
                '.ap_container', '.mt1', '.read__tagging', '.read__related',
                '.latest', '.prads', '.coverwa', '.social', '.photo',
                '.photo__img', '.photo__caption', '.cards_list', '.cards__item',
                '.read__info', '.read__title', 'div.tags', 'section.read__tagging',
                'div.photo', 'div.social', 'div.mt1', 'div.coverwa'
            ]
            
            for selector in elements_to_remove:
                for unwanted in article_content.select(selector):
                    unwanted.decompose()
            
            # Hapus juga semua div dengan class yang mengandung kata tertentu
            for div in article_content.find_all('div', class_=True):
                class_str = ' '.join(div.get('class', []))
                if any(word in class_str.lower() for word in ['ad', 'iklan', 'social', 'photo', 'tag', 'related', 'latest', 'prads']):
                    div.decompose()
            
            # Ekstrak semua paragraf (p) yang merupakan konten artikel
            paragraphs = article_content.find_all('p')
            LOG.info(f"Menemukan {len(paragraphs)} paragraf dalam artikel")
            
            for p in paragraphs:
                text = clean_text(p.get_text())
                
                # Filter: teks harus cukup panjang dan bukan bagian dari navigasi/meta
                if text and len(text) > 20:
                    # Filter out common non-content text
                    exclude_keywords = [
                        'baca juga', 'iklan', 'advertisement', 'related article',
                        'komentar', 'share', 'follow', 'tags:', 'kategori:',
                        'www.pikiran-rakyat.com', 'update terbaru', 'google news',
                        'berita pilihan', 'konten promosi', 'sponsored', 'promosi',
                        'penulis:', 'editor:', 'foto:', 'sumber:', 'dok:'
                    ]
                    
                    if not any(keyword in text.lower() for keyword in exclude_keywords):
                        # Hapus tag yang mungkin masih ada dalam teks
                        text = re.sub(r'<[^>]+>', '', text)
                        content_parts.append(text)
        else:
            LOG.warning("article.read__content tidak ditemukan, mencari alternatif...")
            
            # STRATEGI ALTERNATIF: Cari div dengan class read__content
            read_content_div = soup.select_one('div.read__content')
            if read_content_div:
                LOG.info("Menggunakan div.read__content untuk ekstraksi konten")
                paragraphs = read_content_div.find_all('p')
                
                for p in paragraphs:
                    text = clean_text(p.get_text())
                    if text and len(text) > 20 and not text.startswith(('Penulis:', 'Editor:', 'www.Pikiran-Rakyat.com')):
                        content_parts.append(text)
        
        # Gabungkan semua bagian konten
        content = "\n\n".join(content_parts).strip()
        
        # Jika konten terlalu pendek, coba metode lain
        if len(content) < 100:
            LOG.info(f"Konten terlalu pendek ({len(content)} karakter), mencoba metode ekstraksi alternatif")
            
            # Coba ambil semua teks dari area artikel
            article_body = soup.select_one('[itemprop="articleBody"]')
            if article_body:
                # Hapus elemen yang tidak diinginkan
                for unwanted in article_body.select('script, style, .ads, .iklan, .google-auto-placed'):
                    unwanted.decompose()
                
                all_text = clean_text(article_body.get_text(separator='\n', strip=True))
                if len(all_text) > len(content):
                    content = all_text
                    LOG.info(f"Metode alternatif meningkatkan konten menjadi {len(content)} karakter")
            
            # Jika masih pendek, coba dari body langsung dengan filter
            if len(content) < 100:
                body_text = soup.get_text(separator='\n', strip=True)
                lines = body_text.split('\n')
                content_lines = []
                
                for line in lines:
                    line_clean = clean_text(line)
                    if len(line_clean) > 50:
                        exclude_keywords = [
                            'iklan', 'advertisement', 'baca juga', 'komentar',
                            'share', 'follow us', 'related posts', 'popular posts',
                            'tags:', 'categories:', '©', 'all rights reserved',
                            'privacy policy', 'terms of use', 'cookie policy'
                        ]
                        
                        if not any(keyword in line_clean.lower() for keyword in exclude_keywords):
                            content_lines.append(line_clean)
                
                if content_lines:
                    content = "\n\n".join(content_lines[:30])  # Ambil 30 baris pertama
                    LOG.info(f"Metode body text meningkatkan konten menjadi {len(content)} karakter")
        
        LOG.info(f"Panjang konten akhir: {len(content)} karakter")

        # ===== 4. EKSTRAKSI PENULIS & EDITOR =====
        author, editor = "", ""
        
        # STRATEGI 1: Cari dari read__info__author
        read_info_author = soup.select_one('div.read__info__author')
        if read_info_author:
            # Cari penulis
            author_elem = read_info_author.find('a', href=re.compile(r'/author/'))
            if author_elem:
                author = clean_text(author_elem.get_text())
            
            # Cari editor
            editor_spans = read_info_author.find_all('span', class_='read_contributor')
            for span in editor_spans:
                if 'Editor:' in span.get_text():
                    editor_text = clean_text(span.get_text())
                    editor = editor_text.replace('Editor:', '').strip()
                    break
        
        # STRATEGI 2: Fallback - cari pola teks
        if not author or not editor:
            author_pattern = re.compile(r'Penulis[:\s]+([^\n\r]+)', re.IGNORECASE)
            editor_pattern = re.compile(r'Editor[:\s]+([^\n\r]+)', re.IGNORECASE)
            
            for elem in soup.find_all(['p', 'div', 'span']):
                text = clean_text(elem.get_text())
                if not author:
                    author_match = author_pattern.search(text)
                    if author_match:
                        author = clean_text(author_match.group(1))
                if not editor:
                    editor_match = editor_pattern.search(text)
                    if editor_match:
                        editor = clean_text(editor_match.group(1))
                if author and editor:
                    break

        # ===== 5. EKSTRAKSI FOTO/ILUSTRASI =====
        photo_info = ""
        photo_div = soup.select_one('div.photo')
        if photo_div:
            # Ambil caption foto jika ada
            caption = photo_div.select_one('div.photo__caption')
            if caption:
                photo_info = clean_text(caption.get_text())
            
            # Ambil URL gambar utama jika ada
            img_tag = photo_div.find('img')
            if img_tag and img_tag.get('src'):
                photo_url = normalize_url(img_tag.get('src'))
                if photo_info:
                    photo_info += f" | URL: {photo_url}"
                else:
                    photo_info = f"URL gambar: {photo_url}"

        # ===== 6. EKSTRAKSI TAG & KATEGORI =====
        tags = []
        
        # Cari div tags
        tags_section = soup.select_one('section.read__tagging')
        if tags_section:
            tags_div = tags_section.select_one('div.tag')
            if tags_div:
                for tag_link in tags_div.find_all('a'):
                    tag_text = clean_text(tag_link.get_text())
                    if tag_text:
                        tags.append(tag_text)
        
        # Ekstrak kategori dari URL atau breadcrumb
        category = ""
        url_match = re.search(r'/(news|entertainment|sports|technology|pendidikan)/', url)
        if url_match:
            category = url_match.group(1)

        # ===== 7. EKSTRAKSI KETERANGAN STRUKTUR HTML =====
        html_structure = {
            'has_read_title': bool(soup.select_one('div.read__title')),
            'has_read_content': bool(soup.select_one('article.read__content')),
            'has_read_info': bool(soup.select_one('div.read__info')),
            'has_photo_div': bool(photo_div),
            'has_read_tagging': bool(tags_section),
            'total_paragraphs': len(content_parts),
            'content_length': len(content)
        }

        # ===== 8. KOMPILASI DATA ARTIKEL =====
        metadata = {
            'article_id': article_id,
            'sumber': 'pikiran-rakyat',
            'judul': title,
            'waktu_terbit': final_date,
            'author': author,
            'editor': editor,
            'konten': content,
            'panjang_konten': len(content),
            'kategori': category or 'news',
            'tags': ', '.join(tags) if tags else '',
            'photo_info': photo_info,
            'url': url,
            'scraped_at': datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S"),
            'html_structure_found': html_structure
        }

        # ===== 9. VALIDASI DATA =====
        validation_warnings = []
        
        if len(content) < 100:
            validation_warnings.append(f"Konten terlalu pendek ({len(content)} karakter)")
        
        if not title or title == "Judul tidak ditemukan":
            validation_warnings.append("Judul tidak valid")
        
        if not final_date:
            validation_warnings.append("Tanggal tidak ditemukan")
        
        if validation_warnings:
            LOG.warning(f"Validasi artikel {url}: {'; '.join(validation_warnings)}")
        
        LOG.info(f"""
Artikel berhasil diparsing:
- Judul: {title[:60]}...
- Tanggal: {final_date}
- Panjang konten: {len(content)} karakter
- Penulis: {author or 'Tidak diketahui'}
- Editor: {editor or 'Tidak diketahui'}
- Kategori: {category}
- Tags: {', '.join(tags) if tags else 'Tidak ada'}
        """)

        return metadata, None

    except Exception as e:
        error_msg = f"Error parsing article {url}: {str(e)}"
        LOG.error(error_msg, exc_info=True)
        return None, error_msg

# =========================
# FUNGSI UTAMA (SISA KODE TETAP SAMA)
# =========================
def search_pikiran_rakyat(
    keyword: str,
    start_page: int = 1,
    end_page: int = 5,
    debug_mode: bool = False
) -> Dict[str, Any]:
    """
    Fungsi utama untuk pencarian dan scraping Pikiran-Rakyat.
    """
    LOG.info(f"Memulai pencarian Pikiran-Rakyat untuk: '{keyword}'")
    LOG.info(f"Rentang halaman: {start_page} sampai {end_page}")
    
    all_search_results = []
    all_articles = []
    errors = []
    
    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(
            headless=not debug_mode,
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
        page.set_default_timeout(30000)
        
        try:
            # ===== PHASE 1: COLLECT SEARCH RESULTS =====
            LOG.info("Fase 1: Mengumpulkan hasil pencarian...")
            
            for page_num in range(start_page, end_page + 1):
                try:
                    search_url = SEARCH_TEMPLATE.format(query=quote_plus(keyword), page=page_num)
                    LOG.info(f"Mengakses halaman {page_num}: {search_url}")
                    
                    # Navigasi ke halaman pencarian
                    page.goto(search_url, wait_until="domcontentloaded")
                    time.sleep(random.uniform(2, 3))
                    
                    # Scroll untuk memuat konten lazy load
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
                    time.sleep(1)
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(1)
                    
                    # DEBUG: Simpan screenshot jika mode debug
                    if debug_mode:
                        screenshot_path = f"search_page_{page_num}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                        page.screenshot(path=screenshot_path)
                        LOG.info(f"Screenshot disimpan: {screenshot_path}")
                    
                    # Ambil HTML
                    html = page.content()
                    
                    # Parse hasil
                    page_results = parse_search_results(html, keyword, page_num)
                    
                    if not page_results:
                        LOG.warning(f"Tidak ada hasil ditemukan di halaman {page_num}")
                        if page_num > start_page + 1:  # Berhenti setelah 2 halaman kosong berturut-turut
                            LOG.info(f"Berhenti karena halaman {page_num} kosong")
                            break
                    else:
                        all_search_results.extend(page_results)
                        LOG.info(f"Ditemukan {len(page_results)} artikel di halaman {page_num}")
                    
                    # Delay antar halaman
                    if page_num < end_page:
                        time.sleep(random.uniform(2, 4))
                        
                except PlaywrightTimeoutError:
                    LOG.error(f"Timeout saat mengakses halaman {page_num}")
                    errors.append(f"Page {page_num}: Timeout error")
                    continue
                except Exception as e:
                    LOG.error(f"Error di halaman {page_num}: {str(e)}")
                    errors.append(f"Page {page_num}: {str(e)}")
                    continue
            
            # ===== PHASE 2: DEDUPLICATE URLS =====
            LOG.info("Fase 2: Deduplikasi URL...")
            
            seen_urls = set()
            unique_search_data = []
            
            for result in all_search_results:
                url = result.get('url', '')
                if url and url not in seen_urls and BASE_URL in url:
                    seen_urls.add(url)
                    unique_search_data.append((url, result))
            
            LOG.info(f"Total URL unik: {len(unique_search_data)} dari {len(all_search_results)} hasil")
            
            # ===== PHASE 3: SCRAPE ARTICLE DETAILS =====
            LOG.info("Fase 3: Scraping detail artikel...")
            
            for idx, (url, search_data) in enumerate(unique_search_data, 1):
                try:
                    LOG.info(f"Scraping artikel {idx}/{len(unique_search_data)}: {url}")
                    
                    # Navigasi ke artikel
                    page.goto(url, wait_until="domcontentloaded")
                    time.sleep(random.uniform(2, 3))
                    
                    # Scroll untuk memuat konten
                    page.evaluate("window.scrollBy(0, 800)")
                    time.sleep(1)
                    
                    # DEBUG: Simpan screenshot artikel jika mode debug
                    if debug_mode:
                        screenshot_path = f"article_{idx}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                        page.screenshot(path=screenshot_path)
                        LOG.info(f"Screenshot artikel disimpan: {screenshot_path}")
                    
                    # Ambil HTML
                    article_html = page.content()
                    
                    # Parse artikel
                    metadata, error = parse_article_page(article_html, url)
                    
                    if metadata:
                        # Gabungkan dengan data pencarian
                        metadata.update({
                            'search_keyword': search_data.get('search_keyword', keyword),
                            'search_page': search_data.get('search_page', 0),
                            'search_category': search_data.get('category', ''),
                            'search_image_url': search_data.get('image_url', ''),
                        })
                        all_articles.append(metadata)
                        LOG.info(f"✅ Berhasil: {metadata['judul'][:50]}...")
                    else:
                        errors.append(f"{url}: {error}")
                        LOG.error(f"❌ Gagal: {error}")
                    
                    # Delay antar artikel
                    if idx < len(unique_search_data):
                        time.sleep(random.uniform(2, 3))
                        
                except Exception as e:
                    error_msg = f"{url}: {str(e)}"
                    errors.append(error_msg)
                    LOG.error(f"❌ Error scraping {url}: {str(e)}")
                    continue
        
        finally:
            # Cleanup
            context.close()
            browser.close()
    
    # ===== COMPILE RESULTS =====
    result_data = {
        'keyword': keyword,
        'pages_searched': f"{start_page}-{end_page}",
        'total_search_results': len(all_search_results),
        'total_unique_urls': len(unique_search_data),
        'total_articles_scraped': len(all_articles),
        'total_errors': len(errors),
        'articles': all_articles,
        'search_results': all_search_results,
        'errors': errors,
        'timestamp': datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S")
    }
    
    return result_data


# =========================
# FUNGSI EKSPOR DATA
# =========================
def export_results(
    result_data: Dict[str, Any],
    output_dir: str = "data",
    formats: List[str] = ['csv', 'excel']
) -> Dict[str, str]:
    """Ekspor hasil scraping ke berbagai format."""
    import os
    
    # Buat direktori jika belum ada
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    keyword_clean = re.sub(r'[^\w\-]', '_', result_data['keyword'])
    base_filename = f"pikiran_{keyword_clean}_{timestamp}"
    
    exported_files = {}
    
    # Export articles
    if result_data['articles']:
        articles_df = pd.DataFrame(result_data['articles'])
        
        if 'csv' in formats:
            csv_path = os.path.join(output_dir, f"{base_filename}_articles.csv")
            articles_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
            exported_files['articles_csv'] = csv_path
            LOG.info(f"Artikel disimpan ke CSV: {csv_path}")
        
        if 'excel' in formats:
            excel_path = os.path.join(output_dir, f"{base_filename}_articles.xlsx")
            with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
                articles_df.to_excel(writer, sheet_name='Articles', index=False)
                
                # Tambah sheet summary
                summary_data = {
                    'Metric': [
                        'Keyword', 'Pages Searched', 'Total Articles',
                        'Date Range', 'Average Content Length', 'Unique Authors'
                    ],
                    'Value': [
                        result_data['keyword'],
                        result_data['pages_searched'],
                        len(result_data['articles']),
                        f"{articles_df['waktu_terbit'].min()} to {articles_df['waktu_terbit'].max()}" 
                        if not articles_df['waktu_terbit'].empty else 'N/A',
                        f"{articles_df['panjang_konten'].mean():.0f} chars" 
                        if not articles_df['panjang_konten'].empty else 'N/A',
                        articles_df['author'].nunique() if not articles_df['author'].empty else 0
                    ]
                }
                pd.DataFrame(summary_data).to_excel(writer, sheet_name='Summary', index=False)
            
            exported_files['articles_excel'] = excel_path
            LOG.info(f"Artikel disimpan ke Excel: {excel_path}")
    
    # Export search results
    if result_data['search_results']:
        search_df = pd.DataFrame(result_data['search_results'])
        search_path = os.path.join(output_dir, f"{base_filename}_search_results.csv")
        search_df.to_csv(search_path, index=False, encoding='utf-8-sig')
        exported_files['search_results'] = search_path
        LOG.info(f"Hasil pencarian disimpan: {search_path}")
    
    return exported_files

# =========================
# FUNGSI UTAMA CLI
# =========================
def main():
    """Fungsi utama untuk CLI."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Scraper Artikel Pikiran-Rakyat.com',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Contoh Penggunaan:
1. Pencarian dasar:
   python mbg_news_pr.py "mbg" --start-page 1 --end-page 3
   
2. Dengan output Excel:
   python mbg_news_pr.py "ekonomi" --output-dir ./results --format excel
   
3. Mode debug (browser terbuka):
   python mbg_news_pr.py "teknologi" --debug-mode
   
4. Hanya pencarian tanpa scraping detail:
   python mbg_news_pr.py "politik" --search-only
        """
    )
    
    parser.add_argument('keyword', help='Kata kunci pencarian (contoh: "mbg", "ekonomi", "politik")')
    parser.add_argument('--start-page', type=int, default=1, 
                       help='Halaman awal pencarian (default: 1)')
    parser.add_argument('--end-page', type=int, default=5,
                       help='Halaman akhir pencarian (default: 5)')
    parser.add_argument('--output-dir', default='data',
                       help='Direktori untuk menyimpan hasil (default: data)')
    parser.add_argument('--format', choices=['csv', 'excel', 'both'], default='both',
                       help='Format file output (default: both)')
    parser.add_argument('--debug-mode', action='store_true',
                       help='Mode debug - browser akan terlihat')
    parser.add_argument('--search-only', action='store_true',
                       help='Hanya melakukan pencarian, tidak scraping detail artikel')
    parser.add_argument('--max-articles', type=int, default=50,
                       help='Maksimal artikel yang akan di-scrape (default: 50)')
    
    args = parser.parse_args()
    
    # Tampilkan header
    print(f"""
{'='*60}
SCRAPER PIKIRAN-RAKYAT.COM
{'='*60}
Keyword       : {args.keyword}
Halaman       : {args.start_page} - {args.end_page}
Output Dir    : {args.output_dir}
Format        : {args.format}
Debug Mode    : {'Ya' if args.debug_mode else 'Tidak'}
Search Only   : {'Ya' if args.search_only else 'Tidak'}
Max Articles  : {args.max_articles}
{'='*60}
Memulai proses scraping...
    """)
    
    try:
        # Jalankan pencarian
        LOG.info(f"Memulai pencarian untuk keyword: '{args.keyword}'")
        
        result_data = search_pikiran_rakyat(
            keyword=args.keyword,
            start_page=args.start_page,
            end_page=args.end_page,
            debug_mode=args.debug_mode
        )
        
        # Jika hanya search only
        if args.search_only:
            print(f"\n🔍 HASIL PENCARIAN SAJA:")
            print(f"   Total ditemukan: {result_data['total_search_results']}")
            print(f"   URL unik: {result_data['total_unique_urls']}")
            
            if result_data['search_results']:
                print(f"\n📋 5 ARTIKEL TERATAS:")
                for i, r in enumerate(result_data['search_results'][:5], 1):
                    print(f"\n{i}. {r.get('title', 'No title')}")
                    print(f"   Kategori: {r.get('category', 'Tidak diketahui')}")
                    print(f"   Tanggal: {r.get('date_text', 'Tidak diketahui')}")
                    print(f"   URL: {r.get('url', 'No URL')[:80]}...")
        
        else:
            # Export results
            formats = []
            if args.format == 'both':
                formats = ['csv', 'excel']
            else:
                formats = [args.format]
            
            files = export_results(result_data, args.output_dir, formats)
            
            # Tampilkan summary
            print(f"\n{'='*60}")
            print("📊 HASIL AKHIR")
            print(f"{'='*60}")
            print(f"Keyword         : {result_data['keyword']}")
            print(f"Halaman dicari  : {result_data['pages_searched']}")
            print(f"Hasil pencarian : {result_data['total_search_results']}")
            print(f"URL unik        : {result_data['total_unique_urls']}")
            print(f"Artikel berhasil: {result_data['total_articles_scraped']}")
            print(f"Error          : {result_data['total_errors']}")
            print(f"Timestamp      : {result_data['timestamp']}")
            
            if files:
                print(f"\n💾 FILE DISIMPAN:")
                for key, path in files.items():
                    print(f"  • {key}: {path}")
            
            # Tampilkan contoh artikel
            if result_data['articles']:
                print(f"\n📋 CONTOH ARTIKEL (3 pertama):")
                for i, article in enumerate(result_data['articles'][:3], 1):
                    print(f"\n{i}. {article['judul'][:60]}...")
                    print(f"   📅 {article['waktu_terbit']}")
                    print(f"   ✍️  {article['author'] or 'Tidak diketahui'}")
                    print(f"   📝 {article['panjang_konten']} karakter")
                    print(f"   🔗 {article['url'][:80]}...")
        
        print(f"\n{'='*60}")
        print("✅ PROSES SELESAI!")
        print(f"{'='*60}")
        
    except KeyboardInterrupt:
        print(f"\n\n❌ Proses dihentikan oleh pengguna")
    except Exception as e:
        print(f"\n\n❌ ERROR: {e}")
        LOG.error(f"Error dalam eksekusi utama: {e}", exc_info=True)

# =========================
# FUNGSI TAMBAHAN UNTUK PENGGUNAAN PROGRAMMATIC
# =========================
def search_and_save(keyword: str, pages: int = 3, output_dir: str = "data"):
    """Fungsi sederhana untuk penggunaan programmatic."""
    print(f"Memulai pencarian untuk: {keyword}")
    
    results = search_pikiran_rakyat(
        keyword=keyword,
        start_page=1,
        end_page=pages
    )
    
    files = export_results(results, output_dir, ['csv', 'excel'])
    
    print(f"\nSelesai! {len(results['articles'])} artikel ditemukan.")
    print(f"File disimpan di: {output_dir}")
    
    return results

if __name__ == "__main__":
    # Untuk CLI, jalankan main()
    main()
    
    # Untuk penggunaan programmatic:
    # results = search_and_save("mbg", pages=2)
    # print(f"Ditemukan {len(results['articles'])} artikel")