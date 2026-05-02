/**
 * Script to remove all console.* statements from JS files,
 * handling both single-line and multi-line calls.
 */
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// Get all JS files (excluding node_modules, .planning, and this script)
const files = execSync(
  'find . -name "*.js" -not -path "*/node_modules/*" -not -path "*/.planning/*" -not -name ".remove-consoles.js"',
  { cwd: __dirname, encoding: 'utf8' }
).trim().split('\n').filter(Boolean);

let totalRemoved = 0;

for (const relFile of files) {
  const filePath = path.resolve(__dirname, relFile);
  const original = fs.readFileSync(filePath, 'utf8');
  let content = original;

  // Handle both single-line and multi-line by tracking parentheses
  const consoleRegex = /^[ \t]*console\.\w+\s*\(/gm;
  
  let match;
  const removals = []; // collect ranges to remove

  while ((match = consoleRegex.exec(content)) !== null) {
    const start = match.index;
    // Find the matching closing paren
    let parenDepth = 0;
    let i = start;
    let foundOpen = false;
    
    while (i < content.length) {
      const ch = content[i];
      if (ch === '(') {
        parenDepth++;
        foundOpen = true;
      } else if (ch === ')') {
        parenDepth--;
        if (foundOpen && parenDepth === 0) {
          // Found matching close paren
          let end = i + 1;
          // Skip optional semicolon
          if (content[end] === ';') end++;
          // Skip trailing whitespace and newline
          while (end < content.length && (content[end] === ' ' || content[end] === '\t')) end++;
          if (content[end] === '\r') end++;
          if (content[end] === '\n') end++;
          
          removals.push({ start, end });
          break;
        }
      } else if (ch === '"' || ch === "'" || ch === '`') {
        // Skip string literals
        const quote = ch;
        i++;
        while (i < content.length) {
          if (content[i] === '\\') { i += 2; continue; }
          if (content[i] === quote) break;
          // Template literal newlines are ok
          i++;
        }
      }
      i++;
    }
  }

  if (removals.length === 0) continue;

  // Remove from end to start to preserve indices
  for (let r = removals.length - 1; r >= 0; r--) {
    content = content.slice(0, removals[r].start) + content.slice(removals[r].end);
  }

  // Clean up any resulting empty lines (multiple blank lines -> single)
  content = content.replace(/\n{3,}/g, '\n\n');

  if (content !== original) {
    fs.writeFileSync(filePath, content, 'utf8');
    totalRemoved += removals.length;
  }
}

