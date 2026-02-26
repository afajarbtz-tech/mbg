import requests
from bs4 import BeautifulSoup
import urllib.parse
import time
import random
import csv
import pandas as pd
from datetime import datetime
import os

def random_sleep(base=25, variation=10):
    """Sleep dengan waktu random di sekitar base seconds"""
    sleep_time = base + random.uniform(-variation, variation)
    print(f"Sleeping for {sleep_time:.1f} seconds...")
    time.sleep(sleep_time)

def google_search_scrape(query, num_results=20):
    """Scrape hasil Google Search dengan random delay"""
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Referer': 'https://www.google.com/'
    }
    
    # Encode query
    query_encoded = urllib.parse.quote_plus(query)
    url = f"https://www.google.com/search?q={query_encoded}&num={num_results}"
    
    try:
        print(f"Searching for: {query}")
        print(f"URL: {url}")
        
        # Tambahkan random delay sebelum request
        random_sleep(15, 5)
        
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        results = []
        search_count = 0
        
        # Parse hasil pencarian
        for g in soup.find_all('div', class_='g'):
            # Extract link
            anchor = g.find('a')
            if not anchor or not anchor.get('href'):
                continue
                
            link = anchor['href']
            
            # Filter hanya link yang valid
            if link.startswith('http') and 'google.com' not in link and 'webcache' not in link:
                # Extract judul
                title_elem = g.find('h3')
                title = title_elem.text if title_elem else "No title"
                
                # Extract snippet
                snippet_elem = g.find('div', {'style': '-webkit-line-clamp:2'})
                if not snippet_elem:
                    snippet_elem = g.find('div', class_='VwiC3b')
                if not snippet_elem:
                    snippet_elem = g.find('span', class_='aCOpRe')
                    
                snippet = snippet_elem.text if snippet_elem else "No snippet"
                
                # Extract tanggal jika ada
                date_elem = g.find('span', class_='MUxGbd')
                date_published = date_elem.text if date_elem else ""
                
                results.append({
                    'title': title.strip(),
                    'link': link,
                    'snippet': snippet.strip(),
                    'date': date_published,
                    'query': query,
                    'scraped_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                })
                
                search_count += 1
                print(f"Found result {search_count}: {title[:50]}...")
                
                # Random delay antara hasil
                if search_count % 5 == 0:
                    random_sleep(8, 3)
        
        print(f"Total results found: {len(results)}")
        return results
        
    except requests.exceptions.RequestException as e:
        print(f"Request error: {e}")
        random_sleep(60, 15)  # Sleep lebih lama jika error
        return []
    except Exception as e:
        print(f"Error: {e}")
        return []

def save_to_csv(data, filename="google_search_results.csv"):
    """Simpan hasil ke file CSV"""
    
    if not data:
        print("No data to save!")
        return
    
    # Buat directory jika belum ada
    os.makedirs('results', exist_ok=True)
    
    # Tambahkan timestamp ke filename
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"results/google_search_{timestamp}.csv"
    
    try:
        # Gunakan pandas untuk simpan ke CSV
        df = pd.DataFrame(data)
        
        # Reorder columns
        columns_order = ['scraped_at', 'query', 'title', 'link', 'snippet', 'date']
        df = df[columns_order]
        
        # Save to CSV
        df.to_csv(filename, index=False, encoding='utf-8-sig')
        
        print(f"\nData saved successfully to: {filename}")
        print(f"Total records: {len(data)}")
        
        # Tampilkan preview
        print("\nPreview of saved data:")
        print(df[['title', 'link']].head())
        
        return filename
        
    except Exception as e:
        print(f"Error saving to CSV: {e}")
        
        # Fallback: gunakan csv module
        try:
            filename = f"results/google_search_{timestamp}_fallback.csv"
            with open(filename, 'w', newline='', encoding='utf-8-sig') as csvfile:
                fieldnames = data[0].keys()
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(data)
            print(f"Data saved using fallback method: {filename}")
            return filename
        except Exception as e2:
            print(f"Fallback also failed: {e2}")
            return None

def parse_article_content(url):
    """Parse konten dari artikel dengan random delay"""
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }
    
    try:
        print(f"\nParsing article: {url[:80]}...")
        
        # Random delay sebelum parsing artikel
        random_sleep(20, 8)
        
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract title
        title = ""
        for tag in ['h1', 'h2', 'title']:
            title_elem = soup.find(tag)
            if title_elem:
                title = title_elem.get_text(strip=True)
                if title and len(title) > 10:
                    break
        
        # Extract content - sesuaikan dengan struktur Pikiran Rakyat
        content = ""
        content_selectors = [
            'div.read__content',
            'article',
            'div.article-content',
            'div.post-content',
            'div.entry-content',
            'div.content'
        ]
        
        for selector in content_selectors:
            content_div = soup.select_one(selector)
            if content_div:
                # Hapus script dan style
                for script in content_div(["script", "style", "iframe", "nav", "footer"]):
                    script.decompose()
                
                # Ambil teks
                paragraphs = content_div.find_all('p')
                if paragraphs:
                    content = ' '.join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
                    break
        
        # Extract date
        date_published = ""
        date_selectors = [
            'time',
            'span.date',
            'div.date',
            'meta[property="article:published_time"]'
        ]
        
        for selector in date_selectors:
            if selector.startswith('meta'):
                date_elem = soup.select_one(selector)
                if date_elem and date_elem.get('content'):
                    date_published = date_elem['content']
                    break
            else:
                date_elem = soup.select_one(selector)
                if date_elem:
                    date_published = date_elem.get_text(strip=True)
                    break
        
        # Clean content
        if content:
            content = content[:2000]  # Batasi konten
            content = ' '.join(content.split())  # Normalize whitespace
        
        return {
            'article_title': title[:200] if title else "No title",
            'article_content': content[:1000] if content else "No content",
            'article_date': date_published[:50],
            'article_url': url,
            'parsed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
    except Exception as e:
        print(f"Error parsing article {url}: {e}")
        return {
            'article_title': "Error",
            'article_content': f"Error: {str(e)}",
            'article_date': "",
            'article_url': url,
            'parsed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

def main():
    """Fungsi utama"""
    
    print("=" * 70)
    print("GOOGLE SEARCH PARSER WITH RANDOM SLEEP")
    print("=" * 70)
    
    # Keyword pencarian
    keyword = "mbg site:https://www.pikiran-rakyat.com/"
    
    print(f"\nStarting search for: {keyword}")
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Langkah 1: Lakukan pencarian Google
    print("\n" + "=" * 50)
    print("STEP 1: Searching Google...")
    print("=" * 50)
    
    search_results = google_search_scrape(keyword, num_results=15)
    
    if not search_results:
        print("No search results found!")
        return
    
    # Langkah 2: Simpan hasil pencarian ke CSV
    print("\n" + "=" * 50)
    print("STEP 2: Saving search results to CSV...")
    print("=" * 50)
    
    search_csv_file = save_to_csv(search_results, "search_results.csv")
    
    # Langkah 3: Parse konten artikel (opsional)
    print("\n" + "=" * 50)
    print("STEP 3: Parsing article contents (optional)...")
    print("=" * 50)
    
    parse_articles = input("\nDo you want to parse article contents? (y/n): ").lower()
    
    if parse_articles == 'y':
        articles_data = []
        max_articles = min(5, len(search_results))  # Batasi ke 5 artikel
        
        print(f"\nWill parse {max_articles} articles...")
        
        for i, result in enumerate(search_results[:max_articles], 1):
            print(f"\n[{i}/{max_articles}] ", end="")
            article_content = parse_article_content(result['link'])
            articles_data.append(article_content)
        
        # Simpan hasil parsing artikel ke CSV terpisah
        if articles_data:
            articles_csv_file = save_to_csv(articles_data, "articles_content.csv")
    
    # Langkah 4: Tampilkan summary
    print("\n" + "=" * 50)
    print("PROCESS COMPLETED SUCCESSFULLY!")
    print("=" * 50)
    print(f"End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Total search results: {len(search_results)}")
    
    if 'articles_data' in locals():
        print(f"Total articles parsed: {len(articles_data)}")
    
    print(f"\nCSV files saved in 'results/' directory")
    print("\nData structure in CSV:")
    print("-" * 40)
    if search_results:
        for key in search_results[0].keys():
            print(f"  • {key}")
    
    print("\n" + "=" * 70)

if __name__ == "__main__":
    # Install required packages jika belum ada
    try:
        import pandas
    except ImportError:
        print("Installing required packages...")
        import subprocess
        subprocess.check_call(['pip', 'install', 'pandas', 'requests', 'beautifulsoup4'])
        print("Packages installed successfully!")
    
    # Jalankan program
    main()