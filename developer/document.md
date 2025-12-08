# AI Agent Builder - Developer Guide

**Bittensor Subnet 80 - Public API for Developers & Researchers**

---

## Overview

The AI Agent Builder Public API allows developers to programmatically execute AI agent workflows. Build applications that leverage the decentralized miner network for scalable AI execution.

### Key Features

- 🔐 **Secure Authentication**: Optional Bittensor coldkey signature-based authentication
- ⚖️ **Fair Access**: Proportional rate limiting based on alpha stake
- 🚀 **High Performance**: Distributed miner network for scalable AI execution
- 📊 **Graph Execution**: Execute complex multi-agent DAG workflows

---

## 📚 Full API Documentation

For complete API documentation, code examples, authentication guides, and endpoint references:

### 👉 **[https://agentbuilder80.com/index.html#/docs](https://agentbuilder80.com/index.html#/docs)**

The full documentation includes:

- **Getting Started** - Prerequisites and installation
- **Authentication** - Bittensor wallet signing guide
- **Rate Limiting** - Stake-based rate limit system
- **API Endpoints** - All available endpoints with examples
- **Workflow Export** - How to export and execute workflows
- **Code Examples** - Python examples for common use cases

---

## Quick Start

### 1. Install Dependencies

```bash
pip install bittensor requests
```

### 2. Execute a Simple Workflow

```python
import requests

payload = {
  "workflow": {
    "workflow_id": "workflow_example",
    "nodes": [
      {
        "id": "node_1",
        "type": "user",
        "dependencies": [],
        "user_query": "Hello, how are you?"
      },
      {
        "id": "node_2",
        "type": "component",
        "dependencies": ["node_1"],
        "component": "complete",
        "coldkey": "5Hata2bXMw44DtDxRcL6wTY44AZcsezh2UeAZiEm6yBkGHc9",
        "task": "Generate a helpful response",
        "use_conversation_history": True,
        "use_playbook": True
      }
    ]
  },
  "cid": "conv_example"
}

response = requests.post(
    "https://agent-builder-agent-builder-dev-api.hf.space/orchestrate/execute",
    headers={"Content-Type": "application/json"},
    json=payload,
    timeout=180
)

result = response.json()
for end_node in result["end_node_outputs"]:
    print("AI Response:", end_node["output"]["immediate_response"])
```

---

## Resources

| Resource | URL |
|----------|-----|
| **Full API Docs** | [https://agentbuilder80.com/index.html#/docs](https://agentbuilder80.com/index.html#/docs) |
| **Builder UI** | [https://agentbuilder80.com](https://agentbuilder80.com) |
| **Miner Dashboard** | [https://agentbuilder80.com/index.html#/monitor](https://agentbuilder80.com/index.html#/monitor) |

---

## ⚠️ Important Disclaimers

- **Research Platform**: This is an experimental research tool
- **No Guarantees**: No guarantees of availability or performance
- **Not Investment Advice**: Staking is for resource allocation, not financial investment
- **Your Responsibility**: You are responsible for your usage and decisions

---

**Version:** 3.0.0  
**Last Updated:** December 8, 2025
