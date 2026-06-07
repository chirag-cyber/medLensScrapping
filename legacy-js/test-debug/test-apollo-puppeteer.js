const puppeteer = require('puppeteer-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
puppeteer.use(StealthPlugin());

async function scrapeApolloSearch(query) {
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });
  const page = await browser.newPage();
  const searchUrl = `https://www.apollopharmacy.in/search-medicines/${encodeURIComponent(query)}`;
  
  console.log(`Navigating to ${searchUrl}`);
  await page.goto(searchUrl, { waitUntil: 'networkidle2', timeout: 30000 });
  
  // Wait for product cards to render
  try {
    await page.waitForSelector('a[href*="/medicine/"], a[href*="/otc/"]', { timeout: 10000 });
  } catch (e) {
    console.log("Timeout waiting for product links, might be no results.");
  }

  const links = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('a'))
      .map(a => a.href)
      .filter(href => href && (href.includes('/medicine/') || href.includes('/otc/')) && href.includes('apollopharmacy.in'));
  });

  await browser.close();
  
  const uniqueLinks = [...new Set(links)];
  console.log(`Found ${uniqueLinks.length} product links:`);
  uniqueLinks.slice(0, 10).forEach(l => console.log(l));
  return uniqueLinks;
}

scrapeApolloSearch('warfarin 5mg').catch(console.error);
