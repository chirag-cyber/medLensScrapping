import os
import sys
import json
import time
import logging
from pymongo import MongoClient
from dotenv import load_dotenv
from groq import Groq

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv('a:/medlens/Scrapping/medLensScrapping/.env')

MONGO_URL = os.getenv('MONGO_URL')
GROQ_API_KEY = os.getenv('GROQ_API_KEY')

if not MONGO_URL or not GROQ_API_KEY:
    logger.error("Missing MONGO_URL or GROQ_API_KEY")
    sys.exit(1)

client = MongoClient(MONGO_URL)
db = client['MEDSAVE']
medicines = db['medicines']

groq_client = Groq(api_key=GROQ_API_KEY)

def fix_side_effects():
    # Target the medicines we filled with the placeholder
    query = {'side_effects': ['No common side effects reported']}
    total = medicines.count_documents(query)
    logger.info(f"Found {total} medicines to enrich via LLM")

    cursor = medicines.find(query)
    processed = 0
    updated = 0
    errors = 0

    for doc in cursor:
        processed += 1
        name = doc.get('name', '')
        salt = doc.get('salt', '')
        
        if not name and not salt:
            continue
            
        prompt = f"""
You are an expert pharmacist.
List the most common side effects for the medicine '{name}' (Composition/Salt: {salt}).
Return a JSON object with a single key "side_effects" containing an array of strings (e.g. {{"side_effects": ["Nausea", "Headache", "Stomach pain"]}}).
Keep the side effects concise (1-3 words each). Do NOT return any markdown, do NOT return any introductory text. JUST the JSON object.
If you do not know, return {{"side_effects": ["Unknown"]}}.
"""
        try:
            chat_completion = groq_client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": "You output JSON only."
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                model="llama-3.3-70b-versatile",
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            
            response = chat_completion.choices[0].message.content.strip()
            
            # Parse the JSON object
            parsed_json = json.loads(response)
            side_effects = parsed_json.get("side_effects", [])
            
            if isinstance(side_effects, list) and side_effects and side_effects[0] != "Unknown":
                # Clean up formatting just in case
                cleaned = [str(s).strip().capitalize() for s in side_effects if isinstance(s, str) and len(str(s).strip()) > 0]
                medicines.update_one({'_id': doc['_id']}, {'$set': {'side_effects': cleaned}})
                updated += 1
                logger.info(f"[{processed}/{total}] {name} - Updated: {cleaned}")
            else:
                logger.warning(f"[{processed}/{total}] {name} - Model returned unknown or invalid format")
                
        except Exception as e:
            errors += 1
            logger.error(f"[{processed}/{total}] {name} - Error: {str(e)}")
            
        # Respect rate limits (30 RPM = 2 seconds per request)
        time.sleep(2)
        
    logger.info(f"COMPLETED. Processed: {processed}, Updated: {updated}, Errors: {errors}")

if __name__ == "__main__":
    fix_side_effects()
