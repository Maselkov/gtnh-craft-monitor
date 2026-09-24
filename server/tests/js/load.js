// Loads frontend scripts the way the page does - classic scripts sharing
// one global scope - into a fresh VM context, and returns that context
// so tests can call the functions they declare.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const JS_DIR = path.join(__dirname, '..', '..', 'static', 'js');

function loadScripts(...names) {
  const context = vm.createContext({});
  for (const name of names) {
    const file = path.join(JS_DIR, name);
    vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
  }
  return context;
}

// Values created inside the VM context have that context's Array/Object
// prototypes, which strict deep-equality treats as different - copy
// them into this realm before comparing.
function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

module.exports = { loadScripts, plain };
