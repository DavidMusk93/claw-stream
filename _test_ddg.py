"""quick test for DDG search"""
import json, sys
from duckduckgo_search import DDGS

with DDGS() as ddgs:
    results = list(ddgs.text('夢実かなえ MFYD-115', max_results=3))
    print(f'Results: {len(results)}')
    for r in results:
        print(f'  title: {r.get("title","")[:80]}')
        print(f'  body: {r.get("body","")[:120]}')
        print()
