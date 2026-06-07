import requests
from bs4 import BeautifulSoup

def test_netmeds_direct(name="Dolo 650"):
    # Normalize name to slug
    slug = name.lower().replace(' ', '-').replace('\'', '-').replace("\\", "-").replace(".", "-")
    slug = slug.replace("%", "")
    
    url = f"https://www.netmeds.com/prescriptions/{slug}"
    print(f"Fetching: {url}")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    }
    
    response = requests.get(url, headers=headers)
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        soup = BeautifulSoup(response.text, "lxml")
        
        # Finding Out MRP
        price_elem = soup.find("span", {"class": "final-price"})
        mrp = price_elem.get_text() if price_elem else "Not Found"
        print(f"Price: {mrp}")
        
        # Manufacturer
        manu_elem = soup.find("span", {"class": "drug-manu"})
        manu = manu_elem.get_text(strip=True) if manu_elem else "Not Found"
        print(f"Manufacturer: {manu}")

if __name__ == "__main__":
    test_netmeds_direct("Dolo 650 Tablet 15's")
    test_netmeds_direct("Dolo 650")
