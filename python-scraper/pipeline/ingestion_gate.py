"""
MedLens Scraper — Ingestion Gatekeeper & Safe Promoter
======================================================
Strict quality control and deduplication boundary.
Enforces:
- Hard Rule 5 (Canonical Promotion Gate): A staging candidate MUST NOT be promoted
  unless every required canonical identity field can be populated from validated data.
- Idempotent promotion and non-destructive canonical merges.
"""

import re
import logging
from datetime import datetime
from bson.objectid import ObjectId
from pymongo import WriteConcern

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from medicine_identity import (
    parse_identity,
    compute_resolution_signature,
    _salt_is_molecular,
    _normalize_salt_key,
    extract_formulation_modifier
)

logger = logging.getLogger(__name__)


def validate_staging_candidate(candidate: dict) -> tuple[bool, list[str], str]:
    """
    Validates a staging medicine candidate against structural and clinical data rules.
    Returns: (is_valid, rejection_reasons, resolution_signature)
    Enforces Hard Rule 5: all canonical fields must be derivable from valid data.
    """
    reasons = []
    
    raw_name = candidate.get("raw_name", "").strip()
    raw_salt = candidate.get("raw_salt", "").strip()
    raw_manufacturer = candidate.get("raw_manufacturer", "").strip()
    raw_form = candidate.get("raw_form", "").strip()
    raw_prices = candidate.get("raw_prices", [])

    # 1. Name validation
    if not raw_name or len(raw_name) < 3:
        reasons.append("CANONICAL_FIELD_MISSING: name too short or empty")
    elif re.search(r'\b(best\s*seller|flat\s*\d+%|off|pack\s*of|buy\s*\d+)\b', raw_name, re.IGNORECASE):
        reasons.append("MARKETING_GIBBERISH_IN_NAME")

    # 2. Molecular Salt validation (Hard Rule 5)
    normalized_salt = _normalize_salt_key(raw_salt)
    if not raw_salt or not normalized_salt:
        reasons.append("CANONICAL_FIELD_MISSING: salt is required")
    elif not _salt_is_molecular(normalized_salt):
        reasons.append("INVALID_MOLECULAR_SALT: composition appears to be prose/marketing text")

    # 3. Manufacturer validation (Hard Rule 5)
    if not raw_manufacturer or len(raw_manufacturer) < 2:
        reasons.append("CANONICAL_FIELD_MISSING: manufacturer is required")

    # 4. Identity & Form / Strength extraction
    ident = parse_identity(raw_name, raw_salt)
    dosage_form = ident.form or raw_form.lower().strip()
    if not dosage_form or dosage_form == "unspecified":
        reasons.append("CANONICAL_FIELD_MISSING: dosage_form cannot be identified")

    strength_nums = ident.strength_nums
    strength_str = "+".join(ident.strength) if ident.strength else ""
    if not strength_nums:
        reasons.append("CANONICAL_FIELD_MISSING: strength could not be parsed")

    # 5. Price sanity
    valid_prices = 0
    for p in raw_prices:
        mrp = float(p.get("mrp", 0.0))
        sale_price = float(p.get("sale_price", 0.0))
        if mrp > 0 and sale_price > 0 and sale_price <= mrp:
            valid_prices += 1
            
    if valid_prices == 0:
        reasons.append("PRICE_INVALID: Candidate must have at least one valid price > 0 with sale_price <= mrp")

    # 6. Resolution Signature computation
    resolution_signature = compute_resolution_signature(raw_name, raw_salt, dosage_form)
    
    is_valid = len(reasons) == 0
    return is_valid, reasons, resolution_signature


