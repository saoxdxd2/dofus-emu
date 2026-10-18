const fs = require('fs');

const code = fs.readFileSync('client/www/build/script.js', 'utf8');

const idx = 5085167;
const funcStart = code.lastIndexOf('function(', idx);
console.log('Function start:', funcStart);
console.log(code.substring(funcStart - 100, funcStart + 2500));
