import requests
import json
from bs4 import BeautifulSoup

url = "https://www.1mg.com/search/all?name=dolo%20650"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0 Safari/537.36',
    'Sec-Fetch-Dest': 'document', 'Sec-Fetch-Mode': 'navigate', 'Upgrade-Insecure-Requests': '1'
}

r = requests.get(url, headers=headers)
soup = BeautifulSoup(r.text, 'lxml')

for script in soup.find_all('script'):
    if script.string and 'window.__INITIAL_STATE__' in script.string:
        start_idx = script.string.find('window.__INITIAL_STATE__ = {')
        if start_idx != -1:
            start_idx += len('window.__INITIAL_STATE__ = ')
            json_str = script.string[start_idx:]
            
            stack = []
            end_idx = -1
            for i, char in enumerate(json_str):
                if char == '{': stack.append('{')
                elif char == '}':
                    if stack: stack.pop()
                    if not stack:
                        end_idx = i
                        break
            
            if end_idx != -1:
                clean_json = json_str[:end_idx+1]
                try:
                    state = json.loads(clean_json)
                    print(json.dumps(state.get('searchReducer', {}), indent=2)[:1000])
                except Exception as e:
                    pass
        break
