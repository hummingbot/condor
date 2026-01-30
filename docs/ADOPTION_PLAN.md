# Condor Adoption Plan

**Focus: Easy setup, community contributions, shareable examples**

---

## 1. Ease of Installation

### Current State
- Requires: Python 3.12+, Conda, Hummingbot Backend API, Telegram Bot Token
- Multiple steps: clone, make install, configure .env, run

### Goal: 5-Minute Setup

#### One-Line Install Script
```bash
curl -fsSL https://raw.githubusercontent.com/hummingbot/condor/main/install.sh | bash
```

The script should:
1. Check prerequisites (Python, Docker)
2. Clone the repo
3. Prompt for Telegram token interactively
4. Create `.env` file
5. Start the bot

#### Docker-First Approach
```bash
# Single command to run
docker run -d \
  -e TELEGRAM_TOKEN=your_token \
  -e ADMIN_USER_ID=your_id \
  -v condor_data:/app/data \
  hummingbot/condor
```

#### Telegram-Based Onboarding
After `/start`, guide users through setup:
```
Welcome to Condor! Let's get you set up.

Step 1/3: Connect a Hummingbot server
Enter your server URL (e.g., http://localhost:8000):
```

### Installation Documentation

Create `docs/QUICK_START.md`:
```markdown
# Quick Start (5 minutes)

## Option 1: Docker (Recommended)
\`\`\`bash
docker run -d -e TELEGRAM_TOKEN=xxx hummingbot/condor
\`\`\`

## Option 2: Local Python
\`\`\`bash
pip install condor-bot
condor --setup
\`\`\`

## Option 3: From Source
\`\`\`bash
git clone https://github.com/hummingbot/condor
cd condor && make install && make run
\`\`\`
```

---

## 2. Community Contributions

### Routines Marketplace

The existing `/routines` system is perfect for contributions. Make it easy:

#### Structure
```
routines/
├── community/           # Git submodule or separate repo
│   ├── whale_alerts.py
│   ├── funding_scanner.py
│   └── dca_bot.py
├── examples/            # Bundled examples
│   ├── hello_world.py
│   ├── price_alert.py
│   └── arb_checker.py
└── README.md            # How to contribute
```

#### Contributing Guide (`routines/CONTRIBUTING.md`)
```markdown
# Contributing Routines

## Create a Routine

1. Create a Python file in `routines/`
2. Add a `Config` class with Pydantic
3. Implement `async def run(config, context)`
4. Submit a PR!

## Template
\`\`\`python
"""
Brief description of what this routine does.
Author: @your_github
"""
from pydantic import BaseModel, Field

class Config(BaseModel):
    """Configuration shown to users."""
    threshold: float = Field(5.0, description="Alert threshold %")

async def run(config: Config, context) -> str:
    # Your logic here
    return "Result message"
\`\`\`

## Guidelines
- Include docstring with description
- Use type hints
- Handle errors gracefully
- Test locally before submitting
```

#### Featured Routines
Curate and highlight community contributions:
- Pin top routines in README
- Add "Featured" tag in `/routines` menu
- Credit authors: "by @username"

### Handler Plugins (Future)

Allow community-built command handlers:
```
handlers/
├── community/
│   ├── whale_watch/     # /whale command
│   ├── fear_greed/      # /sentiment command
│   └── gas_tracker/     # /gas command
```

### GitHub Templates

Add issue/PR templates:

`.github/ISSUE_TEMPLATE/feature_request.md`:
```markdown
## Feature Request

**What problem does this solve?**

**Proposed solution:**

**Are you willing to contribute this?**
- [ ] Yes, I can submit a PR
- [ ] I need help implementing this
```

`.github/ISSUE_TEMPLATE/routine_submission.md`:
```markdown
## New Routine Submission

**Routine name:**
**Description:**
**Use case:**

**Checklist:**
- [ ] Follows template structure
- [ ] Includes Config class
- [ ] Tested locally
- [ ] No API keys hardcoded
```

---

## 3. Social Media Examples

### Screenshot-Ready Features

Design outputs that look good when shared:

#### Portfolio Summary Card
```
┌─────────────────────────────┐
│  📊 CONDOR PORTFOLIO        │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━  │
│                             │
│  Total: $42,847             │
│  24h:   +$892 (+2.1%)  📈   │
│                             │
│  BTC   $21,420  (50%)       │
│  ETH   $12,854  (30%)       │
│  SOL   $8,573   (20%)       │
│                             │
│  🤖 3 bots running          │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━  │
│  condor.hummingbot.org      │
└─────────────────────────────┘
```

