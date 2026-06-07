import requests
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_truemeds_secret_api(medicine_name):
    url = "https://nal.tmmumbai.in/CustomerService/getSearchResult"
    querystring = {
        "warehouseId": "20",
        "elasticSearchType": "SKU_BRAND_SEARCH",
        "searchString": medicine_name,
        "isMultiSearch": "true",
        "pageName": "srp",
        "variantId": "18",
        "platform": "m_web"
    }
    headers = {
        "accept": "application/json, text/plain, */*",
        "origin": "https://www.truemeds.in",
        "referer": "https://www.truemeds.in/",
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_7_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.2 Mobile/15E148 Safari/604.1"
    }

    try:
        logger.info(f"Testing Truemeds Secret API for: {medicine_name}")
        response = requests.get(url, headers=headers, params=querystring, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            products = data.get("responseData", {}).get("elasticProductDetails", [])
            logger.info(f"Success! Found {len(products)} products in secret API.")
            if products:
                print(json.dumps(products[0], indent=2)[:1000] + "...")
            return True
        else:
            logger.error(f"Secret API Failed with status: {response.status_code}")
            print(response.text)
            return False
    except Exception as e:
        logger.error(f"Error: {e}")
        return False

if __name__ == "__main__":
    test_truemeds_secret_api("Dolo 650")
