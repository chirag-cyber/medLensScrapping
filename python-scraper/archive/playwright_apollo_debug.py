import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0 Safari/537.36"
        )
        try:
            print("Navigating to Apollo...")
            await page.goto("https://www.apollopharmacy.in/search-medicines/dolo%20650", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(4)
            html = await page.content()
            with open("apollo_test.html", "w", encoding="utf-8") as f:
                f.write(html)
            print("Saved apollo_test.html")
        except Exception as e:
            print("Error:", e)
        finally:
            await browser.close()

asyncio.run(run())
