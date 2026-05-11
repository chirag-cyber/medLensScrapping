const { scrapeProductDetail } = require('./productScraper.js');
console.log('Starting...');
scrapeProductDetail('https://www.netmeds.com/product/omefol-20mg-capsule-15s-lui1q6-8228150', { mode: 'fast', timeout: 5000 })
  .then((r) => { console.log('Done', r.name); process.exit(0); })
  .catch((e) => { console.log('Err', e.message); process.exit(1); });
