import bittensor as bt
import requests
from binascii import hexlify

API_BASE = "https://api.agentbuilder80.com"

def generate_auth_headers(wallet_name: str, password: str = None) -> dict:
    """
    Generate authentication headers for API requests.
    
    Args:
        wallet_name: Your Bittensor wallet name
        password: Wallet password (optional if already unlocked)
    
    Returns:
        Dictionary with headers ready to use
    """
    # Load wallet
    wallet = bt.wallet(name=wallet_name)
    if password:
        wallet.coldkey_file.save_password_to_env(password)
    wallet.unlock_coldkey()
    
    # Sign ANY message (you can reuse this!)
    coldkey = wallet.coldkey.ss58_address
    message = "I want to use Agent Builder API"
    signature = wallet.coldkey.sign(message.encode())
    signature_hex = hexlify(signature).decode()
    
    # Get coldkey address
    # coldkey = "5EPjbb5nmV9MuvEGNBJa3EUEGHpTdWLAHcmiSDbTnBEJyKnU"
    # signature_hex = "f01644b4a24974d6feb32594375e87891a04862dac94f91396f417eaa5a47a71e118fd3328fa8475305c75f6f9be5fa68105f12ce4bdae77cd7f84ecc82d0784"
    # Create standard format
    signed_message = f"{message}<separate>Signed by: {coldkey}<separate>Signature: {signature_hex}"
    
    return {
        "Content-Type": "application/json",
        "X-Signed-Message": signed_message,
        "X-Coldkey": coldkey
    }

# Generate headers once, reuse for all requests!
headers = generate_auth_headers("my_wallet", "my_password")
print(f"Ready to make authenticated requests!")

# Your workflow payload
payload = {
    "workflow": {
        "workflow_id": "test_workflow",
        "nodes": [],
        "edges": []
    },
    "cid": "conversation_123"
}

# Make authenticated request
response = requests.post(
    f"{API_BASE}/orchestrate/execute",
    headers=headers,  # Your auth headers
    json=payload,
    timeout=180
)

print(response.json())