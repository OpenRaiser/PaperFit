#!/usr/bin/env node
console.log('\nPaperFit CLI installed. Add the layout skill to your host:');
console.log('  paperfit-install --target codex   (or --target claude / --target cursor)');
console.log('Or from a paper project, install only into that project:');
console.log('  paperfit-install --target codex --scope project');
console.log('Open a new host session. Codex: $paperfit Inspect or repair this paper layout');
console.log('Claude Code / Cursor: ask to use PaperFit for this paper.');
console.log('The host keeps its own model and configuration. Python/TeX/Poppler setup: paperfit doctor --target <host>\n');
