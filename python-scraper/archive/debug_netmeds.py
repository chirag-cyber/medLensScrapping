import asyncio
from scrapers.playwright_base import PlaywrightBaseScraper

async def debug_netmeds():
    scraper = PlaywrightBaseScraper(headless=True)
    page = await scraper.get_page()
    await page.goto("https://www.netmeds.com/products?q=Dolo%20650")
    await asyncio.sleep(4)
    html = await page.content()
    open("netmeds_debug.html", "w", encoding="utf-8").write(html)
    print("Saved netmeds_debug.html")
    await scraper.stop()

if __name__ == "__main__":
    asyncio.run(debug_netmeds())
