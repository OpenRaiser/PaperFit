#!/usr/bin/env node

/**
 * PaperFit Install Script
 * Runs during npm install to verify dependencies and prepare the environment
 */

const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

console.log('📦 PaperFit Install Script\n');

const projectRoot = path.join(__dirname, '..');

// Check Python availability
console.log('🔍 Checking Python...');
try {
    execSync('python3 --version', { stdio: 'pipe' });
    const pythonVersion = execSync('python3 --version', { encoding: 'utf-8' }).trim();
    console.log(`✅ ${pythonVersion}`);
} catch (e) {
    console.log('⚠️  Python 3 not found. Please install Python 3.8+');
    console.log('   macOS: brew install python@3.11');
    console.log('   Linux: apt-get install python3 python3-pip');
}

// Check latexmk
console.log('\n🔍 Checking LaTeX...');
try {
    execSync('which latexmk', { stdio: 'pipe' });
    console.log('✅ latexmk detected');
} catch (e) {
    console.log('⚠️  latexmk not found. Install MacTeX or TeX Live');
    console.log('   macOS: brew install --cask mactex');
    console.log('   Linux: apt-get install texlive-full latexmk');
}

// Check poppler
try {
    execSync('which pdfinfo', { stdio: 'pipe' });
    console.log('✅ Poppler utilities detected');
} catch (e) {
    console.log('⚠️  Poppler not found. Required for PDF rendering.');
    console.log('   macOS: brew install poppler');
    console.log('   Linux: apt-get install poppler-utils');
}

// Install Python dependencies
console.log('\n📦 Installing Python dependencies...');
const requirementsPath = path.join(projectRoot, 'requirements.txt');
if (fs.existsSync(requirementsPath)) {
    try {
        execSync('pip3 install -r requirements.txt', {
            cwd: projectRoot,
            stdio: 'inherit'
        });
        console.log('✅ Python dependencies installed');
    } catch (e) {
        console.log('⚠️  Failed to install Python dependencies. Run manually: pip3 install -r requirements.txt');
    }
} else {
    console.log('⚠️  requirements.txt not found. Skipping Python dependencies.');
}

console.log('\n✅ Install script completed');
