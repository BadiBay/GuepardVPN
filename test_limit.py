import asyncio
from datetime import datetime, timedelta, timezone
import remnawave_api as api

async def test():
    session = await api.get_session()
    token = await api.get_token()
        
    url = f"{api.PANEL_URL}/api/users"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    
    expire_at = (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()
    
    payload = {
        "username": "limit_test_user_3",
        "status": "ACTIVE",
        "expireAt": expire_at,
        "hwidDeviceLimit": 5
    }
    
    print("Payload:", payload)
    async with session.post(url, json=payload, headers=headers) as resp:
        if resp.status in (200, 201):
            print("OK:", await resp.json())
        else:
            print("ERR:", resp.status, await resp.text())
            
        
if __name__ == "__main__":
    asyncio.run(test())
