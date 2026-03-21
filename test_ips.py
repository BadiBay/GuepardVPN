import asyncio
import xui_api

async def main():
    import aiohttp
    async with aiohttp.ClientSession() as session:
        await xui_api.login(session)
        email = "tg_1034853921" # testing with a known email
        url = f"{xui_api.PANEL_URL}/panel/api/inbounds/clientIps/{email}"
        async with session.post(url) as resp:
            print(await resp.text())

if __name__ == "__main__":
    asyncio.run(main())
