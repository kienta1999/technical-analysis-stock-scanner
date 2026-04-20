# ai-stock-investment

AI-powered stock investment analysis using Claude Code with MCP integrations.

## MCP Servers

### Prerequisites

- [uv](https://github.com/astral-sh/uv) — for `uvx` (Python MCP servers)
- [Node.js](https://nodejs.org) — for `npx` (Node MCP servers)

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### yfinance-mcp

Fetches stock market data — prices, financials, historical OHLCV, etc.

```bash
# Install
uvx yfinance-mcp

# Add to this project
claude mcp add yfinance-mcp uvx yfinance-mcp
```

### playwright

Browser automation — scraping news, financial sites, charts.

```bash
# Install
npx -y @playwright/mcp --browser chromium

# Add to this project
claude mcp add playwright -- npx -y @playwright/mcp --browser chromium
```

### Verify MCP servers are active

```bash
claude mcp list
```
