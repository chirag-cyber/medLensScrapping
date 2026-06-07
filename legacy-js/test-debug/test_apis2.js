const https = require('https');

function fetch(url) {
    return new Promise((resolve, reject) => {
        https.get(url, {
            headers: {
                'User-Agent': 'Mozilla/5.0'
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
        'https://medcompare.in/api/medicine-info?q=paracetamol',
        'https://medcompare.in/api/generic-alternatives?slug=paracetamol',
        'https://medcompare.in/api/category-search?q=fever',
        'https://medcompare.in/api/salt-search?q=paracetamol'
    ];

    for (const url of urls) {
        console.log(`Testing ${url}`);
        try {
            const res = await fetch(url);
            console.log(`Status: ${res.statusCode}`);
            console.log(`Data: ${res.data.substring(0, 300)}...`);
        } catch (e) {
            console.error(`Error: ${e.message}`);
        }
        console.log('---');
    }
}

test();
