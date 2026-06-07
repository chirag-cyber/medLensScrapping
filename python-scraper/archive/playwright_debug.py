import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        async def on_response(response):
            if "search" in response.url or "api" in response.url:
                print("Response:", response.url, response.status)
                
        page.on("response", on_response)
        print("Navigating...")
        await page.goto("https://www.1mg.com/search/all?name=dolo%20650", wait_until="networkidle", timeout=20000)
        await asyncio.sleep(2)
        await browser.close()

asyncio.run(run())
