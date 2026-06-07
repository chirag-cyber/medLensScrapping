const https = require('https');

function fetch(url) {
    return new Promise((resolve, reject) => {
        https.get(url, {
            headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
        }, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => resolve({statusCode: res.statusCode, data}));
        }).on('error', reject);
    });
}

async function test() {
    const urls = [
        'https://medcompare.in/api/popular-searches',
        'https://api.medcompare.in/api/popular-searches',
        'https://api.medcompare.in/popular-searches',
        'https://medcompare.in/api/direct-search?q=paracetamol',
        'https://api.medcompare.in/api/direct-search?q=paracetamol',
    ];

    for (const url of urls) {
        console.log(`Testing ${url}`);
        try {
            const res = await fetch(url);
            console.log(`Status: ${res.statusCode}`);
            console.log(`Data: ${res.data.substring(0, 100)}...`);
        } catch (e) {
            console.error(`Error: ${e.message}`);
        }
        console.log('---');
    }
}

test();
