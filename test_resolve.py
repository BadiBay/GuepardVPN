import asyncio
import remnawave_api as api

async def test():
    session = await api.get_session()
    token = await api.get_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    
    m_user = api.get_m_username(1034853921)
    
    print("Testing GET /api/users/username")
    async with session.get(f"{api.PANEL_URL}/api/users/{m_user}", headers=headers) as resp:
        print(resp.status)
        if resp.status == 200:
            print(await resp.json())
            
    print("Testing POST /api/users/resolve")
    async with session.post(f"{api.PANEL_URL}/api/users/resolve", json={"username": m_user}, headers=headers) as resp:
        print(resp.status)
        if resp.status == 200:
            print(await resp.json())
            
    await session.close()
    
if __name__ == "__main__":
    asyncio.run(test())
