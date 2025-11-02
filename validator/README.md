# Validator - Auto Weight Setter

A simple automated validator for Bittensor that fetches miner performance weights from a remote scoring API and submits them to the blockchain.

---

## 🎯 Overview

This validator automatically:
1. **Fetches** miner weights from the central scoring system
2. **Submits** weights to the Bittensor network on-chain
3. **Loops** continuously at specified intervals

The validator does not perform scoring itself - it relies on the orchestrator's centralized scoring system to evaluate miner performance and simply broadcasts those weights on-chain.

---

## 📋 Prerequisites

### 1. Bittensor Wallet
You need a registered Bittensor wallet with a hotkey registered as a validator on the subnet.

```bash
# Install Bittensor
pip install bittensor

# Create wallet (if you don't have one)
btcli wallet new_coldkey --wallet.name my_validator
btcli wallet new_hotkey --wallet.name my_validator --wallet.hotkey default

# Register as validator on subnet (requires TAO stake)
btcli subnet register --netuid 80 --wallet.name my_validator --wallet.hotkey default
```

### 2. TAO Stake
You must have sufficient TAO staked to your validator hotkey to set weights on the network.

---

## 🚀 Installation

### Step 1: Install Dependencies

```bash
pip install bittensor requests
```

### Step 2: Verify Your Wallet

Make sure your wallet is properly set up and registered:

```bash
btcli wallet overview --wallet.name my_validator
```

---

## ▶️ Running the Validator

### Basic Usage

```bash
python validator.py --wallet my_validator --hotkey default
```

### Full Command Options

```bash
python validator.py \
  --wallet <your_wallet_name> \
  --hotkey <your_hotkey_name> \
  --netuid 80 \
  --interval 30.0
```

### Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--wallet` | ✅ Yes | - | Your Bittensor coldkey wallet name |
| `--hotkey` | ✅ Yes | - | Your validator hotkey name |
| `--netuid` | No | 80 | Subnet netuid to set weights on |
| `--interval` | No | 30.0 | Seconds between weight updates |

---

## 🔧 How It Works

### 1. Weight Fetching

The validator fetches weights from the central API:
```
GET https://star145s-agent-score.hf.space/weights/array
```

**Response format:**
```json
{
  "weights": [0.1, 0.2, 0.15, ...],
  "num_uids": 256
}
```

### 2. Weight Submission

Weights are submitted to the Bittensor blockchain using:
```python
subtensor.set_weights(
    netuid=netuid,
    uids=[0, 1, 2, ...],
    weights=[0.1, 0.2, 0.15, ...],
    wallet=wallet,
    wait_for_inclusion=True,
    wait_for_finalization=False
)
```

### 3. Continuous Loop

The validator:
- Fetches weights from API
- Submits to blockchain
- Waits for next block (to avoid rate limits)
- Sleeps for the specified interval
- Repeats

---

## 📊 Example Output

```
Starting validator weight loop on subnet 80 using wallet=my_validator/default
[14:30:15] Fetched 128 weights (sum=1.000000)
[14:30:15] ✅ Weights set successfully on subnet 80
[14:30:45] Fetched 128 weights (sum=1.000000)
[14:30:45] ✅ Weights set successfully on subnet 80
[14:31:15] Fetched 128 weights (sum=1.000000)
[14:31:15] ✅ Weights set successfully on subnet 80
```

---

## ⚠️ Important Notes

### 1. **This is a Centralized Validator**
- The validator **does not** perform independent scoring
- It relies on the orchestrator's centralized scoring API
- Weights are calculated off-chain by the orchestrator

### 2. **TAO Requirements**
- You need sufficient TAO stake to set weights
- Higher stake = more influence on weight distribution
- Check subnet requirements for minimum stake

### 3. **Rate Limiting**
- The validator waits for the next block before submitting weights
- This prevents rate limiting issues
- Default interval (30s) is safe for most use cases
---

## 📁 File Structure

```
validator/
├── README.md          # This file
└── validator.py       # Main validator script
```

---

## 🔗 Related Links

- **Scoring API**: https://star145s-agent-score.hf.space/weights/array
- **Bittensor Documentation**: https://docs.bittensor.com/
- **Subnet Information**: Check subnet 80 details on Taostats

---

## ⚖️ License

See the main repository LICENSE file for details.
