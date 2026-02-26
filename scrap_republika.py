# mbg_scraper_cli.py
#!/usr/bin/env python3
"""
CLI untuk scraping MBG dari Republika
Usage: python mbg_scraper_cli.py --pages 1-5 --scrape-content
"""

import argparse
import sys
from datetime import datetime
from mbg_news_republika import scrape_republika_search, scrape_republika_batch
import pandas as pd

def parse_page_range(page_str):
    """Parse string seperti '1-5' menjadi (1, 5)"""
    if '-' in page_str:
        start, end = map(int, page_str.split('-'))
        return start, end
    elif ',' in page_str:
        # Multiple ranges: '1-3,5-7,10-12'
        ranges = []
        for part in page_str.split(','):
            if '-' in part:
                start, end = map(int, part.split('-'))
                ranges.append((start, end))
            else:
                page = int(part)
                ranges.append((page, page))
        return ranges
    else:
        page = int(page_str)
        return (page, page)

def main():
    parser = argparse.ArgumentParser(
        description='Scrape MBG articles from Republika.co.id',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --pages 1-3
  %(prog)s --pages 1-5 --scrape-content --output mbg_data.csv
  %(prog)s --pages "1-3,5-7,10-12" --workers 5
        """
    )
    
    parser.add_argument('--pages', type=str, default='1-3',
                       help='Page range (e.g., "1-5", "1-3,5-7")')
    parser.add_argument('--scrape-content', action='store_true',
                       help='Scrape full article content')
    parser.add_argument('--workers', type=int, default=3,
                       help='Number of parallel workers')
    parser.add_argument('--output', type=str, default=None,
                       help='Output filename (default: auto-generated)')
    parser.add_argument('--format', choices=['csv', 'excel', 'json'], 
                       default='csv', help='Output format')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Verbose output')
    
    args = parser.parse_args()
    
    print(f"""
╔══════════════════════════════════════════════════════╗
║          REPUBLIKA MBG ARTICLE SCRAPER               ║
╠══════════════════════════════════════════════════════╣
║ Keyword: MBG                                         ║
║ Pages: {args.pages:40} ║
║ Scrape content: {str(args.scrape_content):37} ║
║ Workers: {args.workers:39} ║
╚══════════════════════════════════════════════════════╝
    """)
    
    try:
        # Parse page range(s)
        page_ranges = parse_page_range(args.pages)
        
        all_articles = []
        
        # Handle single range
        if isinstance(page_ranges, tuple):
            start_page, end_page = page_ranges
            page_ranges = [(start_page, end_page)]
        
        # Process each page range
        for start_page, end_page in page_ranges:
            print(f"\n📖 Processing pages {start_page}-{end_page}")
            print(f"{'─' * 50}")
            
            # Search articles
            search_results, status = scrape_republika_search(
                keyword="mbg",
                startdate="2023-01-01",
                enddate=datetime.now().strftime("%Y-%m-%d"),
                max_pages=end_page
            )
            
            # Filter by page range
            filtered = [
                r for r in search_results 
                if start_page <= r.get('page', 0) <= end_page
            ]
            
            print(f"✅ Found {len(filtered)} articles")
            
            if args.scrape_content:
                # Scrape full content
                urls = [article['url'] for article in filtered]
                
                if urls:
                    print(f"📄 Scraping content for {len(urls)} articles...")
                    
                    articles, errors = scrape_republika_batch(
                        urls=urls,
                        max_workers=args.workers
                    )
                    
                    print(f"   ✅ Success: {len(articles)}")
                    print(f"   ❌ Errors: {len(errors)}")
                    
                    # Combine with search metadata
                    for article in articles:
                        search_match = next(
                            (s for s in filtered if s['url'] == article['url']), 
                            None
                        )
                        if search_match:
                            article['search_page'] = search_match.get('page')
                            article['search_date'] = search_match.get('date')
                            all_articles.append(article)
                else:
                    all_articles.extend(filtered)
            else:
                all_articles.extend(filtered)
        
        # Remove duplicates
        seen_urls = set()
        unique_articles = []
        for article in all_articles:
            url = article.get('url') or article.get('URL')
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_articles.append(article)
        
        print(f"\n{'='*50}")
        print(f"📊 FINAL RESULTS")
        print(f"{'='*50}")
        print(f"Total unique articles: {len(unique_articles)}")
        print(f"From page ranges: {args.pages}")
        
        if not unique_articles:
            print("\n❌ No articles to save. Exiting.")
            sys.exit(1)
        
        # Save results
        df = pd.DataFrame(unique_articles)
        
        if args.output:
            filename = args.output
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            page_str = args.pages.replace(',', '_').replace('-', 'to')
            filename = f"mbg_articles_{page_str}_{timestamp}.{args.format}"
        
        if args.format == 'csv':
            df.to_csv(filename, index=False, encoding='utf-8-sig')
        elif args.format == 'excel':
            df.to_excel(filename, index=False)
        elif args.format == 'json':
            df.to_json(filename, orient='records', indent=2, force_ascii=False)
        
        print(f"\n💾 Data saved to: {filename}")
        
        # Show preview
        if args.verbose:
            print(f"\n📋 PREVIEW (first 3 articles):")
            print(f"{'─' * 80}")
            for i, (_, row) in enumerate(df.head(3).iterrows(), 1):
                title = row.get('judul') or row.get('title') or 'No title'
                date = row.get('waktu_terbit') or row.get('date') or 'No date'
                print(f"{i}. {title[:60]}...")
                print(f"   📅 {date}")
                if 'panjang_konten' in row:
                    print(f"   📝 {row['panjang_konten']} chars")
                print()
        
        print(f"\n✅ Scraping completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()