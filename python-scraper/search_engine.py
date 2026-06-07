import asyncio
import logging
import re
import sys
import time
from typing import List, Dict

# Set Windows specific loop policy to avoid "I/O operation on closed pipe" errors
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Import individual scrapers
from scrapers.pharmeasy import PharmEasyScraper
from scrapers.amazon_pharmacy import AmazonPharmacyScraper
from scrapers.onemg_search import OneMgSearchScraper
from scrapers.apollo import ApolloScraper
from scrapers.netmeds_search import NetmedsSearchScraper
from scrapers.truemeds import TruemedsScraper
from scrapers.platinumrx import PlatinumRxScraper
from scrapers.medplus import MedplusScraper

logger = logging.getLogger(__name__)


def compute_relevance_score(query: str, result_name: str) -> float:
    """
    Score how well a result name matches the search query.
    Higher score = better match. Returns 0.0 to 1.0.
    
    Logic:
    1. Extract brand name and dosage from query (e.g. "Dolo" + "650")
    2. Check if brand name is present in result
    3. Check if dosage matches exactly
    4. Penalize wrong form (injection vs tablet when tablet expected)
    """
    query_lower = query.lower().strip()
    name_lower = result_name.lower().strip()
    
    score = 0.0
    
    # --- Step 1: Extract components from query ---
    # Split query into words
    query_words = query_lower.split()
    
    # Extract dosage pattern (e.g. "650", "500mg", "2.5mg")
    dosage_pattern = re.compile(r'(\d+\.?\d*)\s*(mg|ml|gm|mcg|g)?', re.IGNORECASE)
    
    query_dosages = dosage_pattern.findall(query_lower)
    result_dosages = dosage_pattern.findall(name_lower)
    
    # Extract brand name (non-dosage words)
    brand_words = [w for w in query_words if not re.match(r'^\d+\.?\d*(mg|ml|gm|mcg|g)?$', w, re.IGNORECASE)]
    
    # --- Step 2: Brand name match ---
    # The first word is usually the primary brand name and is critical.
    if brand_words:
        first_word = brand_words[0]
        if first_word not in name_lower:
            # Massive penalty if the primary brand name is missing entirely
            score -= 1.0
        else:
            score += 0.5
            
        for word in brand_words[1:]:
            if word in name_lower:
                score += 0.2  # Smaller boost for secondary words (plus, oral, orange, etc.)
            else:
                score -= 0.2  # Penalty for missing word
    
    # --- Step 3: Dosage match ---
    if query_dosages:
        query_dose_value = query_dosages[0][0]  # e.g. "650"
        
        # Check if exact dosage exists in result
        dose_found = False
        for rd_val, rd_unit in result_dosages:
            if rd_val == query_dose_value:
                score += 0.4  # Big boost for exact dosage match
                dose_found = True
                break
        
        if not dose_found:
            score -= 0.5  # Heavy penalty for wrong dosage
    
    # --- Step 4: Form mismatch penalty ---
    # If query doesn't say "injection", penalize injection results
    query_forms = {'tablet', 'tab', 'capsule', 'cap', 'syrup', 'injection', 'inj', 'drops', 'cream', 'gel', 'ointment', 'suspension', 'solution', 'powder', 'spray'}
    query_form = None
    result_form = None
    
    for form in query_forms:
        if form in query_lower:
            query_form = form
        if form in name_lower:
            result_form = form
    
    # If query has no explicit form, assume "tablet" (most common)
    tablet_forms = {'tablet', 'tab', 'strip'}
    injection_forms = {'injection', 'inj', 'vial'}
    
    if result_form in injection_forms and query_form not in injection_forms:
        score -= 0.6  # Heavy penalty for injection when not asked
    
    if result_form in {'syrup', 'drops', 'cream', 'gel', 'ointment', 'suspension', 'solution', 'powder', 'spray'} and query_form != result_form:
        score -= 0.3  # Penalty for wrong form
    
    return max(0.0, min(1.0, score))


def pick_best_per_platform(query: str, results: List[Dict]) -> List[Dict]:
    """
    From all results, pick the single best-matching result per platform.
    Uses relevance scoring to find the most accurate match.
    """
    # Score all results
    scored = []
    for r in results:
        score = compute_relevance_score(query, r['name'])
        scored.append((score, r))
    
    # Group by platform, pick highest-scored result per platform
    best_per_platform = {}
    for score, r in scored:
        platform = r['platform']
        if platform not in best_per_platform or score > best_per_platform[platform][0]:
            best_per_platform[platform] = (score, r)
    
    # Filter out results with very low relevance (< 0.2)
    filtered = []
    for platform, (score, r) in best_per_platform.items():
        if score >= 0.2:
            r['match_percentage'] = round(score * 100)
            filtered.append(r)
            logger.info(f"[{platform}] Best match: '{r['name']}' (score: {score:.2f})")
        else:
            logger.warning(f"[{platform}] Dropped '{r['name']}' — irrelevant (score: {score:.2f})")
    
    return filtered


