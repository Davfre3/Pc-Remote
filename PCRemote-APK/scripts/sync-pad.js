// Copia la pagina del mando (unica fuente: PCRemote-EXE/app/static/index.html) y el lector de QR a www/
const fs = require('fs'), path = require('path');
const root = path.join(__dirname, '..');
const src = path.join(root, '..', 'PCRemote-EXE', 'app', 'static', 'index.html');
fs.copyFileSync(src, path.join(root, 'www', 'pad.html'));
fs.copyFileSync(path.join(root, 'node_modules', 'jsqr', 'dist', 'jsQR.js'), path.join(root, 'www', 'jsQR.js'));
console.log('pad.html y jsQR.js actualizados en www/');
