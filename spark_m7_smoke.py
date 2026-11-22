#!/usr/bin/env python3
"""Run a read-only live end-to-end MedGemma chat smoke test; no corpus mutation."""
import json
import os
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE = os.getenv('SPARK_API_URL', 'http://127.0.0.1:18080').rstrip('/')

def fetch(path, payload=None):
    request = Request(BASE + path, data=json.dumps(payload).encode('utf-8') if payload else None,
                      headers={'Content-Type': 'application/json'} if payload else {},
                      method='POST' if payload else 'GET')
    with urlopen(request, timeout=240) as response:
        return json.load(response)

def main():
    try:
        print('READY', json.dumps(fetch('/ready')))
        status = fetch('/api/v1/chat/status')
        print('MODEL', json.dumps(status))
        if not status.get('model_ready'):
            print('FAIL: Pull the MedGemma model into the running Ollama container before the chat test')
            return 2
        answer = fetch('/api/v1/chat', {'message': "I'm looking for datasets about type 2 diabetes.", 'history': []})
        print('ACTION', answer.get('action'))
        print('ANSWER', answer.get('answer'))
        print('STUDIES', [(row['study_id'], row.get('original_title')) for row in answer.get('results', [])])
        search = answer.get('search') or {}
        if answer.get('action') != 'search_studies' or search.get('source') != 'original_dbgap_metadata_only' or search.get('enrichment_used') is not False:
            print('FAIL: Did not execute original-only dbGaP study search')
            return 3
        print('PASS: MedGemma issued a bounded original-only corpus search. Clinical accuracy is not yet measured.')
        return 0
    except (HTTPError, URLError, ValueError) as error:
        print('FAIL', error)
        if isinstance(error, HTTPError):
            print(error.read().decode('utf-8', 'replace'))
        return 1

if __name__ == '__main__':
    sys.exit(main())