#### Bot Performance Card
```
┌─────────────────────────────┐
│  🤖 PMM-ETH Performance     │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━  │
│                             │
│  Running: 7 days            │
│  Trades:  1,247             │
│  Volume:  $89,420           │
│                             │
│  Profit:  +$342.50          │
│  ROI:     +3.4%             │
│                             │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━  │
│  Powered by Condor 🦅       │
└─────────────────────────────┘
```

#### Alert Screenshot
```
┌─────────────────────────────┐
│  🚨 PRICE ALERT             │
│                             │
│  ETH broke $3,500!          │
│  Current: $3,512 (+4.2%)    │
│                             │
│  Your holdings: 2.5 ETH     │
│  Value: $8,780              │
│                             │
│  [View Portfolio]           │
└─────────────────────────────┘
```

### Export Commands

Add `/export` command for shareable images:
```
/export portfolio   → PNG of portfolio card
/export bot PMM-ETH → PNG of bot performance
/export pnl 30d     → PNG of 30-day PnL chart
```

### Example Use Cases for Social

Create a `docs/EXAMPLES.md` with real scenarios:

```markdown
# Condor Use Cases

## 1. Morning Portfolio Check
> "I wake up, check Telegram, and Condor already summarized
> my overnight PnL and bot performance."

Screenshot: [morning_briefing.png]

## 2. Price Alert While AFK
> "Was in a meeting when ETH pumped. Condor alerted me
> and I closed my short from my phone."

Screenshot: [price_alert.png]

## 3. Bot Monitoring
> "My market making bot had an error at 3am.
> Condor notified me and I fixed it before losing money."

Screenshot: [bot_error_alert.png]

## 4. Quick Trade from Anywhere
> "Saw alpha on Twitter, opened Telegram, typed
> 'buy $500 SOL' and Condor executed it in seconds."

Screenshot: [quick_trade.png]
```

### Social Proof Section in README

Add a "Community" section:
```markdown
## Community

### Screenshots
<img src="docs/screenshots/portfolio.png" width="300">
<img src="docs/screenshots/trading.png" width="300">

### User Stories
> "Condor replaced my trading terminal for 90% of tasks"
> — @trader_handle

> "Finally, a self-hosted alternative to paid trading bots"
> — @defi_user

### Share Your Setup
Tag us with #CondorBot to be featured!
```

---

## 4. Implementation Checklist

### Easy Installation
- [ ] Create `install.sh` one-line installer
- [ ] Publish Docker image to Docker Hub
- [ ] Add `pip install condor-bot` option
- [ ] Create interactive Telegram onboarding flow
- [ ] Write `docs/QUICK_START.md`

### Community Contributions
- [ ] Create `routines/CONTRIBUTING.md`
- [ ] Add example routines with good documentation
- [ ] Set up GitHub issue templates
- [ ] Create `routines/community/` structure
- [ ] Add "Featured Routines" to `/routines` menu

### Shareable Examples
- [ ] Design screenshot-ready message formats
- [ ] Add `/export` command for PNG generation
- [ ] Create `docs/EXAMPLES.md` with use cases
- [ ] Add screenshots to README
- [ ] Create social media templates

---

## 5. README Improvements

Update README with:

```markdown
## Quick Start

### 1. Get a Telegram Bot Token
1. Message @BotFather on Telegram
2. Send `/newbot` and follow prompts
3. Copy the token

### 2. Run Condor
\`\`\`bash
# Docker (easiest)
docker run -d -e TELEGRAM_TOKEN=your_token -e ADMIN_USER_ID=your_id hummingbot/condor

# Or with pip
pip install condor-bot && condor --setup
\`\`\`

### 3. Connect to Hummingbot
Message your bot `/servers` and add your Hummingbot API server.

**That's it!** Use `/portfolio` to see your balances.

## Examples

| Use Case | Screenshot |
|----------|------------|
| Portfolio Dashboard | ![](docs/img/portfolio.png) |
| Place a Trade | ![](docs/img/trade.png) |
| Bot Monitoring | ![](docs/img/bots.png) |
| AI Chat | ![](docs/img/chat.png) |

## Contributing

We welcome contributions! See:
- [Contributing Guide](CONTRIBUTING.md)
- [Routine Development](routines/CONTRIBUTING.md)
- [Good First Issues](https://github.com/hummingbot/condor/labels/good%20first%20issue)
```

---

## Summary

| Focus Area | Key Actions |
|------------|-------------|
| **Easy Install** | One-line script, Docker image, pip package |
| **Contributions** | Routines marketplace, templates, clear guides |
| **Examples** | Screenshot-ready outputs, export commands, use case docs |

The goal is reducing friction at every step: install → configure → use → share → contribute.
