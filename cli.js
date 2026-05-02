#!/usr/bin/env node

const { scrapePDPLinks } = require("./scraper");

// ─── CLI Entry Point ─────────────────────────────────────────────────
async function main() {
  const args = process.argv.slice(2);

  if (args.length === 0 || args.includes("--help") || args.includes("-h")) {
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

    const result = await scrapePDPLinks(url, {
      sameDomain: !allDomains,
      timeout,
      mode,
    });

    if (compact) {
    } else {
    }

  } catch (err) {
    process.exit(1);
  }
}

main();
