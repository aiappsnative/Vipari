#!/usr/bin/env python
import os
import json
import time
import urllib.request
import urllib.error

from dotenv import load_dotenv
import jwt
from github import Github


def main():
    load_dotenv()

    print('--- ENV VARS (masking secrets) ---')
    for k in [
        'GITHUB_APP_ID',
        'GITHUB_PRIVATE_KEY_PATH',
        'GITHUB_WEBHOOK_SECRET',
        'GITHUB_PAT',
        'NGROK_AUTHTOKEN',
        'AZURE_OPENAI_ENDPOINT',
        'FOUNDRY_PROJECT_ENDPOINT',
    ]:
        v = os.getenv(k)
        if v is None:
            print(f'{k}: MISSING')
        else:
            if 'KEY' in k or 'TOKEN' in k or 'SECRET' in k or 'PAT' in k:
                print(f'{k}: (set) length={len(v)}')
            else:
                print(f'{k}: {v}')

    print('\n--- GitHub App JWT check ---')
    app_id = os.getenv('GITHUB_APP_ID')
    priv_path = os.getenv('GITHUB_PRIVATE_KEY_PATH')
    if not app_id or not priv_path:
        print('Missing GITHUB_APP_ID or GITHUB_PRIVATE_KEY_PATH, skipping GitHub App test')
    else:
        try:
            with open(priv_path, 'r') as f:
                pk = f.read()
            now = int(time.time())
            payload = {'iat': now - 60, 'exp': now + 600, 'iss': str(app_id)}
            token = jwt.encode(payload, pk, algorithm='RS256')
            if isinstance(token, bytes):
                token = token.decode()
            req = urllib.request.Request(
                'https://api.github.com/app',
                headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.load(resp)
            print('GitHub App API OK, app name:', data.get('name'))
        except Exception as e:
            print('GitHub App check failed:', type(e).__name__, e)

    print('\n--- GitHub PAT check (dummyAI repo) ---')
    pat = os.getenv('GITHUB_PAT')
    if not pat:
        print('Missing GITHUB_PAT, skipping')
    else:
        try:
            gh = Github(pat)
            repo = gh.get_repo('doria90/dummyAI')
            print('Accessed repo:', repo.full_name, 'private=', repo.private, 'default_branch=', repo.default_branch)
        except Exception as e:
            print('GitHub PAT check failed:', type(e).__name__, e)

    print('\n--- Azure/Foundry endpoint check ---')
    endpoint = os.getenv('AZURE_OPENAI_ENDPOINT')
    key = os.getenv('FOUNDRY_API_KEY') or os.getenv('OPENAI_API_KEY')
    if not endpoint or not key:
        print('Missing endpoint/key, skipping')
    else:
        url = endpoint.rstrip('/') + '/models'
        headers = {'api-key': key}
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.load(resp)
            print('Azure/OpenAI endpoint reachable: returned keys', list(data.keys())[:5])
        except urllib.error.HTTPError as e:
            print('Azure/OpenAI request failed:', e.code, e.reason, e.read().decode('utf-8', 'ignore')[:200])
        except Exception as e:
            print('Azure/OpenAI request failed:', type(e).__name__, e)

    print('\n--- ngrok availability check ---')
    import shutil, subprocess

    ngrok_path = shutil.which('ngrok')
    if not ngrok_path:
        print('ngrok not found on PATH')
    else:
        try:
            out = subprocess.check_output([ngrok_path, 'version'], stderr=subprocess.STDOUT, text=True)
            print('ngrok is installed:', out.strip())
        except Exception as e:
            print('ngrok check failed:', type(e).__name__, e)


if __name__ == '__main__':
    main()
