// Copia la pagina del mando (unica fuente: PCRemote-EXE/app/static/index.html) a www/pad.html
const fs = require('fs'), path = require('path');
const root = path.join(__dirname, '..');
const src = path.join(root, '..', 'PCRemote-EXE', 'app', 'static', 'index.html');
fs.copyFileSync(src, path.join(root, 'www', 'pad.html'));
console.log('pad.html actualizado en www/');
