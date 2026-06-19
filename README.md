# inbody-api-mcp

<!-- mcp-name: io.github.rwestergren/inbody-api-mcp -->

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/rwestergren/inbody-api-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/rwestergren/inbody-api-mcp/actions/workflows/ci.yml)
[![Build Docker image](https://github.com/rwestergren/inbody-api-mcp/actions/workflows/docker.yml/badge.svg)](https://github.com/rwestergren/inbody-api-mcp/actions/workflows/docker.yml)
[![PyPI](https://img.shields.io/pypi/v/inbody-api-mcp.svg)](https://pypi.org/project/inbody-api-mcp/)

> **Hosted version for Claude.ai, ChatGPT, and Grok coming soon.** [**Join the waitlist →**](https://tally.so/r/A7WVge?ref=inbody-api-mcp)

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server for
[InBody](https://inbody.com/) body-composition data, built on the
reverse-engineered mobile REST API used by the InBody Android app.

InBody has no public API and no web UI for personal scan data. This server talks
to the same JSON REST endpoints the mobile app uses, exposing your body
composition history (body fat, muscle mass, body water, segmental impedance) to
any MCP client.

## Features

- **Profile** -- identity and baseline metrics (height, weight, age, gender)
- **Scan history** -- chronological summaries (weight, BMI, % body fat, muscle mass)
- **Full scan metrics** -- complete body composition (BCA), BMI/%fat/muscle with
  normal ranges (MFA), and segmental/multi-frequency impedance (IMP)
- **Automatic region routing** -- resolves the correct regional API host from
  your country code
- **Automatic re-authentication** -- caches the 24h JWT and re-logs in on expiry

## Quick Start

### 1. Install [uv](https://docs.astral.sh/uv/)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Set credentials

```bash
export INBODY_LOGIN_ID="3026323617"   # registration phone number, digits only
export INBODY_LOGIN_PW="your-password"
export INBODY_COUNTRY_CODE="US"       # ISO country code (default US)
```

> **`INBODY_LOGIN_ID` is the phone number used at registration**, not your email
> -- digits only, no country code or `+` (e.g. `3026323617`). InBody keys login
> on the phone number; the email is only returned as profile data. An email
> value will fail login with `EmptyData`.

### 3. Configure your MCP client

`uvx` downloads and runs the server on demand -- no separate install step.

#### OpenCode (`opencode.json`)

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "inbody": {
      "type": "local",
      "command": ["uvx", "inbody-api-mcp"],
      "environment": {
        "INBODY_LOGIN_ID": "{env:INBODY_LOGIN_ID}",
        "INBODY_LOGIN_PW": "{env:INBODY_LOGIN_PW}",
        "INBODY_COUNTRY_CODE": "US"
      },
      "enabled": true
    }
  }
}
```

#### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "inbody": {
      "command": "uvx",
      "args": ["inbody-api-mcp"],
      "env": {
        "INBODY_LOGIN_ID": "3026323617",
        "INBODY_LOGIN_PW": "your-password",
        "INBODY_COUNTRY_CODE": "US"
      }
    }
  }
}
```

## Available Tools

| Tool | Description |
|------|-------------|
| `get_profile` | User identity and baseline metrics (height, weight, age, gender) |
| `get_scan_count` | Total number of scans on the account |
| `list_scans` | Chronological scan summaries (weight, BMI, % body fat, muscle mass) |
| `get_scan` | Full metric set for one scan (BCA / MFA / IMP blocks) |

This server is read-only: no write or delete endpoints are exposed.

## How It Works

This server communicates with the regional `*.lookinbody.com` REST API -- the
same backend used by the InBody Android app (v2.8.31). The API was
reverse-engineered by capturing app traffic with mitmproxy and confirming
payload shapes against the live API.

The auth flow:

1. `POST /CommonAPI/GetCountryInfoV2` (on `appapicommon.lookinbody.com`) returns
   a per-country host table. The `Type == "API"` row for your ISO country code
   gives the regional API base (US -> `appapiusav2.lookinbody.com`) and the
   numeric phone code used in request bodies.
2. `POST /V2/Main/GetLoginWithSyncDataPartV2` exchanges the login ID + password
   for a 24-hour JWT, a refresh token, and the account UID.
3. Subsequent calls send `Authorization: Bearer <JWT>`. The client
   re-authenticates automatically when the token expires.

Each scan record nests three blocks: **BCA** (body composition analysis -- body
water, protein, mineral, fat, segmental water), **MFA** (BMI, % body fat,
skeletal muscle mass, WHR with normal ranges), and **IMP** (raw impedance per
frequency and body segment).

## Python API

You can use the client directly:

```python
from inbody_api_mcp.client import InBodyClient

client = InBodyClient()

# Total number of scans
count = client.get_scan_count()

# Recent scans (newest first), paginated
scans = client.get_scans(number=20, index=0)

# User profile
profile = client.get_user_info()
```

## Transport

stdio only. MCP clients (OpenCode, Claude Desktop) spawn the stdio process
directly via `uvx`/`uv`.

## License

MIT
