const puppeteer = require("puppeteer");
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

(async () => {
  const browser = await puppeteer.launch({ headless: "new", args: ["--no-sandbox"] });
  const page = await browser.newPage();
  await page.setUserAgent(UA);
  await page.setViewport({ width: 1280, height: 800 });

  // Intercept the actual product search API call
  const productApiCalls = [];
  page.on("response", async (res) => {
    const url = res.url();
    if (url.includes("nal.tmmumbai.in") || url.includes("ProductService") || url.includes("searchProduct")) {
      try {
        const body = await res.text();
        productApiCalls.push({ url, method: res.request().method(), body });
      } catch {}
    }
  });

  await page.goto("https://www.truemeds.in/search?q=paracetamol", { waitUntil: "networkidle2", timeout: 45000 });
  await new Promise(r => setTimeout(r, 5000));
  
  // Scroll to trigger more content
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await new Promise(r => setTimeout(r, 3000));

  productApiCalls.forEach(c => {
  });

  // Try to extract product info from rendered DOM
  const products = await page.evaluate(() => {
    // Look for any element whose text looks like a product name with price
    const allText = document.body.innerText;
    // Find product-like cards by looking for elements containing "₹"
    const elements = [...document.querySelectorAll("*")].filter(el => {
      const text = el.innerText || "";
      return text.includes("₹") && text.length < 500 && text.length > 20 && el.children.length < 10;
    });
    
    return elements.slice(0, 10).map(el => ({
      tag: el.tagName,
      classes: el.className.substring(0, 100),
      text: el.innerText.substring(0, 200),
      href: el.closest("a")?.href || el.querySelector("a")?.href || null,
      onclick: el.getAttribute("onclick") || null,
    }));
  });

  products.forEach((p, i) => {
  });

  // Check the current URL in case it redirected

  await browser.close();
})().catch(() => {});
