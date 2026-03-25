import os
from dotenv import load_dotenv
from yoomoney import Client

load_dotenv()
token = os.getenv("YOOMONEY_TOKEN")
try:
    client = Client(token)
    history = client.operation_history(type="deposition", status="success")
    print("Found operations with type=deposition, status=success:")
    for op in history.operations:
        print(f"ID: {op.operation_id}, Amount: {op.amount}, Label: {op.label}")
except Exception as e:
    print(f"Exception: {e}")
