import asyncio
from scrapers.playwright_base import PlaywrightBaseScraper

async def test_netmeds():
    scraper = PlaywrightBaseScraper(headless=False)
    page = await scraper.get_page()
    
    # We will log all API requests to see how Netmeds searches internally
    page.on("request", lambda request: print(f"> {request.method} {request.url}"))
    page.on("response", lambda response: print(f"< {response.status} {response.url}"))
    
    await page.goto("https://www.netmeds.com/catalogsearch/result/Dolo%20650/all", wait_until="networkidle")
    await asyncio.sleep(5)
    await scraper.stop()

if __name__ == "__main__":
    asyncio.run(test_netmeds())
