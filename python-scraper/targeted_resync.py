import asyncio
import logging
import argparse
from typing import List, Dict
import sys
import re

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from pymongo import MongoClient
import os
from dotenv import load_dotenv

# Import scrapers
from scrapers.pharmeasy import PharmEasyScraper
from scrapers.amazon_pharmacy import AmazonPharmacyScraper
from scrapers.onemg_search import OneMgSearchScraper
from scrapers.apollo import ApolloScraper
from scrapers.netmeds_search import NetmedsSearchScraper
from scrapers.truemeds import TruemedsScraper
from scrapers.platinumrx import PlatinumRxScraper
from scrapers.medplus import MedplusScraper
from detail_scraper import ClinicalDetailScraper

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TargetedResync:
    def __init__(self):
        load_dotenv('a:/medlens/medSave-India/.env')
        self.db = MongoClient(os.getenv('MONGO_URL')).get_database('MEDSAVE')
        self.medicines_col = self.db.medicines
        self.prices_col = self.db.prices
        
        self.clinical_fetcher = ClinicalDetailScraper()
        self.onemg_scraper = OneMgSearchScraper()
        
        self.scrapers = [
            PharmEasyScraper(),
            OneMgSearchScraper(),
            ApolloScraper(),
            NetmedsSearchScraper(),
            TruemedsScraper(),
            PlatinumRxScraper(),
            MedplusScraper(),
            AmazonPharmacyScraper()
        ]

    def _normalize_salt(self, salt_string: str) -> str:
        """Extract primary salt name without dosages for fuzzy matching."""
        if not salt_string:
            return ""
        # Remove numbers, mg, ml, %, etc.
        clean = re.sub(r'\d+\.?\d*\s*(mg|ml|gm|mcg|g|%)', '', salt_string, flags=re.IGNORECASE)
        # Remove parentheses and split by common separators
        clean = clean.replace('(', '').replace(')', '').replace('+', ',').replace(' and ', ',')
        words = [w.strip().lower() for w in clean.split(',') if len(w.strip()) > 3]
        return " ".join(sorted(words))

    async def _fetch_salt_for_price(self, price_name: str) -> str:
        """Search 1mg for the given price name to find its salt composition."""
        results = await self.onemg_scraper.search_async(price_name)
        if not results:
            return ""
        
        top_result_url = results[0].get('url')
        if not top_result_url:
            return ""
            
        clinical = await asyncio.to_thread(self.clinical_fetcher.fetch_clinical_data, top_result_url)
        return clinical.get('composition', '')

    async def resync_medicine(self, medicine_name: str):
        query = medicine_name.lower().strip()
        logger.info(f"Targeted Resync started for: {query}")
        
        # 1. Fetch original medicine
        med = self.medicines_col.find_one({"name": query})
        if not med:
            logger.error(f"Medicine '{query}' not found in database. Please check exact name.")
            return
            
        med_id = med['_id']
        original_salt = med.get('salt', '')
        normalized_original_salt = self._normalize_salt(original_salt)
        logger.info(f"Original Salt: {original_salt} (Normalized: {normalized_original_salt})")
        
        if not normalized_original_salt:
            logger.warning("Original medicine has no salt defined! Relying entirely on text matching.")
        
        # 2. Scrape all platforms concurrently
        logger.info("Scraping all platforms...")
        tasks = [scraper.search_async(query) for scraper in self.scrapers]
        results_nested = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_results = []
        for i, res in enumerate(results_nested):
            platform = self.scrapers[i].platform_name
            if isinstance(res, Exception):
                logger.error(f"[{platform}] Error: {res}")
            elif res:
                all_results.extend(res)
                
        logger.info(f"Scraped {len(all_results)} total raw results.")
        
        # 3. Filter and Validate Results
        best_matches = {}
        
        for result in all_results:
            platform = result['platform']
            price_name = result['name'].lower()
            
            # All platforms must be validated against the original salt
            is_valid = False
            
            if normalized_original_salt:
                logger.info(f"[{platform}] Validating generic substitution: '{price_name}'")
                result_salt = await self._fetch_salt_for_price(price_name)
                norm_result_salt = self._normalize_salt(result_salt)
                
                if norm_result_salt and norm_result_salt == normalized_original_salt:
                    logger.info(f"  -> VALID: Salt matches ({norm_result_salt})")
                    is_valid = True
                else:
                    logger.warning(f"  -> INVALID: Salt mismatch (Got: {norm_result_salt})")
            else:
                # Without salt to compare, we must drop it to be safe
                logger.warning(f"[{platform}] Dropping '{price_name}' (no original salt to compare)")
                    
            if is_valid:
                # Keep the cheapest valid option per platform
                if platform not in best_matches or result['sale_price'] < best_matches[platform]['sale_price']:
                    best_matches[platform] = result

        valid_results = list(best_matches.values())
        logger.info(f"Found {len(valid_results)} validated, correct prices.")
        
        if not valid_results:
            logger.error("No valid prices found after filtering.")
            return

        # 4. Delete old prices and save new ones
        logger.info("Clearing old potentially bad prices from database...")
        self.prices_col.delete_many({"medicine_id": med_id})
        
        for med_price in valid_results:
            from datetime import datetime
            price_entry = {
                "medicine_id": med_id,
                "medicine_name": query,
                "platform": med_price['platform'].lower(),
                "name": med_price['name'].replace('\u20b9', 'Rs.').replace('₹', 'Rs.'),
                "url": med_price['url'],
                "mrp": med_price['mrp'],
                "sale_price": med_price['sale_price'],
                "price": med_price['sale_price'],
                "pack_size": med_price.get('pack_size', ''),
                "manufacturer": med_price.get('manufacturer', ''),
                "in_stock": med_price.get('in_stock', True),
                "discount_percent": med_price.get('discount_percent', 0.0),
                "updated_at": datetime.utcnow()
            }
            self.prices_col.insert_one(price_entry)
            logger.info(f"  Saved: [{med_price['platform']}] {price_entry['name']} - Rs.{price_entry['sale_price']}")
            
        logger.info(f"Targeted resync complete for '{query}'!")

    def close(self):
        """Close all scraper sessions to avoid Event loop errors."""
        for scraper in self.scrapers:
            try:
                if hasattr(scraper, 'close'):
                    scraper.close()
            except Exception as e:
                logger.error(f"Error closing {scraper.__class__.__name__}: {e}")
        try:
            if hasattr(self.onemg_scraper, 'close'):
                self.onemg_scraper.close()
        except:
            pass
        try:
            if hasattr(self.clinical_fetcher, 'close'):
                self.clinical_fetcher.close()
        except:
            pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Targeted Resync for specific medicines using Salt validation.")
    parser.add_argument("--medicines", type=str, required=False, help="Comma-separated list of medicine names")
    parser.add_argument("--file", type=str, required=False, help="Path to a text file containing medicine names (one per line)")
    args = parser.parse_args()
    
    meds = []
    if args.medicines:
        meds.extend([m.strip() for m in args.medicines.split(',') if m.strip()])
        
    if args.file:
        try:
            with open(args.file, 'r', encoding='utf-8') as f:
                meds.extend([line.strip() for line in f if line.strip()])
        except Exception as e:
            logger.error(f"Failed to read file {args.file}: {e}")
            sys.exit(1)
            
    if not meds:
        logger.error("Please provide medicines using either --medicines or --file.")
        sys.exit(1)
    
    async def run_all(medicines):
        syncer = TargetedResync()
        try:
            for m in medicines:
                try:
                    await syncer.resync_medicine(m)
                except Exception as e:
                    logger.error(f"Error processing {m}: {e}")
        finally:
            if hasattr(syncer, 'close'):
                syncer.close()
                
    asyncio.run(run_all(meds))
