# medLensScrapping Full Restructure Script
# Run from: a:\medlens\Scrapping\medLensScrapping

$root = "a:\medlens\Scrapping\medLensScrapping"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " medLensScrapping Folder Restructure" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# ─── Step 1: Create all new directories ───────────────────────────────
Write-Host "`n[1/6] Creating new directories..." -ForegroundColor Yellow
$dirs = @(
    "$root\legacy-js",
    "$root\legacy-js\data-scripts",
    "$root\legacy-js\test-debug",
    "$root\data",
    "$root\python-scraper\tools",
    "$root\python-scraper\enrichment",
    "$root\python-scraper\archive"
)
foreach ($d in $dirs) {
    if (!(Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}
Write-Host "  Done." -ForegroundColor Green

# ─── Step 2: Move JS core files → legacy-js/ ─────────────────────────
Write-Host "`n[2/6] Moving JS core files to legacy-js/..." -ForegroundColor Yellow
$jsCore = @(
    "server.js", "orchestrator.js", "scraper.js", "productScraper.js",
    "cli.js", "batch-job.js", "ecosystem.config.js", "render.yaml",
    "setup-vm.sh", "package.json", "package-lock.json"
)
foreach ($f in $jsCore) {
    $src = "$root\$f"
    if (Test-Path $src) { Move-Item $src "$root\legacy-js\$f" -Force; Write-Host "  Moved $f" }
}
# Move etl/ folder
if (Test-Path "$root\etl") {
    Move-Item "$root\etl" "$root\legacy-js\etl" -Force
    Write-Host "  Moved etl/"
}
# Move node_modules/
if (Test-Path "$root\node_modules") {
    Move-Item "$root\node_modules" "$root\legacy-js\node_modules" -Force
    Write-Host "  Moved node_modules/"
}

# ─── Step 3: Move JS data-fix scripts → legacy-js/data-scripts/ ──────
Write-Host "`n[3/6] Moving JS data scripts to legacy-js/data-scripts/..." -ForegroundColor Yellow
$jsDataScripts = @(
    "interlink-medicines.js", "discover-alternatives.js", "fill-salt-dosage.js",
    "targeted-enrichment.js", "enrich-medicines.js", "enrich-medcompare.js",
    "medcompare-scraper.js", "backfill-dosage.js", "populate-salts.js",
    "repair-canonical-from-salt.js", "repair-dosage-noise.js",
    "cleanup-data.js", "cleanup-db.js", "cleanup-prices.js",
    "fix-platforms.js", "force-lower.js", "db-migration-cleaner.js",
    "sweep-prices.js", "reset-db.js", "check-dups.js", "check-prices.js",
    "check_db.js", "check_dolo.js", "inspect-links.js", "analyze-fuzzy.js",
    "extract_urls.js", "duplicate-collections.js", ".remove-consoles.js"
)
foreach ($f in $jsDataScripts) {
    $src = "$root\$f"
    if (Test-Path $src) { Move-Item $src "$root\legacy-js\data-scripts\$f" -Force; Write-Host "  Moved $f" }
}

# JS test/debug → legacy-js/test-debug/
$jsTestDebug = @(
    "test-apollo-puppeteer.js", "test-hang.js", "test-upper.js",
    "test_apis.js", "test_apis2.js", "test_extract.js", "test_token_api.js",
    "scratch_warfarin.js", "temp.js"
)
foreach ($f in $jsTestDebug) {
    $src = "$root\$f"
    if (Test-Path $src) { Move-Item $src "$root\legacy-js\test-debug\$f" -Force; Write-Host "  Moved $f" }
}

# ─── Step 4: Move data files → data/ ─────────────────────────────────
Write-Host "`n[4/6] Moving data files to data/..." -ForegroundColor Yellow
$dataFiles = @(
    "salts.json", "MEDSAVE.medicines.json", "medcompare_test_2.json",
    ".crawler-state.json", "crawler.log"
)
foreach ($f in $dataFiles) {
    $src = "$root\$f"
    if (Test-Path $src) { Move-Item $src "$root\data\$f" -Force; Write-Host "  Moved $f" }
}

# ─── Step 5: Move root Python scripts → python-scraper/tools/ ────────
Write-Host "`n[5/6] Moving root Python scripts to python-scraper/tools/..." -ForegroundColor Yellow
$rootPyTools = @(
    "clean_ibugesic.py", "cleanup_bad_matches.py", "find_suspects.py", "debug.py"
)
foreach ($f in $rootPyTools) {
    $src = "$root\$f"
    if (Test-Path $src) { Move-Item $src "$root\python-scraper\tools\$f" -Force; Write-Host "  Moved $f" }
}

# ─── Step 6: Restructure python-scraper/ internals ───────────────────
Write-Host "`n[6/6] Restructuring python-scraper/ internals..." -ForegroundColor Yellow
$pyScraper = "$root\python-scraper"

# Tools
$pyTools = @(
    "backup_collections.py", "check_coverage.py", "check_db.py",
    "inspect_db.py", "migrate_platform_casing.py"
)
foreach ($f in $pyTools) {
    $src = "$pyScraper\$f"
    if (Test-Path $src) { Move-Item $src "$pyScraper\tools\$f" -Force; Write-Host "  tools/$f" }
}

# Enrichment
$pyEnrich = @(
    "enrich.py", "enrich_clinical.py", "enrich_clinical_v2.py",
    "enrich_from_1mg.py", "enrich_from_medcompare.py", "enrich_from_siblings.py",
    "enrich_remaining.py", "fill_missing_side_effects.py", "clean_side_effects.py",
    "set_default_side_effects.py", "llm_side_effects_fix.py", "update_db_clinical.py"
)
foreach ($f in $pyEnrich) {
    $src = "$pyScraper\$f"
    if (Test-Path $src) { Move-Item $src "$pyScraper\enrichment\$f" -Force; Write-Host "  enrichment/$f" }
}

# Archive
$pyArchive = @(
    "debug.py", "debug2.py", "debug3.py", "debug4.py", "debug5.py",
    "debug_netmeds.py", "dump_html.py", "main_scrapper.py",
    "search_medicine.py", "sync_prices.py", "parser.py", "parser_netmeds.py",
    "apollo_api.py", "apollo_regex.py", "apollo_test.py",
    "test_apollo.py", "test_direct_scrape.py", "test_netmeds_direct.py",
    "test_netmeds_network.py", "test_platforms.py",
    "netmeds_api_test.py", "onemg_api_test.py", "onemg_search_test.py",
    "parse_1mg_test.py", "platinumrx_api_test.py",
    "truemeds_secret_api_test.py", "truemeds_test.py",
    "playwright_apollo_debug.py", "playwright_debug.py", "playwright_dom_debug.py",
    "1mg_test.html", "calpol_html.txt", "medplus_debug.html", "netmeds_debug.html",
    "scraped_medicine.json", "inspect_side_effects.py", "enrich_clinical_v2.log"
)
foreach ($f in $pyArchive) {
    $src = "$pyScraper\$f"
    if (Test-Path $src) { Move-Item $src "$pyScraper\archive\$f" -Force; Write-Host "  archive/$f" }
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host " Restructure Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "`nFinal structure:"
Write-Host "  medLensScrapping/"
Write-Host "    python-scraper/    (Active Python scraper)"
Write-Host "      scrapers/        (Platform modules)"
Write-Host "      tools/           (DB tools, maintenance)"
Write-Host "      enrichment/      (Clinical data enrichment)"
Write-Host "      archive/         (Old debug/test scripts)"
Write-Host "    legacy-js/         (Archived Node.js scraper)"
Write-Host "      etl/             (ETL pipeline)"
Write-Host "      data-scripts/    (Data fix scripts)"
Write-Host "      test-debug/      (Test scripts)"
Write-Host "    data/              (Data files)"
Write-Host "    backups/           (DB backups)"
Write-Host "    dashboard.html     (Monitoring - stays in root)"
Write-Host "    .env, README.md"

