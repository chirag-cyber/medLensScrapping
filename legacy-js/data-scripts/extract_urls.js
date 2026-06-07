const fs = require('fs');
const content = fs.readFileSync(process.argv[2], 'utf8');
const apis = content.match(/["'`][^"'`]*\/api\/[^"'`]*["'`]/g);
if (apis) {
    const unique = [...new Set(apis)];
    unique.forEach(u => console.log(u));
} else {
    console.log('No /api/ URLs found.');
}
