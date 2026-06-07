#!/bin/bash
# Azure Server Setup Script for Medlens Scraper
# Run this script on your Azure Ubuntu/Debian server to prepare the environment

echo "Starting Azure Setup for Medlens Scraper..."

# 1. Update system and install Python + dependencies
echo "Installing Python and required system libraries..."
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv git curl

# 2. Setup Virtual Environment
echo "Setting up Python Virtual Environment..."
cd "$(dirname "$0")"
python3 -m venv venv
source venv/bin/activate

# 3. Install Python Dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# 4. Install Playwright Browsers (Required for headless scraping on Linux)
echo "Installing Playwright browsers and Linux dependencies..."
# The --with-deps flag is critical on fresh Azure servers to install missing C++ libraries that Chromium needs!
playwright install chromium --with-deps

echo "--------------------------------------------------------"
echo "✅ Setup Complete!"
echo "--------------------------------------------------------"
echo "To run your scraper in the background on Azure:"
echo "1. Make sure your .env file is uploaded to the server with MONGO_URL"
echo "2. Activate the virtual environment: source venv/bin/activate"
echo "3. Run the script: nohup python sync_and_scrape.py > scraper.log 2>&1 &"
echo ""
echo "To check the logs: tail -f scraper.log"
echo "--------------------------------------------------------"
