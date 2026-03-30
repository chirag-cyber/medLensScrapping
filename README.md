# 🔍 PDP Link Scraper

A Node.js web scraper that extracts **Product Detail Page (PDP)** links from any website URL. Pass a URL, get back a clean JSON list of all product page links found. Built with medical/pharma e-commerce sites in mind — **Apollo Pharmacy**, **PharmEasy**, **Netmeds**, and more.

## ✨ Features

- **Dual-mode scraping** — fast HTTP fetch (Axios) + headless browser (Puppeteer) for JS-rendered pages
- **Smart auto mode** — tries fast fetch first, falls back to browser if no results found
- **Medical site patterns** — built-in heuristics for `/otc/`, `/medicine/`, `/drugs/`, `/health-care/` and other pharma URL patterns
- **Two interfaces** — CLI tool for terminal usage, Express API server for programmatic access
- **Same-domain filtering** — excludes external links by default
- **Noise filtering** — ignores cart, login, blog, FAQ, and other non-product pages

---

## 📦 Installation

```bash
git clone <repo-url>
cd scrapper
npm install
```

> **Note:** Puppeteer will download a Chromium binary (~170 MB) on first install.

---

## 🚀 Usage

### CLI Mode

```bash
# Basic usage
node cli.js "https://www.apollopharmacy.in/shop-by-category"

# Force headless browser (for JS-rendered pages)
node cli.js "https://www.apollopharmacy.in/shop-by-category" --browser

# Fast mode only (Axios, no browser fallback)
node cli.js "https://pharmeasy.in/online-medicine-order" --mode fast

# Include cross-domain links
node cli.js "https://www.netmeds.com/non-prescriptions" --all-domains

# Compact JSON (no pretty-print)
node cli.js "https://www.1mg.com/categories/all-medicines" --compact

# Custom timeout
node cli.js "https://www.apollopharmacy.in/" --timeout 30000
```

### API Server Mode

```bash
# Start the server (default port 3000)
npm start

# Or with custom port
PORT=8080 npm start
```

#### Endpoints

| Method | Path      | Description              |
|--------|-----------|--------------------------|
| `GET`  | `/`       | Health check / API info  |
| `GET`  | `/scrape` | Scrape PDP links from URL |

#### Query Parameters

| Param        | Type    | Default | Description                          |
|--------------|---------|---------|--------------------------------------|
| `url`        | string  | —       | **(required)** Target URL to scrape  |
| `mode`       | string  | `auto`  | `auto` \| `fast` \| `browser`       |
| `sameDomain` | boolean | `true`  | Only return links on same domain     |
| `timeout`    | number  | `15000` | Request timeout in milliseconds      |

#### Example Request

```
GET http://localhost:3000/scrape?url=https://www.apollopharmacy.in/shop-by-category&mode=auto
```

---

## 📤 Output Format

Both CLI and API return the same JSON structure:

```json
{
  "success": true,
  "source": "https://www.apollopharmacy.in/shop-by-category",
  "mode": "fast",
  "scrapedAt": "2026-03-30T15:00:00.000Z",
  "totalLinksFound": 142,
  "pdpLinksFound": 87,
  "pdpLinks": [
    {
      "url": "https://www.apollopharmacy.in/otc/apollo-pharmacy-paracetamol-500mg",
      "path": "/otc/apollo-pharmacy-paracetamol-500mg"
    }
  ]
}
```

---

## ⚙️ Scraping Modes

| Mode      | Engine    | Speed   | JS Support | Best For                          |
|-----------|-----------|---------|------------|-----------------------------------|
| `fast`    | Axios     | ⚡ Fast | ❌ No       | Server-rendered pages (Netmeds)   |
| `browser` | Puppeteer | 🐢 Slow | ✅ Yes      | JS-rendered SPAs (Apollo, 1mg)    |
| `auto`    | Both      | Varies  | Fallback   | Unknown sites — tries fast first  |

---

## 🏥 Supported Medical Site URL Patterns

The scraper recognises these pharma/medical URL patterns as PDP links:

| Pattern                         | Example Sites              |
|---------------------------------|----------------------------|
| `/otc/<product-slug>`           | Apollo Pharmacy            |
| `/online-medicine-order/<slug>` | PharmEasy                  |
| `/medicine/<slug>`              | Netmeds, 1mg               |
| `/drugs/<slug>`                 | Generic pharma sites       |
| `/health-care/<slug>`           | Apollo, PharmEasy          |
| `/non-prescriptions/<slug>`     | Netmeds                    |
| `/prescriptions/<slug>`         | Netmeds                    |
| `/wellness/<slug>`              | 1mg, Apollo                |
| `/products/<slug>`              | Generic e-commerce         |
| `/p/<slug>`, `/dp/<slug>`       | Flipkart, Amazon           |

---

## 🗂 Project Structure

```
scrapper/
├── scraper.js      # Core scraping engine (Axios + Puppeteer + Cheerio)
├── cli.js          # CLI entry point
├── server.js       # Express API server
├── package.json
└── README.md
```

---

## 🛠 Tech Stack

- **[Axios](https://axios-http.com/)** — fast HTTP requests
- **[Cheerio](https://cheerio.js.org/)** — HTML parsing & link extraction
- **[Puppeteer](https://pptr.dev/)** — headless Chrome for JS-rendered pages
- **[Express](https://expressjs.com/)** — API server
- **[CORS](https://github.com/expressjs/cors)** — cross-origin support

---

## 📝 License

ISC
