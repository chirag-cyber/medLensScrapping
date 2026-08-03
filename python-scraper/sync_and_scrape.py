import os
import sys
import time
import json
import asyncio
import logging
import argparse
from datetime import datetime
import re
from pymongo import MongoClient
from bson.objectid import ObjectId
from dotenv import load_dotenv

from search_engine import UnifiedSearchEngine, pick_best_per_platform
from detail_scraper import ClinicalDetailScraper

# Define state file path
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sync-state-python.json")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
MONGO_URL = os.getenv("MONGO_URL")

class ScrapeAndSync:
    def __init__(self):
        if not MONGO_URL:
            logger.error("MONGO_URL not found in .env file.")
            sys.exit(1)
        
        self.client = MongoClient(MONGO_URL)
        self.db = self.client.get_database("MEDSAVE")
        self.medicines_col = self.db.get_collection("medicines")
        self.prices_col = self.db.get_collection("prices")
        
        self.engine = UnifiedSearchEngine(delay=1.0)
        self.clinical_fetcher = ClinicalDetailScraper(delay=0.5)

    def load_state(self):
        if not os.path.exists(STATE_FILE):
            return None
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading state file: {e}")
            return None

    def save_state(self, state):
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving state file: {e}")

    def _normalize_salt(self, salt_string: str) -> str:
        """Extract primary salt name without dosages for fuzzy matching."""
        if not salt_string:
            return ""
        # Remove numbers, mg, ml, gm, etc.
        clean = re.sub(r'\d+\.?\d*\s*(mg|ml|gm|mcg|g|%)', '', salt_string, flags=re.IGNORECASE)
        # Remove parentheses and split by common separators
        clean = clean.replace('(', '').replace(')', '').replace('+', ',').replace(' and ', ',')
        words = [w.strip().lower() for w in clean.split(',') if len(w.strip()) > 3]
        return " ".join(sorted(words))

    def _is_salt_match(self, salt1: str, salt2: str) -> bool:
        """Check if two normalized salt strings match using Jaccard similarity / subset logic."""
        if not salt1 or not salt2:
            return False
        w1 = set(salt1.split())
        w2 = set(salt2.split())
        # If one is a pure subset of the other, we consider it a match
        if w1.issubset(w2) or w2.issubset(w1):
            return True
            
        intersection = w1.intersection(w2)
        union = w1.union(w2)
        if not union:
            return False
            
        similarity = len(intersection) / len(union)
        return similarity >= 0.5  # 50% overlap is a good threshold for complex combinations

    async def _validate_matches(self, query: str, best_matches: list, ground_truth_salt: str, is_new: bool) -> list:
        """Filter matches by checking brand name similarity OR salt composition match."""
        valid_matches = []
        normalized_ground_truth = self._normalize_salt(ground_truth_salt)
        
        query_words = set(self._normalize_salt(query).split())
        
        for match in best_matches:
            platform = match['platform']
            price_name = match['name'].lower()
            
            # Fast-path: If the candidate's name heavily overlaps with our original query,
            # we assume it's a direct brand match and bypass the expensive 1mg salt lookup.
            cand_words = set(self._normalize_salt(price_name).split())
            if query_words and cand_words:
                overlap = len(query_words.intersection(cand_words)) / len(query_words)
                if overlap >= 0.75:
                    logger.info(f"[{platform}] Brand match bypass: '{price_name}' closely matches query '{query}'.")
                    valid_matches.append(match)
                    continue
            
            # If the candidate IS the 1mg result we just used for ground truth (only applies to new medicines)
            if is_new and platform == '1mg':
                valid_matches.append(match)
                continue
                
            if not ground_truth_salt:
                logger.warning(f"[{platform}] No ground truth salt available. Accepting '{price_name}' blindly.")
                valid_matches.append(match)
                continue
                
            logger.info(f"[{platform}] Validating generic substitution: '{price_name}'...")
            
            # Fetch candidates from 1mg to find its salt
            results_1mg = await self.engine.search_1mg_async(price_name)
            if not results_1mg:
                logger.warning(f"  -> INVALID: Could not fetch salt for {price_name} on 1mg.")
                continue
                
            # Pick the 1mg result that best matches the candidate's title
            best_1mg_result = results_1mg[0]
            from search_engine import compute_relevance_score
            best_score = 0
            for r in results_1mg:
                score = compute_relevance_score(price_name, r['name'].lower())
                if score > best_score:
                    best_score = score
                    best_1mg_result = r
                    
            candidate_1mg_url = best_1mg_result['url']
            candidate_clinical = await self.clinical_fetcher.fetch_clinical_data_async(onemg_url=candidate_1mg_url)
            candidate_salt = candidate_clinical.get('composition', '')
            normalized_candidate = self._normalize_salt(candidate_salt)
            
            if self._is_salt_match(normalized_candidate, normalized_ground_truth):
                valid_matches.append(match)
            else:
                logger.warning(f"  -> INVALID: Salt mismatch (Truth: {normalized_ground_truth} | Got: {normalized_candidate})")
                
        return valid_matches

    def reset_state(self):
        if os.path.exists(STATE_FILE):
            try:
                os.remove(STATE_FILE)
            except Exception as e:
                logger.error(f"Error removing state file: {e}")

    async def _process_medicine(self, query: str, existing_med_id=None, force_clinical=False):
        """Searches all platforms and updates MongoDB."""
        logger.info(f"--- Processing: '{query}' ---")
        
        # 1. Search across all platforms
        all_results = await self.engine.search_all_async(query)
        if not all_results:
            logger.warning(f"No results found for '{query}' on any platform.")
            return False

        # 2. Filter best matches
        best_matches = pick_best_per_platform(query, all_results)
        if not best_matches:
            logger.warning(f"No relevant results matched '{query}'.")
            return False

        # Sort by price
        best_matches.sort(key=lambda x: x['sale_price'] if x['sale_price'] > 0 else 99999)
        
        # 3. Fetch clinical data (if new medicine or forced)
        clinical_info = {}
        if not existing_med_id or force_clinical:
            onemg_url = next((m['url'] for m in best_matches if m['platform'] == '1mg'), None)
            netmeds_url = next((m['url'] for m in best_matches if m['platform'] == 'netmeds'), None)
            api_composition = next((m.get('composition') for m in best_matches if m['platform'] in ('truemeds', 'platinumrx') and m.get('composition')), None)
            
            logger.info("Fetching clinical data...")
            clinical_info = await self.clinical_fetcher.fetch_clinical_data_async(onemg_url=onemg_url, netmeds_url=netmeds_url)
            
            if not clinical_info.get('composition') and api_composition:
                clinical_info['composition'] = api_composition

        # 3.5 Validate matches using salt comparison
        existing_med_doc = None
        if existing_med_id:
            existing_med_doc = self.medicines_col.find_one({"_id": ObjectId(existing_med_id)})
            
        ground_truth_salt = clinical_info.get('composition', '') if not existing_med_id else (existing_med_doc.get('salt', '') if existing_med_doc else '')
        
        logger.info(f"Ground truth salt for validation: '{ground_truth_salt}'")
        best_matches = await self._validate_matches(query, best_matches, ground_truth_salt, is_new=not existing_med_id)
        
        if not best_matches:
            logger.warning(f"No valid matches passed salt validation for '{query}'.")
            return False

        # 4. Save to database
        med_id = existing_med_id
        
        # Insert/Update Medicine record
        if not existing_med_id:
            logger.info("Inserting new medicine record...")
            normalized_name = query.lower().strip()
            # Ensure safe structure for DB
            med_doc = {
                "name": query,
                "normalized_name": normalized_name,
                "primary_salt_key": clinical_info.get('composition', '').lower().replace(' ', '_') or normalized_name,
                "salt": clinical_info.get('composition', ''),
                "manufacturer": clinical_info.get('manufacturer', ''),
                "description": clinical_info.get('description', ''),
                "uses": clinical_info.get('uses', []),
                "side_effects": clinical_info.get('side_effects', []),
                "how_it_works": clinical_info.get('how_it_works', ''),
                "how_to_use": clinical_info.get('how_to_use', ''),
                "missed_dose": clinical_info.get('missed_dose', ''),
                "quick_tips": clinical_info.get('quick_tips', []),
                "safety_advice": clinical_info.get('safety_advice', {}),
                "fact_box": clinical_info.get('fact_box', {}),
                "prescription_status": clinical_info.get('prescription_status', ''),
                "administration_route": clinical_info.get('administration_route', ''),
                "dosage_form": clinical_info.get('dosage_form', ''),
                "faq": clinical_info.get('faqs', []),
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
            
            # Check if it already exists by normalized name
            existing = self.medicines_col.find_one({"normalized_name": normalized_name})
            if existing:
                med_id = existing['_id']
                # Update clinical fields if empty
                update_fields = {}
                for k, v in med_doc.items():
                    if k not in ['created_at', 'name', 'normalized_name'] and v and not existing.get(k):
                        update_fields[k] = v
                if update_fields:
                    update_fields['updated_at'] = datetime.utcnow()
                    self.medicines_col.update_one({"_id": med_id}, {"$set": update_fields})
            else:
                result = self.medicines_col.insert_one(med_doc)
                med_id = result.inserted_id
        else:
            # Updating existing medicine clinical data
            if force_clinical and clinical_info:
                logger.info("Updating existing clinical data...")
                update_fields = {
                    "updated_at": datetime.utcnow()
                }
                for key in ['salt', 'manufacturer', 'description', 'uses', 'side_effects', 
                            'how_it_works', 'how_to_use', 'missed_dose', 'quick_tips', 
                            'safety_advice', 'fact_box', 'prescription_status', 
                            'administration_route', 'dosage_form']:
                    if clinical_info.get(key):
                        update_fields[key] = clinical_info[key]
                
                if clinical_info.get('faqs'):
                    update_fields['faq'] = clinical_info['faqs']
                    
                self.medicines_col.update_one({"_id": med_id}, {"$set": update_fields})

        # 5. Update Prices collection
        logger.info(f"Saving {len(best_matches)} prices to database...")
        
        # CLEAR old prices for this medicine to ensure bad matches or missing platforms are correctly removed
        self.prices_col.delete_many({"medicine_id": med_id})
        
        for med in best_matches:
            price_entry = {
                "medicine_id": med_id,
                "medicine_name": query,
                "platform": med['platform'].lower(),
                "name": med['name'].replace('\u20b9', 'Rs.').replace('₹', 'Rs.'),
                "url": med['url'],
                "mrp": med['mrp'],
                "sale_price": med['sale_price'],
                "price": med['sale_price'],
                "pack_size": med.get('pack_size', ''),
                "manufacturer": med.get('manufacturer', ''),
                "in_stock": med.get('in_stock', True),
                "discount_percent": med.get('discount_percent', 0.0),
                "match_percentage": med.get('match_percentage', 0),
                "updated_at": datetime.utcnow()
            }
            
            # Insert price (since we just deleted old ones, we can just insert)
            self.prices_col.insert_one(price_entry)
            
        logger.info(f"Successfully scraped and synced '{query}'.\n")
        return True

    def scrape_new(self, query: str):
        """Scrape a single new medicine."""
        async def _run():
            try:
                return await self._process_medicine(query, existing_med_id=None, force_clinical=True)
            finally:
                await self.engine.shutdown_async()

        try:
            asyncio.run(_run())
        except Exception as e:
            logger.error(f"Error scraping {query}: {e}")

    def sync_all(self, skip: int = 0, update_clinical: bool = False, reset: bool = False):
        """Iterate through the database and update prices/clinical data for existing medicines, with checkpoints."""
        if reset:
            self.reset_state()

        state = self.load_state()
        should_resume = state is not None and not state.get("completed", False) and not reset

        if should_resume:
            logger.info("♻️ Automatically resuming incomplete sync cycle from checkpoint...")
            medicine_ids = state.get("medicine_ids", [])
            current_index = state.get("current_index", 0)
            logger.info(f"Resuming at index {current_index} of {len(medicine_ids)} medicines.")
        else:
            if state and state.get("completed", False):
                logger.info("♻️ Previous sync cycle was completed. Starting a new fresh crawl.")
            elif reset:
                logger.info("♻️ Reset requested. Starting a new fresh crawl.")
            else:
                logger.info("🆕 Starting fresh sync cycle.")
                
            logger.info(f"Querying all existing medicines from DB (skip={skip})...")
            cursor = self.medicines_col.find({}, {"_id": 1}).skip(skip)
            medicine_ids = [str(doc["_id"]) for doc in cursor]
            current_index = 0
            
            state = {
                "medicine_ids": medicine_ids,
                "current_index": 0,
                "completed": False,
                "skip": skip,
                "update_clinical": update_clinical
            }
            self.save_state(state)

        total_medicines = len(medicine_ids)
        logger.info(f"📊 {total_medicines - current_index} medicines remaining to sync in this cycle.")

        async def _run_loop():
            try:
                for i in range(current_index, total_medicines):
                    med_id_str = medicine_ids[i]
                    try:
                        med = self.medicines_col.find_one({"_id": ObjectId(med_id_str)})
                        if med:
                            med_name = med.get('name')
                            if med_name:
                                logger.info(f"Syncing [{i + 1}/{total_medicines}]: {med_name}")
                                await self._process_medicine(med_name, existing_med_id=med['_id'], force_clinical=update_clinical)
                                await asyncio.sleep(1) # Be polite to servers
                    except Exception as e:
                        logger.error(f"Error syncing medicine ID {med_id_str}: {e}")

                    state["current_index"] = i + 1
                    self.save_state(state)
            finally:
                # Print per-platform yield so a dead selector is obvious.
                logger.info("📈 Per-platform yield this run:\n%s", self.engine.yield_summary())
                # Tear down the shared Chromium exactly once, inside the live loop.
                try:
                    await self.engine.shutdown_async()
                except Exception as e:
                    logger.warning(f"Browser shutdown error (ignoring): {e}")

        asyncio.run(_run_loop())

        if state["current_index"] >= total_medicines:
            state["completed"] = True
            self.save_state(state)
            logger.info("🏁 Sync cycle completed successfully! Next run will start from the beginning.")

    def close(self):
        self.engine.close()
        self.clinical_fetcher.close()
        self.client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unified MedSave Scraper and Sync Tool")
    parser.add_argument("--scrape", type=str, help="Name of a new medicine to scrape and add to DB")
    parser.add_argument("--sync-all", action="store_true", help="Sync existing medicines in the DB")
    parser.add_argument("--update-clinical", action="store_true", help="When syncing, also force re-fetch of clinical data")
    parser.add_argument("--skip", type=int, default=0, help="Number of medicines to skip (for --sync-all)")
    parser.add_argument("--reset", action="store_true", help="Reset crawler state and start fresh")
    
    args = parser.parse_args()
    
    if not args.scrape and not args.sync_all:
        parser.print_help()
        sys.exit(1)
        
    scraper = ScrapeAndSync()
    try:
        if args.scrape:
            scraper.scrape_new(args.scrape)
        elif args.sync_all:
            scraper.sync_all(
                skip=args.skip,
                update_clinical=args.update_clinical,
                reset=args.reset
            )
    finally:
        scraper.close()
