#!/usr/bin/env node

const { scrapePDPLinks } = require("./scraper");

// ─── CLI Entry Point ─────────────────────────────────────────────────
async function main() {
  const args = process.argv.slice(2);

  if (args.length === 0 || args.includes("--help") || args.includes("-h")) {
    console.log(`
╔══════════════════════════════════════════════════╗
║        PDP Link Scraper — CLI Mode 🔍           ║
╠══════════════════════════════════════════════════╣
║                                                  ║
║  Usage:                                          ║
║    node cli.js <url> [options]                   ║
║                                                  ║
║  Options:                                        ║
║    --mode <mode>    auto|fast|browser (default: auto)║
║    --browser        Shortcut for --mode browser  ║
║    --all-domains    Include cross-domain links    ║
║    --timeout <ms>   Set request timeout           ║
║    --pretty         Pretty-print JSON output      ║
║    --help, -h       Show this help message        ║
║                                                  ║
║  Modes:                                          ║
║    auto    → Axios first, Puppeteer if 0 results ║
║    fast    → Axios only (no JS rendering)        ║
║    browser → Puppeteer only (JS-rendered pages)  ║
║                                                  ║
║  Examples:                                       ║
║    node cli.js "https://www.apollopharmacy.in/"   ║
║    node cli.js "https://apollopharmacy.in/..." --browser ║
║                                                  ║
╚══════════════════════════════════════════════════╝
    `);
    process.exit(0);
  }

  const url = args[0];
  const allDomains = args.includes("--all-domains");
  const compact = args.includes("--compact");
  const useBrowser = args.includes("--browser");
  const timeoutIdx = args.indexOf("--timeout");
  const timeout = timeoutIdx !== -1 ? parseInt(args[timeoutIdx + 1], 10) : 15000;
  const modeIdx = args.indexOf("--mode");
  const mode = useBrowser ? "browser" : modeIdx !== -1 ? args[modeIdx + 1] : "auto";

  try {
    console.error(`\n🔍 Scraping PDP links from: ${url}`);
    console.error(`   Mode: ${mode}\n`);

    const result = await scrapePDPLinks(url, {
      sameDomain: !allDomains,
      timeout,
      mode,
    });

    if (compact) {
      console.log(JSON.stringify(result));
    } else {
      console.log(JSON.stringify(result, null, 2));
    }

    console.error(
      `\n✅ Done! Found ${result.pdpLinksFound} PDP links out of ${result.totalLinksFound} total links (via ${result.mode}).\n`
    );
  } catch (err) {
    console.error(`\n❌ Error: ${err.message}\n`);
    process.exit(1);
  }
}

main();
