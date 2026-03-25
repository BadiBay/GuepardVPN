import os
from dotenv import load_dotenv
from yoomoney import Client

load_dotenv()
token = os.getenv("YOOMONEY_TOKEN")
try:
    client = Client(token)
    history = client.operation_history(records=5)
    print("All recent operations:")
    for op in history.operations:
        print(f"ID: {op.operation_id}, Amount: {op.amount}, Title: {op.title}, Type: {op.type}, Status: {op.status}, Label: {op.label}")
except Exception as e:
    print(f"Exception: {e}")
