import asyncio
import os
from dotenv import load_dotenv

load_dotenv()
import xui_api

async def main():
    print(f"Testing connection to: {xui_api.PANEL_URL}")
    print(f"Username: {xui_api.USERNAME}")
    print(f"Inbound ID: {xui_api.INBOUND_ID}")
    
    import aiohttp
    async with aiohttp.ClientSession() as session:
        # TEST 1: Login
        print("\n--- Test 1: Login ---")
        is_logged = await xui_api.login(session)
        print(f"Login success: {is_logged}")
        if not is_logged:
            print("Login failed! Check credentials or URL.")
            return
            
        # TEST 2: List Inbounds (to verify ID)
        print("\n--- Test 2: Check Inbounds ---")
        url = f"{xui_api.PANEL_URL}/panel/api/inbounds/list"
        headers = {'Accept': 'application/json'}
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            if data.get("success"):
                inbounds = data.get("obj", [])
                print(f"Found {len(inbounds)} inbounds.")
                for inbound in inbounds:
                    print(f" - ID: {inbound['id']}, Remark: {inbound.get('remark')}, Port: {inbound.get('port')}")
                
                # Check if our ID exists
                ids = [i['id'] for i in inbounds]
                if xui_api.INBOUND_ID not in ids:
                    print(f"WARNING: The Inbound ID {xui_api.INBOUND_ID} specified in .env does not exist!")
            else:
                print("Failed to list inbounds")

if __name__ == "__main__":
    asyncio.run(main())
