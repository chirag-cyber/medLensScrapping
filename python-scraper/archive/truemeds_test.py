import requests
import json
from bs4 import BeautifulSoup

url = "https://www.truemeds.in/search?q=dolo+650"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0 Safari/537.36'
}

r = requests.get(url, headers=headers)
print("Status:", r.status_code)
soup = BeautifulSoup(r.text, 'lxml')

nd = soup.find('script', id='__NEXT_DATA__')
if nd:
    try:
        data = json.loads(nd.string)
        # Find where products are
        print("Root keys:", list(data.keys()))
        props = data.get('props', {}).get('pageProps', {})
        print("PageProps keys:", list(props.keys()))
        
        # Truemeds uses Redux
        state = props.get('initialReduxState', {})
        print("Redux state keys:", list(state.keys()))
        
        search_res = state.get('searchResults', {}).get('results', [])
        if not search_res:
             search_res = state.get('searchParamsAndResult', {}).get('searchResults', [])
             
        # Look anywhere in state
        def find_products(obj, key_name='products'):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k == key_name: return v
                    res = find_products(v, key_name)
                    if res: return res
            elif isinstance(obj, list):
                for item in obj:
                    res = find_products(item, key_name)
                    if res: return res
            return None
            
        products = find_products(state, 'elasticProducts')
        if not products:
             products = find_products(state, 'productList')
        if not products:
             products = find_products(state, 'products')
             
        if products:
             print(f"Products found: {len(products)}")
             print(json.dumps(products[0], indent=2))
        else:
             print("Could not find products array.")
             print("State keys:", list(state.keys()))
    except Exception as e:
        print("Error parsing __NEXT_DATA__:", e)
else:
    print("No __NEXT_DATA__")
