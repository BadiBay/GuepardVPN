import asyncio
import aiohttp

async def test():
    url = "https://sub.guepardvpn.top:8000"
    print(f"Testing {url}")

    async with aiohttp.ClientSession() as session:
        # Try Marzban
        try:
            async with session.post(f"{url}/api/admin/token", data={"username": "Aldivi", "password": "GQMY6FQC"}, ssl=False, timeout=3) as r:
                print("Marzban Token status:", r.status)
        except Exception as e:
            print("Marzban Error:", e)

        # Try Remnawave
        try:
            async with session.post(f"{url}/api/auth/login", json={"username": "Aldivi", "password": "GQMY6FQC"}, ssl=False, timeout=3) as r:
                print("Remnawave Auth status:", r.status)
        except Exception as e:
            print("Remnawave Error:", e)

asyncio.run(test())
