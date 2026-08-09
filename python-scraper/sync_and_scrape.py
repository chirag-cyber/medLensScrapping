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
from medicine_identity import parse_identity, is_same_medicine, brand_prefix, _salt_is_molecular
from scrapers.pdp_fetch import PdpFetcher

# Define state file path
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sync-state-python.json")
# Moving checkpoint pointer, split OUT of STATE_FILE so the big frozen id list is
# not rewritten on every medicine (that was the O(N^2) disk churn). Only this tiny
# file is written during the crawl.
CURSOR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sync-cursor.json")
# How often to persist the cursor mid-crawl. The `finally` always writes the exact
# pointer; this bounds re-work to <= CHECKPOINT_EVERY items after a hard kill that
# skips `finally`.
CHECKPOINT_EVERY = 25

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
        # Append-only price snapshots over time. Separate from the existing
        # `history` collection (that is user browsing history) — this one records
        # a row whenever a platform's price changes, so trends survive the
        # delete-and-rewrite the live `prices` grid does each sync.
        self.history_col = self.db.get_collection("price_history")
        
        self.engine = UnifiedSearchEngine(delay=1.0)
        self.clinical_fetcher = ClinicalDetailScraper(delay=0.5)
        # Direct product-page fetcher — used ONLY to fill a platform that search
        # missed, against a URL already stored from a previous sync.
        self.pdp_fetcher = PdpFetcher(delay=0.5)

        # Drift alarms from the most recent sync_all() run, as returned by
        # UnifiedSearchEngine.drift_alarm(): list of (name, severity, message).
        # An unattended caller (scheduled_runner) reads this after sync_all() so a
        # platform that went DEAD (0 hits / all queries) can raise the process exit
        # code — otherwise a fully broken scraper hides behind the working ones and
        # the scheduled run still reports success.
        self.last_drift_alarms = []

    def ensure_indexes(self):
        """Idempotently create the price_history index. Opt-in (run via
        --ensure-indexes), NOT auto-run on every sync — index creation is a
        privileged op the user gates. The price write path does NOT depend on
        this index (the change-diff reads the live `prices` rows, not history),
        so history logging works even before this is ever run; the index just
        makes trend/time-range reads fast later."""
        idx = self.history_col.create_index(
            [("medicine_id", 1), ("scraped_at", -1)],
            name="med_time_idx",
        )
        logger.info(f"Ensured price_history index: {idx}")
        return idx

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

    def load_cursor(self):
        """Read the tiny moving pointer {current_index, completed}. Split from
        STATE_FILE so the frozen id list is not rewritten every iteration."""
        if not os.path.exists(CURSOR_FILE):
            return None
        try:
            with open(CURSOR_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading cursor file: {e}")
            return None

    def save_cursor(self, current_index: int, completed: bool = False):
        """Persist just the pointer — O(1) bytes, cheap to write often."""
        try:
            with open(CURSOR_FILE, "w", encoding="utf-8") as f:
                json.dump({"current_index": current_index, "completed": completed}, f)
        except Exception as e:
            logger.error(f"Error saving cursor file: {e}")

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

    def _sanitize_prices(self, matches: list) -> list:
        """Validate/normalize scraped prices before they are written to the DB.

        Guards the failure modes that visibly corrupt the frontend price grid:
          * sale_price <= 0   -> drop the row (a zero price is not a real offer).
          * mrp < sale_price  -> lift mrp up to sale_price (kills fake/negative
                                 discounts; also fills an absent/zero mrp).
          * one order-of-magnitude-inflated price (e.g. a parser regression that
            renders 100x the real value) -> drop it as a peer outlier.

        The peer-outlier guard uses a HIGH multiplier (25x) and needs >=3 peers,
        so ordinary pack-size spread (a 200ml bottle vs a strip of 10) is never
        dropped — only genuine order-of-magnitude parse errors are.
        Discount is recomputed here so it is always internally consistent.
        """
        clean = []
        for m in matches:
            try:
                sale = float(m.get('sale_price', 0) or 0)
                mrp = float(m.get('mrp', 0) or 0)
            except (TypeError, ValueError):
                logger.warning(
                    f"[{m.get('platform', '?')}] Dropping price with "
                    f"non-numeric value for {m.get('name', '?')!r}.")
                continue
            if sale <= 0:
                logger.warning(
                    f"[{m.get('platform', '?')}] Dropping non-positive "
                    f"sale_price ({sale}) for {m.get('name', '?')!r}.")
                continue
            if mrp < sale:
                mrp = sale  # no negative discount; also covers absent/zero mrp
            m['sale_price'] = sale
            m['mrp'] = mrp
            m['discount_percent'] = (
                round((mrp - sale) / mrp * 100, 2) if mrp > 0 else 0.0)
            clean.append(m)

        # Peer-outlier guard: only meaningful once several platforms priced the
        # same medicine (a stable median). Catches an order-of-magnitude mis-parse
        # on a single platform without touching legitimate pack-size variance.
        if len(clean) >= 3:
            sales = sorted(x['sale_price'] for x in clean)
            median = sales[len(sales) // 2]
            if median > 0:
                kept = []
                for m in clean:
                    if m['sale_price'] > median * 25:
                        logger.warning(
                            f"[{m['platform']}] Dropping absurd price "
                            f"{m['sale_price']} (peer median {median}) for "
                            f"{m['name']!r}.")
                    else:
                        kept.append(m)
                clean = kept
        return clean

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

            # Distinguish a FAILED lookup (empty candidate salt) from a real
            # MISMATCH (non-empty but different). An empty salt means the 1mg
            # composition fetch came back blank — a data gap, not evidence of
            # the wrong drug. Since this match already cleared relevance scoring
            # and is the best result for its platform, accept it rather than
            # silently dropping valid coverage (e.g. apollo's real Dolo-650).
            if not normalized_candidate:
                logger.warning(f"[{platform}] Salt lookup returned empty for '{price_name}' - accepting on relevance (lookup gap, not a mismatch).")
                valid_matches.append(match)
            elif self._is_salt_match(normalized_candidate, normalized_ground_truth):
                valid_matches.append(match)
            else:
                logger.warning(f"  -> INVALID: Salt mismatch (Truth: {normalized_ground_truth} | Got: {normalized_candidate})")
                
        return valid_matches

    def reset_state(self):
        for path in (STATE_FILE, CURSOR_FILE):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as e:
                    logger.error(f"Error removing state file {path}: {e}")

    # Platform inferred from URL host, so a seeded URL cannot be filed under the
    # wrong platform by a typo. Keys match each scraper's platform_name.
    _HOST_PLATFORM = {
        "1mg.com": "1mg", "tata1mg.com": "1mg",
        "pharmeasy.in": "pharmeasy",
        "netmeds.com": "netmeds",
        "truemeds.in": "truemeds",
        "apollopharmacy.in": "apollo",
        "medplusmart.com": "medplus",
        "platinumrx.in": "platinumrx",
        "amazon.in": "amazon_pharmacy",
    }

    def _platform_from_url(self, url: str) -> str:
        try:
            host = url.split("/")[2].lower().replace("www.", "")
        except IndexError:
            return ""
        for domain, platform in self._HOST_PLATFORM.items():
            if host.endswith(domain):
                return platform
        return ""

    def seed_urls(self, pairs: list, dry_run: bool = False):
        """Register known product URLs for medicines whose platform search fails.

        Each URL is FETCHED and relevance-checked against the medicine name before
        anything is stored, so a wrong or stale URL is rejected here rather than
        writing another product's price into this medicine's grid. Once stored,
        every later sync re-fetches that PDP automatically via _pdp_backfill.
        """
        from search_engine import compute_relevance_score

        for raw in pairs:
            if "=" not in raw:
                logger.error(f"Bad --seed-url (expected 'MEDICINE=URL'): {raw}")
                continue
            name, url = raw.split("=", 1)
            name, url = name.strip(), url.strip()
            platform = self._platform_from_url(url)
            if not platform:
                logger.error(f"Unrecognized pharmacy host, skipping: {url}")
                continue

            med = self.medicines_col.find_one(
                {"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}},
                {"_id": 1, "name": 1})
            if not med:
                logger.error(
                    f"'{name}' is not in the medicines collection — scrape it "
                    f"first (--scrape \"{name}\"), then seed its URLs.")
                continue

            row = self.pdp_fetcher.fetch(platform, url)
            if not row:
                logger.error(f"[{platform}] Could not parse a product from {url}")
                continue
            score = compute_relevance_score(name, row["name"])
            if score < 0.2:
                logger.error(
                    f"[{platform}] REJECTED: page is '{row['name']}' which does "
                    f"not match '{name}' (score {score:.2f}). Wrong URL?")
                continue

            logger.info(
                f"[{platform}] '{row['name']}' sale={row['sale_price']} "
                f"stock={row['in_stock']} score={score:.2f}")
            if dry_run:
                logger.info(f"  (dry-run) would store URL for {med['name']}")
                continue

            self.prices_col.update_one(
                {"medicine_id": med["_id"], "platform": platform},
                {"$set": {
                    "medicine_id": med["_id"],
                    "medicine_name": med["name"],
                    "platform": platform,
                    "name": row["name"],
                    "url": url,
                    "mrp": row["mrp"],
                    "sale_price": row["sale_price"],
                    "price": row["sale_price"],
                    "pack_size": row.get("pack_size", ""),
                    "manufacturer": row.get("manufacturer", ""),
                    "in_stock": row["in_stock"],
                    "discount_percent": row.get("discount_percent", 0.0),
                    "source": "pdp_seed",
                    "last_updated": datetime.utcnow(),
                }},
                upsert=True,
            )
            logger.info(f"  Seeded {platform} URL for '{med['name']}'.")

    def _known_platform_urls(self, med_id) -> dict:
        """platform -> product URL already stored for this medicine.

        These are last sync's rows, so every URL was a real, reachable PDP for
        THIS medicine. That makes them a safe seed for a direct re-fetch on a
        platform whose search has since stopped surfacing the brand.
        """
        if not med_id:
            return {}
        try:
            rows = self.prices_col.find(
                {"medicine_id": med_id}, {"platform": 1, "url": 1})
            return {
                (r.get("platform") or "").lower(): r["url"]
                for r in rows
                if r.get("url", "").startswith("http")
            }
        except Exception as e:
            logger.warning(f"Could not read stored URLs for backfill: {e}")
            return {}

    async def _pdp_backfill(self, query: str, best_matches: list,
                            existing_med_id=None) -> list:
        """Fill platforms search missed by re-fetching their known product page.

        Returns the merged, re-scored match list. Every PDP row passes through the
        SAME brand+dose relevance filter as search rows, so a stale URL that now
        points at a different product is rejected rather than written as this
        medicine's price.
        """
        covered = {m['platform'].lower() for m in best_matches}
        all_platforms = {s.platform_name.lower() for s in self.engine.scrapers}
        gaps = all_platforms - covered
        if not gaps:
            return best_matches

        known = self._known_platform_urls(existing_med_id)
        targets = {p: known[p] for p in gaps if p in known}
        if not targets:
            logger.info(
                f"[pdp] {len(gaps)} platform(s) missing for '{query}' and no "
                f"stored URL to re-fetch: {sorted(gaps)}")
            return best_matches

        logger.info(f"[pdp] Direct re-fetch for {sorted(targets)} on '{query}'")
        rows = await asyncio.gather(*[
            asyncio.to_thread(self.pdp_fetcher.fetch, platform, url)
            for platform, url in targets.items()
        ], return_exceptions=True)

        fetched = []
        for row in rows:
            if isinstance(row, dict):
                fetched.append(row)
            elif isinstance(row, Exception):
                logger.warning(f"[pdp] fetch failed: {row}")
        if not fetched:
            return best_matches

        merged = pick_best_per_platform(query, best_matches + fetched)
        gained = {m['platform'].lower() for m in merged} - covered
        if gained:
            logger.info(f"[pdp] Recovered platform(s) via direct PDP: {sorted(gained)}")
        return merged

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

        # 2b. Exact-brand second pass. A platform's own search often ranks a
        # low-visibility brand below same-salt substitutes for a multiword query
        # (e.g. "Pactol 650" returns Crocin/Calpol/Pacimol, never Pactol), so
        # that platform contributes zero relevant rows. Retry ONLY the platforms
        # that came back with nothing, using the bare brand ("Pactol"), which
        # ranks the exact product higher. The merged set is re-filtered by the
        # same brand+dose scorer, so no substitute can leak into the price grid.
        brand = brand_prefix(query)
        if brand and brand.lower() != query.lower().strip():
            covered = {m['platform'] for m in best_matches}
            missing = [s for s in self.engine.scrapers
                       if s.platform_name not in covered]
            if missing:
                retry_raw = await self.engine.search_selected_async(brand, missing)
                if retry_raw:
                    all_results = all_results + retry_raw
                    best_matches = pick_best_per_platform(query, all_results)

        # 2c. Direct-PDP third pass. Some platforms' search NEVER ranks a
        # low-visibility brand above its same-salt substitutes, even for the bare
        # brand — the product is listed and purchasable, but unreachable through
        # search (confirmed live for 6 of 8 platforms on "Pactol 650"). For those,
        # fetch the product page directly IF we already know its URL from a
        # previous sync. Product URLs carry unguessable ids, so this only ever
        # re-fetches a URL already stored — it never guesses one.
        best_matches = await self._pdp_backfill(query, best_matches, existing_med_id)

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

        # Garbage-truth guard: an existing record's stored `salt` (or a freshly
        # scraped composition) is sometimes webpage PROSE, e.g. "a combination of"
        # or "...composition consists of...". Trusting that as ground truth rejects
        # every genuinely-correct match (the reported budetrol bug: truth
        # 'a combination of' vs got 'budesonide formoterol'). Only trust a salt that
        # looks molecular; otherwise drop to empty and let _validate_matches accept
        # on relevance (these matches already cleared per-platform scoring).
        if ground_truth_salt and not _salt_is_molecular(self._normalize_salt(ground_truth_salt)):
            logger.warning(
                f"Stored/scraped ground-truth salt looks like prose, not a "
                f"molecule — ignoring for validation: {ground_truth_salt[:60]!r}")
            ground_truth_salt = ""

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
            salt_raw = clinical_info.get('composition', '')
            # normalized_salt is the dosage-stripped salt the live search groups
            # on (search_service groups by normalized_salt). Use the SAME helper
            # that produces the rest of the collection's values (e.g. Ibuprofen
            # (600mg) -> "ibuprofen") so new records group with existing ones.
            normalized_salt = self._normalize_salt(salt_raw)
            # Garbage-salt guard: scraped `composition` is sometimes webpage PROSE
            # ("...composition consists of paracetamol...manufactured by...") rather
            # than a molecular salt. Writing that as normalized_salt/primary_salt_key
            # creates a bogus one-off group that corrupts search grouping. Trust it
            # only when it looks molecular (1-5 tokens, no prose markers); otherwise
            # store empty salt keys and let identity/dedup resolve later.
            salt_is_clean = bool(normalized_salt) and _salt_is_molecular(normalized_salt)
            if salt_raw and not salt_is_clean:
                logger.warning(
                    f"Non-composition salt ignored for '{normalized_name}' "
                    f"(looks like prose, not a molecule): {salt_raw[:60]!r}")
                salt_raw = ""
                normalized_salt = ""
            primary_salt_key = (
                salt_raw.lower().replace(' ', '_') if salt_is_clean else normalized_name)
            # Ensure safe structure for DB
            med_doc = {
                # Store the lowercased form for consistency with the existing
                # collection (every other record's `name` is lowercased).
                "name": normalized_name,
                "normalized_name": normalized_name,
                "normalized_salt": normalized_salt,
                "primary_salt_key": primary_salt_key,
                "salt": salt_raw,
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
                # Entity-graph edges + storage: detail_scraper already extracts
                # these (substitutes/drug_interactions top-10, storage) — persist
                # them so alternatives/interaction content and future graph links
                # aren't discarded. Additive fields; existing readers ignore them.
                "substitutes": clinical_info.get('substitutes', []),
                "drug_interactions": clinical_info.get('drug_interactions', []),
                "storage": clinical_info.get('storage', ''),
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
            
            # Check if it already exists. Exact normalized_name first; then an
            # IDENTITY-BASED match so the same product written with trivial
            # surface differences ("brucare 600mg" vs "brucare 600mg tablets"
            # vs "Brucare-600 Tab") updates the existing record instead of
            # inserting a duplicate. We fetch candidates cheaply by brand prefix
            # (indexed-friendly anchored regex on normalized_name) and then
            # confirm each with the structured resolver, which matches on
            # (brand, dose, form) and uses salt as a guard so unrelated drugs
            # (Mucare vs Brucare, 400 vs 600, wrong salt) can never collide.
            existing = self.medicines_col.find_one({"normalized_name": normalized_name})
            if not existing:
                new_ident = parse_identity(normalized_name, salt_raw)
                prefix = brand_prefix(normalized_name)
                if prefix:
                    anchored = f"^{re.escape(prefix)}"
                    for cand in self.medicines_col.find(
                            {"normalized_name": {"$regex": anchored, "$options": "i"}}):
                        cand_ident = parse_identity(
                            cand.get("normalized_name") or cand.get("name") or "",
                            cand.get("salt", ""))
                        if is_same_medicine(new_ident, cand_ident):
                            existing = cand
                            logger.info(
                                f"Matched existing record "
                                f"'{cand.get('normalized_name')}' for "
                                f"'{normalized_name}' (identity: {new_ident.key}) "
                                f"— updating, not inserting.")
                            break
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
                            'administration_route', 'dosage_form',
                            'substitutes', 'drug_interactions', 'storage']:
                    if not clinical_info.get(key):
                        continue
                    # Never let a prose composition overwrite a clean stored salt.
                    if key == 'salt' and not _salt_is_molecular(
                            self._normalize_salt(clinical_info[key])):
                        logger.warning(
                            f"Skipping prose salt on clinical update: "
                            f"{str(clinical_info[key])[:60]!r}")
                        continue
                    update_fields[key] = clinical_info[key]
                
                if clinical_info.get('faqs'):
                    update_fields['faq'] = clinical_info['faqs']
                    
                self.medicines_col.update_one({"_id": med_id}, {"$set": update_fields})

        # 5. Update Prices collection
        priced = self._sanitize_prices(best_matches)
        if not priced:
            # A scrape that yields NO sane prices must not wipe good prices that
            # are already stored — keep them and skip the rewrite. Otherwise one
            # bad scrape (all zero / all rejected) would blank a medicine's whole
            # price grid on the frontend.
            logger.warning(
                f"No sane prices for '{query}' — keeping existing prices, "
                f"skipping price rewrite.")
            logger.info(f"Successfully scraped and synced '{query}'.\n")
            return True

        logger.info(f"Saving {len(priced)} prices to database...")

        # Snapshot the CURRENT live rows before we overwrite them. These rows are
        # last sync's prices, so they are the correct baseline for "did the price
        # change?" — no separate history read needed.
        old_rows = {
            r.get("platform"): r
            for r in self.prices_col.find(
                {"medicine_id": med_id},
                {"platform": 1, "sale_price": 1, "mrp": 1},
            )
        }

        # Build the full replacement rows up front so the write is one bulk op,
        # not N round trips (removes the crash-mid-loop zero-price window).
        now = datetime.utcnow()
        price_entries = []
        for med in priced:
            price_entries.append({
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
                "updated_at": now
            })

        # Append-only history: one row per platform whose price actually moved
        # (or is newly listed) since last sync. Unchanged platforms add nothing,
        # so growth is bounded by real price movement, not run frequency.
        history_rows = self._history_rows_for(med_id, query, price_entries, old_rows, now)

        self._write_prices_atomic(med_id, price_entries, history_rows)

        if history_rows:
            logger.info(
                f"Recorded {len(history_rows)} price change(s) to history for '{query}'.")
        logger.info(f"Successfully scraped and synced '{query}'.\n")
        return True

    @staticmethod
    def _history_rows_for(med_id, query, price_entries, old_rows, scraped_at):
        """Pure diff: return a price_history doc for each priced row whose platform
        is newly listed OR whose sale_price/mrp differs from the last stored value.

        Unchanged platforms yield nothing, so the history collection grows only
        with genuine price movement. Kept pure (no I/O) so it is unit-testable.
        """
        rows = []
        for entry in price_entries:
            platform = entry["platform"]
            prev = old_rows.get(platform)
            changed = (
                prev is None
                or prev.get("sale_price") != entry["sale_price"]
                or prev.get("mrp") != entry["mrp"]
            )
            if not changed:
                continue
            rows.append({
                "medicine_id": med_id,
                "medicine_name": query,
                "platform": platform,
                "sale_price": entry["sale_price"],
                "mrp": entry["mrp"],
                "in_stock": entry.get("in_stock", True),
                "scraped_at": scraped_at,
            })
        return rows

    def _write_prices_atomic(self, med_id, price_entries, history_rows):
        """Replace the live price grid and append history as ONE atomic unit.

        delete(prices) + insert(prices) + insert(price_history) run inside a
        transaction so a crash can never leave a medicine with zero/partial
        prices (the old delete-then-N-inserts left exactly that window). Falls
        back to a best-effort non-transactional path if the deployment can't do
        transactions (e.g. a standalone mongod in local dev)."""
        def _ops(session=None):
            self.prices_col.delete_many({"medicine_id": med_id}, session=session)
            if price_entries:
                self.prices_col.insert_many(price_entries, session=session)
            if history_rows:
                self.history_col.insert_many(history_rows, session=session)

        try:
            with self.client.start_session() as session:
                session.with_transaction(lambda s: _ops(session=s))
        except Exception as e:
            # Standalone servers raise on start_session/with_transaction. Degrade
            # to build-list -> delete -> bulk insert: still one delete + one bulk
            # insert (not N), so the exposure window is a single round trip, not
            # a per-row loop.
            logger.warning(
                f"Transactional price write unavailable ({e}); "
                f"falling back to non-transactional bulk write.")
            _ops(session=None)

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
        cursor_state = self.load_cursor()

        # The moving pointer now lives in CURSOR_FILE. Fall back to a legacy
        # current_index still embedded in STATE_FILE so an in-flight cycle from
        # the old format resumes cleanly on first run of this version.
        if cursor_state is not None:
            saved_index = cursor_state.get("current_index", 0)
            saved_completed = cursor_state.get("completed", False)
        elif state is not None:
            saved_index = state.get("current_index", 0)
            saved_completed = state.get("completed", False)
        else:
            saved_index = 0
            saved_completed = False

        should_resume = (
            state is not None
            and state.get("medicine_ids")
            and not saved_completed
            and not reset
        )

        if should_resume:
            logger.info("♻️ Automatically resuming incomplete sync cycle from checkpoint...")
            medicine_ids = state.get("medicine_ids", [])
            current_index = saved_index
            logger.info(f"Resuming at index {current_index} of {len(medicine_ids)} medicines.")
        else:
            if saved_completed:
                logger.info("♻️ Previous sync cycle was completed. Starting a new fresh crawl.")
            elif reset:
                logger.info("♻️ Reset requested. Starting a new fresh crawl.")
            else:
                logger.info("🆕 Starting fresh sync cycle.")

            logger.info(f"Querying all existing medicines from DB (skip={skip})...")
            cursor = self.medicines_col.find({}, {"_id": 1}).skip(skip)
            medicine_ids = [str(doc["_id"]) for doc in cursor]
            current_index = 0

            # Frozen id list — written ONCE here, never rewritten during the crawl.
            state = {
                "medicine_ids": medicine_ids,
                "completed": False,
                "skip": skip,
                "update_clinical": update_clinical
            }
            self.save_state(state)
            self.save_cursor(0)

        total_medicines = len(medicine_ids)
        logger.info(f"📊 {total_medicines - current_index} medicines remaining to sync in this cycle.")

        # Batch-fetch names for the remaining slice in ONE query, instead of a
        # find_one per medicine inside the loop. Names are small; hold them in
        # memory keyed by id string. State on disk still stores ids only.
        id_to_name = {}
        remaining_ids = medicine_ids[current_index:]
        if remaining_ids:
            object_ids = []
            for id_str in remaining_ids:
                try:
                    object_ids.append(ObjectId(id_str))
                except Exception as e:
                    logger.warning(f"Skipping malformed medicine id {id_str!r}: {e}")
            if object_ids:
                for doc in self.medicines_col.find(
                    {"_id": {"$in": object_ids}}, {"name": 1}
                ):
                    id_to_name[str(doc["_id"])] = doc.get("name")

        async def _run_loop():
            last_index = current_index
            try:
                for i in range(current_index, total_medicines):
                    med_id_str = medicine_ids[i]
                    try:
                        med_name = id_to_name.get(med_id_str)
                        if med_name:
                            logger.info(f"Syncing [{i + 1}/{total_medicines}]: {med_name}")
                            await self._process_medicine(
                                med_name,
                                existing_med_id=ObjectId(med_id_str),
                                force_clinical=update_clinical,
                            )
                            await asyncio.sleep(1)  # Be polite to servers
                        else:
                            # Doc vanished (deleted between cycle start and now) or
                            # has no name — nothing to sync, just advance.
                            logger.debug(f"No name for medicine id {med_id_str}; skipping.")
                    except Exception as e:
                        logger.error(f"Error syncing medicine ID {med_id_str}: {e}")

                    last_index = i + 1
                    # Cheap O(1) checkpoint every N items — not the whole id list.
                    if last_index % CHECKPOINT_EVERY == 0:
                        self.save_cursor(last_index)
            finally:
                # Always persist the exact pointer, even on crash/interrupt.
                self.save_cursor(last_index, completed=last_index >= total_medicines)
                # Print per-platform yield so a dead selector is obvious.
                logger.info("📈 Per-platform yield this run:\n%s", self.engine.yield_summary())
                # Escalate a whole-run zero-yield platform to a loud alarm so a
                # broken scraper cannot hide behind the others still working.
                self.last_drift_alarms = self.engine.drift_alarm()
                for name, severity, msg in self.last_drift_alarms:
                    if severity == "DEAD":
                        logger.error("🚨 SELECTOR DRIFT: %s", msg)
                    else:
                        logger.warning("⚠️ %s", msg)
                # Tear down the shared Chromium exactly once, inside the live loop.
                try:
                    await self.engine.shutdown_async()
                except Exception as e:
                    logger.warning(f"Browser shutdown error (ignoring): {e}")

        asyncio.run(_run_loop())

        final_cursor = self.load_cursor() or {}
        if final_cursor.get("current_index", 0) >= total_medicines:
            self.save_cursor(total_medicines, completed=True)
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
    parser.add_argument("--ensure-indexes", action="store_true",
                        help="Idempotently create the price_history index, then exit (safe to re-run)")
    parser.add_argument("--seed-url", action="append", metavar="MEDICINE=URL",
                        help="Seed a known product URL so future syncs can fetch "
                             "that platform directly when its search cannot find "
                             "the brand. Repeatable. Platform is inferred from the "
                             "URL host. Example: --seed-url 'Pactol 650=https://www.netmeds.com/product/...'")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --seed-url: validate and print what WOULD be "
                             "written, without touching the database")

    args = parser.parse_args()

    if args.seed_url:
        scraper = ScrapeAndSync()
        try:
            scraper.seed_urls(args.seed_url, dry_run=args.dry_run)
        finally:
            scraper.close()
        sys.exit(0)

    if not args.scrape and not args.sync_all and not args.ensure_indexes:
        parser.print_help()
        sys.exit(1)

    scraper = ScrapeAndSync()
    try:
        if args.ensure_indexes:
            scraper.ensure_indexes()
        elif args.scrape:
            scraper.scrape_new(args.scrape)
        elif args.sync_all:
            scraper.sync_all(
                skip=args.skip,
                update_clinical=args.update_clinical,
                reset=args.reset
            )
    finally:
        scraper.close()