class UnifiedSearchEngine:
    """
    Master controller that orchestrates searches across multiple pharmacy platforms.
    Now includes intelligent relevance scoring to filter irrelevant results.
    """
    
    def __init__(self, delay: float = 1.0):
        # Initialize scrapers
        self.scrapers = [
            PharmEasyScraper(delay=delay),
            AmazonPharmacyScraper(delay=delay),
            OneMgSearchScraper(delay=delay),
            ApolloScraper(delay=delay),
            NetmedsSearchScraper(delay=delay),
            TruemedsScraper(delay=delay),
            PlatinumRxScraper(delay=delay),
            MedplusScraper(delay=delay)
        ]

    async def _search_platform(self, scraper, query: str) -> List[Dict]:
        """Run search for a single platform asynchronously."""
        try:
            results = await scraper.search_async(query)
            logger.info(f"[{scraper.platform_name}] Found {len(results)} raw results.")
            return results
        except Exception as e:
            logger.error(f"[{scraper.platform_name}] Error: {e}")
            return []

    async def search_all_async(self, query: str) -> List[Dict]:
        """
        Search across all platforms concurrently.
        Returns ALL results (unfiltered) — caller decides how to filter.
        """
        tasks = [self._search_platform(scraper, query) for scraper in self.scrapers]
        
        logger.info(f"Starting concurrent search for '{query}' across {len(self.scrapers)} platforms...")
        results_lists = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Flatten results and filter out exceptions
        all_results = []
        for res in results_lists:
            if isinstance(res, list):
                all_results.extend(res)
            elif isinstance(res, Exception):
                logger.error(f"Platform search failed: {res}")

        # Stop all Playwright scrapers to prevent pipe errors
        stop_tasks = []
        for scraper in self.scrapers:
            if hasattr(scraper, 'stop'):
                stop_tasks.append(scraper.stop())
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)

        return all_results

    def search(self, query: str) -> List[Dict]:
        """Synchronous wrapper for searching all platforms."""
        try:
            return asyncio.run(self.search_all_async(query))
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []
            
    async def search_1mg_async(self, query: str) -> List[Dict]:
        """Asynchronous search specifically for 1mg."""
        onemg_scraper = next((s for s in self.scrapers if s.platform_name == '1mg'), None)
        if not onemg_scraper:
            return []
        results = await onemg_scraper.search_async(query)
        # We don't stop the scraper here, because it might be reused by the next async call
        return results

    def search_1mg(self, query: str) -> List[Dict]:
        """Synchronous search specifically for 1mg (used for salt validation)."""
        try:
            return asyncio.run(self.search_1mg_async(query))
        except Exception as e:
            logger.error(f"1mg search failed: {e}")
            return []
        
    def close(self):
        """Close all scraper sessions."""
        for scraper in self.scrapers:
            try:
                scraper.close()
            except:
                pass


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    query = "Dolo 650" if len(sys.argv) == 1 else sys.argv[1]
    
    engine = UnifiedSearchEngine()
    print(f"\n{'='*60}")
    print(f"Searching all platforms for: '{query}'")
    print(f"{'='*60}\n")
    
    try:
        raw_results = engine.search(query)
        # Apply smart filtering
        best_results = pick_best_per_platform(query, raw_results)
    finally:
        engine.close()
        time.sleep(0.5)
    
    print(f"\n{'='*60}")
    print(f"Total Relevant Results: {len(best_results)}")
    print(f"{'='*60}")
    
    # Sort by price
    best_results.sort(key=lambda x: x['sale_price'] if x['sale_price'] > 0 else 99999)
    
    for i, r in enumerate(best_results):
        name = r['name'].replace('\u20b9', 'Rs.').replace('₹', 'Rs.')
        pack = r.get('pack_size', '').replace('\u20b9', 'Rs.').replace('₹', 'Rs.')
        
        print(f"{i+1}. [{r['platform'].upper()}] {name}")
        print(f"   Price: Rs.{r['sale_price']} (MRP: Rs.{r['mrp']}) | Pack: {pack}")
        print(f"   URL: {r['url']}\n")
