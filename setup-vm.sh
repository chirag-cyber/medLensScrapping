#!/bin/bash
set -e

echo "Starting MedLens Scraper VM Setup for Ubuntu..."

# 1. Update and install prerequisites
sudo apt-get update -y
sudo apt-get install -y curl wget git build-essential

# 2. Install Node.js (v20 LTS) if not installed
if ! command -v node &> /dev/null
then
    echo "Node.js not found. Installing Node.js v20..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
else
    echo "Node.js is already installed: $(node -v)"
fi

# 3. Install PM2 globally
if ! command -v pm2 &> /dev/null
then
    echo "Installing PM2..."
    sudo npm install -g pm2
else
    echo "PM2 is already installed."
fi

# 4. Install Puppeteer dependencies for Headless Chrome on Ubuntu
echo "Installing Puppeteer system dependencies..."
sudo apt-get install -y \
    ca-certificates \
    fonts-liberation \
    libappindicator3-1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libc6 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libgcc1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libstdc++6 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    lsb-release \
    xdg-utils

# 5. Install Project Dependencies
echo "Installing NPM dependencies..."
npm install

echo "================================================="
echo "Setup Complete!"
echo "Next Steps:"
echo "1. Configure your .env file with MongoDB and Groq API keys."
echo "2. Run 'pm2 start ecosystem.config.js' to start the pipeline."
echo "3. Run 'pm2 save' and 'pm2 startup' to ensure it starts on boot."
echo "================================================="
