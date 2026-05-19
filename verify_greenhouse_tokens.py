"""
Run this once to check which Greenhouse board tokens are valid.
Usage: python verify_greenhouse_tokens.py
"""
import asyncio
import aiohttp
from config import COMPANIES


async def check():
    async with aiohttp.ClientSession() as session:
        greenhouse = [c for c in COMPANIES if c["ats_type"] == "greenhouse"]
        print(f"Checking {len(greenhouse)} Greenhouse tokens…\n")

        for c in greenhouse:
            token = c["greenhouse_token"]
            url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        count = len(data.get("jobs", []))
                        print(f"  ✓  {c['company_name']:30s}  token={token}  ({count} jobs)")
                    else:
                        print(f"  ✗  {c['company_name']:30s}  token={token}  HTTP {resp.status}")
            except Exception as e:
                print(f"  !  {c['company_name']:30s}  token={token}  ERROR: {e}")


asyncio.run(check())
