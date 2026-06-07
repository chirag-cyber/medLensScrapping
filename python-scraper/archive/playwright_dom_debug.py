import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0 Safari/537.36"
        )
        await page.goto("https://www.1mg.com/search/all?name=dolo%20650", wait_until="networkidle", timeout=30000)
        html = await page.content()
        with open("1mg_test.html", "w", encoding="utf-8") as f:
            f.write(html)
        await browser.close()

asyncio.run(run())
