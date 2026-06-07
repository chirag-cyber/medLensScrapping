const https = require('https');

const url = "https://www.medcompare.in/api/direct-search?q=Paracetamol%20O%20Tablet&token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI3YTk0ZjRjZC0zZDI1LTQxNjgtOTcwOS01NmRkYjJiNDlkM2MiLCJlbWFpbCI6IndvcmtpbmcyODIyQGdtYWlsLmNvbSIsImV4cCI6MTc3OTM3MTQ0NiwiaWF0IjoxNzc4NzY2NjQ2fQ.9h56D3Cc_mlWsKsVka1XgFoyLRnQtNPrUEtqsTpqFHc";

https.get(url, {
    headers: {
        'Accept': 'text/event-stream',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
}, (res) => {
    console.log(`Status: ${res.statusCode}`);
    
    res.on('data', (chunk) => {
        console.log(`Received chunk:\n${chunk.toString()}`);
    });
    
    res.on('end', () => {
        console.log('Stream ended.');
    });
}).on('error', (err) => {
    console.error('Error:', err.message);
});
