import requests
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_platinumrx_api(medicine_name):
    url = "https://backend.platinumrx.in/pdp/fetchPlpInfo"
    payload = {
        "drugName": medicine_name,
        "searchType": None
    }
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://www.platinumrx.in",
        "referer": "https://www.platinumrx.in/"
    }

    try:
        logger.info(f"Testing PlatinumRx API for: {medicine_name}")
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            items = data.get("message", [])
            logger.info(f"Success! Found {len(items)} items.")
            if items:
                # Print first item structure
                print(json.dumps(items[0], indent=2)[:1000] + "...")
            return True
        else:
            logger.error(f"API Failed with status: {response.status_code}")
            print(response.text)
            return False
    except Exception as e:
        logger.error(f"Error: {e}")
        return False

if __name__ == "__main__":
    test_platinumrx_api("Dolo 650")