def promote_to_canonical(db, staging_id: ObjectId, dry_run: bool = False) -> tuple[bool, str, ObjectId | None]:
    """
    Promotes an approved staging candidate to the canonical collection.
    Guarantees idempotency: re-running does not create duplicates.
    Enforces Hard Rule 5: halts if validation fails.
    """
    candidate = db.staging_medicines.find_one({"_id": staging_id})
    if not candidate:
        return False, "STAGING_NOT_FOUND", None

    is_valid, reasons, resolution_signature = validate_staging_candidate(candidate)
    if not is_valid:
        db.staging_medicines.update_one(
            {"_id": staging_id},
            {
                "$set": {
                    "status": "REJECTED",
                    "rejection_reasons": reasons,
                    "resolution_signature": resolution_signature
                }
            }
        )
        return False, f"VALIDATION_FAILED: {'; '.join(reasons)}", None

    if dry_run:
        return True, "DRY_RUN_PASSED", None

    now = datetime.utcnow()
    ident = parse_identity(candidate["raw_name"], candidate["raw_salt"])
    dosage_form = ident.form or candidate.get("raw_form", "tablet").lower().strip()
    normalized_name = re.sub(r'[^a-z0-9 ]', '', candidate["raw_name"].lower()).strip()
    normalized_salt = _normalize_salt_key(candidate["raw_salt"])
    strength_str = "+".join(ident.strength) if ident.strength else "standard"

    # Check for existing canonical match by resolution_signature or alias
    existing_med = db.medicines.find_one({
        "$or": [
            {"resolution_signature": resolution_signature},
            {"known_signatures": resolution_signature}
        ]
    })

    wc_majority = WriteConcern(w="majority")

    if existing_med:
        canonical_id = existing_med["_id"]
        logger.info(f"Staging candidate matched existing canonical medicine {canonical_id} ({existing_med.get('name')}). Merging prices.")
        
        # Merge prices into prices collection
        for p in candidate.get("raw_prices", []):
            platform = candidate.get("source_platform", "1mg").lower()
            price_doc = {
                "medicine_id": canonical_id,
                "platform": platform,
                "mrp": float(p["mrp"]),
                "sale_price": float(p["sale_price"]),
                "price": float(p["sale_price"]),
                "url": candidate.get("source_url", ""),
                "in_stock": bool(p.get("in_stock", True)),
                "pack_size": p.get("pack_size", ""),
                "image_url": p.get("image_url", ""),
                "freshness_state": "Fresh",
                "scraped_at": now,
                "sync_run_id": "discovery_ingestion",
                "raw_scraped_title": candidate["raw_name"],
                "scraper_engine_version": "v2.5.0",
                "match_confidence": 1.0,
                "payload_hash": resolution_signature,
                "updated_at": now
            }
            db.prices.with_options(write_concern=wc_majority).update_one(
                {"medicine_id": canonical_id, "platform": platform},
                {"$set": price_doc},
                upsert=True
            )

        # Update staging document to APPROVED
        db.staging_medicines.update_one(
            {"_id": staging_id},
            {
                "$set": {
                    "status": "APPROVED",
                    "resolution_signature": resolution_signature,
                    "matched_canonical_id": canonical_id,
                    "promoted_at": now
                }
            }
        )
        return True, "MERGED_EXISTING", canonical_id

    else:
        # Create brand-new canonical medicine document
        canonical_doc = {
            "name": candidate["raw_name"],
            "normalized_name": normalized_name,
            "salt": candidate["raw_salt"],
            "normalized_salt": normalized_salt,
            "manufacturer": candidate["raw_manufacturer"],
            "dosage_form": dosage_form,
            "strength": strength_str,
            "pack_size": candidate.get("raw_prices", [{}])[0].get("pack_size", ""),
            "prescription_required": False,
            "resolution_signature": resolution_signature,
            "known_signatures": [resolution_signature],
            "canonical_id_override": None,
            "curation_status": "AUTOMATED",
            "created_at": now,
            "updated_at": now
        }
        res = db.medicines.with_options(write_concern=wc_majority).insert_one(canonical_doc)
        canonical_id = res.inserted_id
        logger.info(f"Promoted staging candidate to new canonical medicine {canonical_id} ({canonical_doc['name']}).")

        # Initialize corresponding operational sync state
        shard_id = int(str(canonical_id)[-4:], 16) % 4
        db.medicine_sync_state.with_options(write_concern=wc_majority).insert_one({
            "_id": canonical_id,
            "shard_id": shard_id,
            "sync_status": "READY",
            "sync_tier": "warm",
            "sync_run_id": None,
            "sync_started_at": None,
            "heartbeat_at": None,
            "last_synced_at": now,
            "last_sync_duration_ms": 0,
            "sync_failure_count": 0,
            "last_failure_reason": None,
            "platform_coverage": len(candidate.get("raw_prices", [])),
            "version": 1
        })

        # Insert prices
        for p in candidate.get("raw_prices", []):
            platform = candidate.get("source_platform", "1mg").lower()
            price_doc = {
                "medicine_id": canonical_id,
                "platform": platform,
                "mrp": float(p["mrp"]),
                "sale_price": float(p["sale_price"]),
                "price": float(p["sale_price"]),
                "url": candidate.get("source_url", ""),
                "in_stock": bool(p.get("in_stock", True)),
                "pack_size": p.get("pack_size", ""),
                "image_url": p.get("image_url", ""),
                "freshness_state": "Fresh",
                "scraped_at": now,
                "sync_run_id": "discovery_ingestion",
                "raw_scraped_title": candidate["raw_name"],
                "scraper_engine_version": "v2.5.0",
                "match_confidence": 1.0,
                "payload_hash": resolution_signature,
                "updated_at": now
            }
            db.prices.with_options(write_concern=wc_majority).update_one(
                {"medicine_id": canonical_id, "platform": platform},
                {"$set": price_doc},
                upsert=True
            )

        db.staging_medicines.update_one(
            {"_id": staging_id},
            {
                "$set": {
                    "status": "APPROVED",
                    "resolution_signature": resolution_signature,
                    "matched_canonical_id": canonical_id,
                    "promoted_at": now
                }
            }
        )
        return True, "PROMOTED_NEW", canonical_id
