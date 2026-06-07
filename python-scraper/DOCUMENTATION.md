# MediSaathi Rx India - Python Scraper Documentation

This directory contains the new 7-platform scraping architecture for MediSaathi Rx India. It is designed to be highly reliable, evading bot protection using Playwright and optimized search logic to find and aggregate medicine pricing and clinical data across major Indian digital pharmacies.

## Core Architecture

The scraping system is divided into modular components:

### 1. `search_engine.py` (The 7-Platform Aggregator)
The core `UnifiedSearchEngine` orchestrates concurrent searches across 7 different pharmacy platforms:
- **1mg** (`OneMgSearchScraper`)
- **Netmeds** (`NetmedsSearchScraper`)
- **Apollo Pharmacy** (`ApolloScraper`)
- **PharmEasy** (`PharmEasyScraper`)
- **Medplus** (`MedplusScraper`)
- **Truemeds** (`TruemedsScraper`)
- **PlatinumRx** (`PlatinumRxScraper`)
- **Amazon Pharmacy** (`AmazonPharmacyScraper` - Optional 8th)

It uses an intelligent `compute_relevance_score` algorithm to filter out irrelevant products (e.g., wrong dosages, injections when tablets were searched) and returns the best matching product from each platform.

### 2. `detail_scraper.py` (Clinical Data Fetcher)
The `ClinicalDetailScraper` visits individual medicine pages (prioritizing 1mg and Netmeds) to extract deep clinical information:
- Composition / Salts
- Description
- Uses & Side Effects
- How to Use & How it Works
- Missed Dose instructions
- Safety Advice (Alcohol, Pregnancy, Driving, etc.)
- Quick Tips & Fact Box
- Frequently Asked Questions (FAQs)

### 3. `main_scrapper.py` (CLI Testing Tool)
A command-line script to test the full pipeline for a single medicine.
**Usage:** `python main_scrapper.py "Dolo 650"`
It searches the 7 platforms, fetches clinical data, and outputs the aggregated result to `scraped_medicine.json`.

### 4. `sync_prices.py` (Legacy Price Sync)
A script that iterates through the MongoDB `medicines` collection and uses the `UnifiedSearchEngine` to refresh the `prices` collection with live data.

### 5. `search_medicine.py` (CLI Search Tool)
A lightweight script to find clinical details for a medicine without fetching all prices.

---

## The Unified Database Script (`sync_and_scrape.py`)

To seamlessly integrate the scraped data into the MongoDB database, we use the master script: `sync_and_scrape.py`. 

### Usage

**1. Scrape and Add a New Medicine:**
If a medicine is not in the database, this command will search all 7 platforms, extract deep clinical data, and insert the normalized record into the `medicines` and `prices` collections.
```bash
python sync_and_scrape.py --scrape "Medicine Name"
```

**2. Sync All Existing Medicines:**
Iterates through the existing database. For each medicine, it searches the 7 platforms to refresh pricing and stock status in the `prices` collection. If the medicine is missing clinical data, it will also attempt to fetch and update it.
```bash
python sync_and_scrape.py --sync-all --limit 100
```
