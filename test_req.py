import os
from dotenv import load_dotenv
from yoomoney import Client

load_dotenv()
token = os.getenv("YOOMONEY_TOKEN")
print(f"Token length: {len(token) if token else 0}")
try:
    client = Client(token)
    user = client.account_info()
    print(f"Account: {user.account}")
    print(f"Balance: {user.balance}")
except Exception as e:
    print(f"Exception: {e}")
