"""Offline release checks. Only report paths/rules, never matched private values."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import sys

RULES = {
    'private-network-address': r'\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b',
    'machine-path': r'(?i)(?:[A-Z]:[/\\]+(?:Project|Users)[/\\]|/[U]sers/|/h[o]me/[^/\s]+/)',
    'private-document': r'alidocs\.dingtalk\.com/i/nodes/[A-Za-z0-9]+',
    'notification-id': r'\b(?:cid[A-Za-z0-9+/=]{10,}|Dcl[A-Za-z0-9]{20,})',
    'private-key': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY',
    'access-token': r'\bgh[pousr]_[A-Za-z0-9]{20,}|\bAKIA[0-9A-Z]{16}',
}
FORBIDDEN_PARTS = {'.local', '.tmp', '.cache', 'reports', '__pycache__', '.git'}
TEXT_EXTENSIONS = {'.md', '.py', '.ps1', '.json', '.yaml', '.yml', '.txt', '.cs', '.js', '.html', '.css'}


def inspect(root):
    findings = []
    files = [p for p in root.rglob('*') if p.is_file()]
    for path in files:
        relative = path.relative_to(root)
        if set(relative.parts) & FORBIDDEN_PARTS or path.name.endswith(('.local.json', '.private.json', '.pyc', '.pem', '.pfx', '.key')) or path.name.startswith('.env'):
            findings.append({'path': str(relative), 'rule': 'private-or-generated-file'})
            continue
        if path.name == '.gitignore':
            continue
        if path.suffix not in TEXT_EXTENSIONS:
            findings.append({'path': str(relative), 'rule': 'unexpected-file-type'})
            continue
        try:
            text = path.read_text(encoding='utf-8-sig')
            if path.suffix == '.json':
                json.loads(text)
        except (UnicodeError, ValueError):
            findings.append({'path': str(relative), 'rule': 'invalid-text-or-json'})
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for rule, pattern in RULES.items():
                if re.search(pattern, line):
                    findings.append({'path': str(relative), 'line': number, 'rule': rule})
        if path.suffix == '.md':
            for target in re.findall(r'\]\(([^)]+)\)', text):
                if re.match(r'[a-z]+:', target) or target.startswith('#') or '<' in target:
                    continue
                target = target.split('#')[0]
                if target and not (path.parent / target).exists():
                    findings.append({'path': str(relative), 'rule': 'broken-local-link'})
    return {'status': 'failed' if findings else 'ok', 'files': len(files), 'findings': findings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path, help='Clean staging package, excluding private/generated files')
    args = parser.parse_args()
    if not args.package.is_dir():
        print(json.dumps({'status': 'failed', 'error': 'PACKAGE_DIRECTORY_REQUIRED'}))
        return 1
    result = inspect(args.package)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result['status'] != 'ok')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
