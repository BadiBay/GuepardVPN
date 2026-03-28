import aiohttp
import asyncio

async def test():
    urls = [
        "https://sub.guepardvpn.top:8000/sub/RGVEcm9wLDE3NzQyMDYxNTUgROyFhsBlo",
        "http://sub.guepardvpn.top:8000/sub/RGVEcm9wLDE3NzQyMDYxNTUgROyFhsBlo",
        "https://sub.guepardvpn.top/sub/RGVEcm9wLDE3NzQyMDYxNTUgROyFhsBlo",
        "http://127.0.0.1:8000/sub/RGVEcm9wLDE3NzQyMDYxNTUgROyFhsBlo"
    ]
    
    async with aiohttp.ClientSession() as session:
        for u in urls:
            try:
                async with session.get(u, ssl=False, timeout=3) as r:
                    print(f"URL: {u} -> Status: {r.status}")
            except Exception as e:
                print(f"URL: {u} -> Error: {e}")

asyncio.run(test())
