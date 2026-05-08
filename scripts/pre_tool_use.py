#!/usr/bin/env python3
"""
PaperFit Pre-Tool Use Security Hook

Detects secrets and sensitive patterns before tool execution.
Inspired by ECC's beforeSubmitPrompt hook with sk-, ghp_, AKIA patterns.

Usage:
    python pre_tool_use.py --check-secrets <content>
    python pre_tool_use.py --check-file <file_path>
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Secret patterns based on ECC security implementation
SECRET_PATTERNS = [
    # API Keys
    (r'sk-[a-zA-Z0-9]{20,}', 'OpenAI API Key (sk-...)'),
    (r'sk-proj-[a-zA-Z0-9]{20,}', 'OpenAI Project Key'),
    (r'api[_-]?key[\"\'\s]*[:=]\s*[\"\'\'][a-zA-Z0-9]{16,}[\"\'\']', 'Generic API Key'),

    # GitHub
    (r'ghp_[a-zA-Z0-9]{36}', 'GitHub Personal Access Token'),
    (r'gho_[a-zA-Z0-9]{36}', 'GitHub OAuth Token'),
    (r'ghu_[a-zA-Z0-9]{36}', 'GitHub User Token'),
    (r'ghs_[a-zA-Z0-9]{36}', 'GitHub Server Token'),
    (r'ghr_[a-zA-Z0-9]{36}', 'GitHub Refresh Token'),

    # AWS
    (r'AKIA[0-9A-Z]{16}', 'AWS Access Key ID'),
    (r'[0-9a-zA-Z/+]{40}={0,2}', 'Possible AWS Secret Key'),

    # Azure
    (r'[a-zA-Z0-9]{8}-[a-zA-Z0-9]{4}-[a-zA-Z0-9]{4}-[a-zA-Z0-9]{4}-[a-zA-Z0-9]{12}', 'Azure/GUID (potential secret)'),

    # Google
    (r'AIza[0-9A-Za-z\\-_]{35}', 'Google Cloud API Key'),
    (r'ya29\\.[0-9A-Za-z\\-_]+', 'Google OAuth Token'),

    # Stripe
    (r'sk_live_[0-9a-zA-Z]{24,}', 'Stripe Secret Key'),
    (r'pk_live_[0-9a-zA-Z]{24,}', 'Stripe Publishable Key'),

    # Slack
    (r'xox[baprs]-[0-9a-zA-Z]{10,48}', 'Slack Token'),

    # Private Keys
    (r'-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----', 'Private Key'),
    (r'-----BEGIN PGP PRIVATE KEY BLOCK-----', 'PGP Private Key'),

    # Passwords in config
    (r'password[\"\'\s]*[:=]\s*[\"\'][^\"\']{8,}[\"\']', 'Hardcoded Password'),
    (r'passwd[\"\'\s]*[:=]\s*[\"\'][^\"\']{8,}[\"\']', 'Hardcoded Password (passwd)'),

    # JWT Tokens
    (r'eyJ[a-zA-Z0-9_-]*\\.eyJ[a-zA-Z0-9_-]*\\.[a-zA-Z0-9_-]*', 'JWT Token'),

    # NPM Tokens
    (r'npm_[a-zA-Z0-9]{36}', 'NPM Access Token'),

    # Hugging Face
    (r'hf_[a-zA-Z]{34}', 'Hugging Face Token'),

    # Generic secrets
    (r'secret[\"\'\s]*[:=]\s*[\"\'][^\"\']{8,}[\"\']', 'Hardcoded Secret'),
    (r'bearer[\\s]+[a-zA-Z0-9\\-_\\.]{20,}', 'Bearer Token'),
]

# Files that commonly contain secrets
SENSITIVE_FILES = [
    '.env',
    '.env.local',
    '.env.production',
    '.env.development',
    '.env.test',
    '.git-credentials',
    '.netrc',
    '.pgpass',
    '.my.cnf',
    'credentials',
    'credentials.json',
    'credentials.yaml',
    'credentials.yml',
    'config.json',
    'config.yaml',
    'config.yml',
    'secrets.json',
    'secrets.yaml',
    'secrets.yml',
    '.dsn',
    'id_rsa',
    'id_dsa',
    'id_ecdsa',
    'id_ed25519',
    '.pem',
    '.key',
    '.p12',
    '.pfx',
]


def check_for_secrets(content: str, file_path: str = None) -> list:
    """
    Check content for secret patterns.

    Args:
        content: The text content to scan
        file_path: Optional file path for context

    Returns:
        List of findings, each with pattern_name, match, and severity
    """
    findings = []

    for pattern, name in SECRET_PATTERNS:
        matches = re.finditer(pattern, content, re.IGNORECASE)
        for match in matches:
            # Skip false positives
            matched_text = match.group(0)

            # Skip if it looks like an example/placeholder
            if any(placeholder in matched_text.lower() for placeholder in
                   ['example', 'your_', '<', '>', 'xxx', '***', '${', '{{']):
                continue

            # Skip very short matches that might be false positives
            if len(matched_text) < 10 and 'key' not in name.lower():
                continue

            findings.append({
                'pattern': name,
                'match': matched_text[:50] + '...' if len(matched_text) > 50 else matched_text,
                'position': match.start(),
                'severity': 'CRITICAL' if 'private key' in name.lower() or 'secret' in name.lower() else 'HIGH'
            })

    return findings


def check_sensitive_file(file_path: str) -> dict:
    """
    Check if a file path matches known sensitive file patterns.

    Args:
        file_path: The file path to check

    Returns:
        Dict with is_sensitive, reason, and risk_level
    """
    path_lower = file_path.lower()
    file_name = Path(file_path).name.lower()

    # Check exact matches
    for sensitive in SENSITIVE_FILES:
        if file_name == sensitive or path_lower.endswith(f'/{sensitive}'):
            return {
                'is_sensitive': True,
                'reason': f'Matches sensitive file pattern: {sensitive}',
                'risk_level': 'HIGH'
            }

    # Check extensions
    sensitive_extensions = ['.key', '.pem', '.p12', '.pfx', '.dsn', '.credentials']
    if any(path_lower.endswith(ext) for ext in sensitive_extensions):
        return {
            'is_sensitive': True,
            'reason': f'Sensitive file extension detected',
            'risk_level': 'HIGH'
        }

    # Check for .env files
    if file_name.startswith('.env'):
        return {
            'is_sensitive': True,
            'reason': 'Environment file detected',
            'risk_level': 'MEDIUM'
        }

    return {
        'is_sensitive': False,
        'reason': None,
        'risk_level': 'LOW'
    }


def main():
    parser = argparse.ArgumentParser(description='PaperFit Security Hook')
    parser.add_argument('--check-secrets', type=str, help='Check string content for secrets')
    parser.add_argument('--check-file', type=str, help='Check file for secrets')
    parser.add_argument('--check-file-sensitive', type=str, help='Check if file path is sensitive')
    parser.add_argument('--json', action='store_true', help='Output in JSON format')

    args = parser.parse_args()

    findings = []
    sensitive_file_result = None

    if args.check_secrets:
        findings = check_for_secrets(args.check_secrets)

    if args.check_file:
        try:
            file_path = args.check_file
            content = Path(file_path).read_text(encoding='utf-8', errors='ignore')
            findings = check_for_secrets(content, file_path)

            # Also check if the file itself is sensitive
            sensitive_file_result = check_sensitive_file(file_path)
            if sensitive_file_result['is_sensitive']:
                findings.append({
                    'pattern': 'Sensitive File',
                    'match': sensitive_file_result['reason'],
                    'position': 0,
                    'severity': sensitive_file_result['risk_level']
                })
        except (OSError, UnicodeDecodeError) as e:
            if args.json:
                print(json.dumps({'error': str(e)}))
            else:
                print(f'Error reading file: {e}')
            sys.exit(1)

    if args.check_file_sensitive:
        sensitive_file_result = check_sensitive_file(args.check_file_sensitive)
        if args.json:
            print(json.dumps(sensitive_file_result, indent=2))
        else:
            if sensitive_file_result['is_sensitive']:
                print(f"⚠️  {sensitive_file_result['reason']}")
                print(f"   Risk Level: {sensitive_file_result['risk_level']}")
            else:
                print("✅ File does not match sensitive patterns")
        sys.exit(0 if not sensitive_file_result['is_sensitive'] else 1)

    # Output results
    if args.json:
        output = {
            'findings': findings,
            'finding_count': len(findings),
            'has_secrets': len(findings) > 0
        }
        if sensitive_file_result:
            output['sensitive_file'] = sensitive_file_result
        print(json.dumps(output, indent=2))
    else:
        if findings:
            print(f"\n🚨 SECURITY ALERT: {len(findings)} potential secret(s) detected\n")
            for i, finding in enumerate(findings, 1):
                print(f"  {i}. {finding['pattern']}")
                print(f"     Match: {finding['match']}")
                print(f"     Severity: {finding['severity']}")
                print()
            print("⚠️  Remove secrets before committing!\n")
            sys.exit(1)
        else:
            print("✅ No secrets detected")
            sys.exit(0)


if __name__ == '__main__':
    main()
