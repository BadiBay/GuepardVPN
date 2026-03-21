import asyncio
import xui_api

async def main():
    import aiohttp
    async with aiohttp.ClientSession() as session:
        await xui_api.login(session)
        # Checking panel settings to see if IP Record is off
        url_settings = f"{xui_api.PANEL_URL}/panel/setting/all"
        async with session.post(url_settings) as resp:
            try:
                data = await resp.json()
                print("--- Panel Settings ---")
                print(f"tgBotEnable: {data.get('obj', {}).get('tgBotEnable')}")
                # We can dump all keys to see what relates to IP
            except:
                pass
                
        # Checking inbound list to see if IPs are nested
        url_inbounds = f"{xui_api.PANEL_URL}/panel/api/inbounds/list"
        async with session.get(url_inbounds) as resp:
            data = await resp.json()
            if data.get("success"):
                inbounds = data.get("obj", [])
                print(f"--- Inbound Data ---")
                for inbound in inbounds:
                    client_stats = inbound.get('clientStats', [])
                    for c in client_stats:
                        print(f"Email: {c.get('email')}, IP Count: {c.get('ips', 'N/A')}")
            else:
                print("Failed inbound.")

if __name__ == "__main__":
    asyncio.run(main())
