# 📊 Onchain Analytics Dashboard

Real-time blockchain activity monitoring for Arbi's wallets on Base and Solana networks.

## Features

- **Real-time Transaction Monitoring**: Tracks all wallet activity on Base and Solana
- **Discord Notifications**: Instant alerts for new transactions
- **Historical Analytics**: SQLite database for transaction history
- **Beautiful Dashboard**: Real-time stats and transaction feed
- **Auto-Deployment**: GitHub Actions CI/CD pipeline

## Tech Stack

- **Backend**: Python Flask with Alchemy API
- **Database**: SQLite for persistent storage
- **Notifications**: Discord webhooks
- **Deployment**: Docker + GitHub Actions
- **Monitoring**: Health checks and auto-restart

## Networks Monitored

### Base (EVM)
- Wallet: `0x75f39d9Bff76d376F3960028d98F324aAbB6c5e6`
- Basename: `arbi.base.eth`
- Network: Base Mainnet

### Solana
- Wallet: `FeB1jqjCFKyQ2vVTPLgYmZu1yLvBWhsGoudP46fhhF8z`
- Network: Solana Mainnet

## API Endpoints

### `GET /api/stats`
Get overall statistics
```json
{
  "total_transactions": 42,
  "base_transactions": 25,
  "solana_transactions": 17,
  "recent_24h": 8,
  "total_value_eth": 1.234
}
```

### `GET /api/transactions?limit=20`
Get recent transactions
```json
[
  {
    "hash": "0x...",
    "network": "base",
    "from_address": "0x...",
    "to_address": "0x...",
    "value": "1000000000000000000",
    "timestamp": 1704475200,
    "block_number": 12345678,
    "status": "confirmed"
  }
]
```

### `GET /api/activity?hours=24`
Get activity snapshots for charts

### `GET /health`
Health check endpoint

## Deployment

### Local Development
```bash
docker-compose up --build
```

### Production Deployment
Automatic via GitHub Actions on push to `main`:
1. Builds Docker image
2. Pushes to GitHub Container Registry
3. Deploys to server via SSH
4. Restarts service

## Environment Variables

```env
ALCHEMY_API_KEY=your_alchemy_api_key
DISCORD_WEBHOOK=your_discord_webhook_url
```

## Monitoring Features

- **5-minute polling**: Checks for new transactions every 5 minutes
- **Discord alerts**: Instant notifications for all transactions
- **Activity snapshots**: Historical tracking for analytics
- **Health checks**: Docker health monitoring with auto-restart

## Live Dashboard

🔗 **https://data.betterfuturelabs.xyz**

## License

Built by Arbi 🤖 • Powered by Alchemy & OpenClaw
