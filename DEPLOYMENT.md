# Grid Bot Deployment Guide

Complete guide for deploying and operating the Grid Trading Bot in production.

## Prerequisites

- Docker & Docker Compose installed
- GitHub repository with Actions enabled
- API keys from your exchange (Binance, etc.)
- (Optional) Telegram Bot Token for alerts

## Local Development

### 1. Setup Environment

```bash
# Clone repository
git clone <your-repo-url>
cd Grid_Bot

# Copy environment template
cp .env.example .env

# Edit .env with your credentials
nano .env
```

### 2. Install Dependencies

```bash
# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install packages
pip install -r requirements.txt
```

### 3. Run Tests

```bash
# Run full test suite
pytest tests/ -v

# With coverage
pytest tests/ -v --cov=src --cov-report=term-missing
```

### 4. Run Locally

```bash
# Using shell script
bash run_grid_bot.sh

# Or directly
python main.py
```

## Docker Deployment

### Quick Start

```bash
# Build and r# Build and r# Bompose
dockerdockerdockerdockerdockerdockerdockerdockerdockerdockerdockerdockerdockerdockeces
docker-compose dodocker-compose dodocker-compose 

`````````````````````````````````````````d-```````````````````````````````````````n -d \
````````````````````````````````````````` -v```````````````````ata \
  -v $(pwd)/logs:/app/logs \
                  
# View logs
docker logs -f gdockeot

# St# St# St# St# St# St# St# ri# St# St# St# St# Sd-b# St# St# St# St# St# St# St# nt

### 1. Server S### 1. Server S### 1. Ssystem
sudo apsudo apsudo apsudo apsudo ae sudo apsudo apsudo apsurlsudo apsudo apsudo apsudo apsudo ae sudo apsudo apsudo apsurlsker.sh

# Install Docker Compose
sudo apt install docker-compose-plugin

# Create bot user
sudo useradd -m -s /bin/bash gridbot
sudo usermod -aG docker gridbot
```

### 2. Deploy Application

```bash```bash```bo bot user
sudo ssudo ssudo s
# Clone # Clone # Cloneclone <# Clone # Cll># Clone # Clod # gri# Clone # Cloneenviron# Clone # Clonxam# Clonnv# Clone # Clone # Cloneclone <#ion v# Clone # Clone # Cloneclone <# Clone # Cll># Clone # Clod # gri# Clone # Cloneenvirosystemd service:

```bash
sudo nano /etc/systemd/systsudo nano /etc/systemd/systsudo nano /etc/systemd/systsudo nano /etc/systemd/systsudo nersudo nano /etc/systemd/systsudo nano /etc/systemd/systsudo nano /exit=yes
WorkingDirectory=/home/gridbot/grid-bot
ExecStart=/usr/bin/docker-compose up -d
ExecStart=/usr/bin/docker-compose up -d
c/systemd/systsudo nano /etc/systemd/systsudomulti-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable grid-bot
sudo systemctl start grid-bot
sudo systemctl status grid-bot
```

## ## ## ## ## ## ## ## ##ns## ## ## ## ## ## ## ## ##ns## ## ##o: `## ## ## ## ecrets## ## ## ## ## ## ## ## ##ns## ## `DO## ## ## ## ## ## ##ker Hub username ## ## ## ## ## ## #R_TOKEN`## ## ## ## ## ## ## ## #n (o## ## ## ## ## ECOV## ## ## ## ## ## ## ## ##ns## ## ## ## ## ## ## ## ##ns## ## ##o: `## ## ## ## ecrets## ## ## ## ## ## ## ## ##ns## ## `DO## ## ## ## ## ## ##ker Hub username ## ## ## ## ## ## #R_TOKEN`## ## ## ## ## ## ## ## #n (o## ## ## ## ## ECOV## ## ## #Runs ## ##  (Monday 00:00 UTC):
- Historical backtest execution
- Performance report generation
- Results archived for 90 days

### Manual Workflow Dispatch

```bash
# Trigger backtest manually
gh workflow run backtest.yml
```

## Monitoring & Observability

### Access Dashboards

---------------------------------------dm---------------------------------------dm------------------**: local---------------ongoDB**: lo--------------------------- L---------------------------se
dddddddddddddddddddddddddddddotddddddddddddddddddddddddddddkerddogsdddddddddddddddddddddddddddddotddddddddddddddddddddddddddddkerddogsdddddddddddddddddddddddddddddotddddddddddddddddddddddddddddkerddogsdddddddddddddddddddddddddddddotddddddddddddddddddddddddddddkerddogsdddddddddddddddddddddddddddddotddddddddddddddddddddddddddddkerddogsdddddddddddddddddddddddddddddotddddddddddddddddddddddddddddkerddogsdddddddddddddddddddddddddddddotddddddddddddddddddddddddddddkerddogsdddddddddddddddddddddddddddddotddddddddddddddddddddddddddddkerddogsdddddddddddddddddddddddddddddotdddddddddd cp grid-bot-mongo:/data/backup ./mongo-backup-$(date +%Y%m%d)
```

### Restore State

```bash
# Restore data directory
tar -xzf backup-20260222.tar.gz

# Restore MongoDB
docker cp ./mongo-backup grid-bot-mongo:/data/restore
docker exec grid-bot-mongo mongorestore /data/restore
```

## Security Best Practices

### 1. API Key Management

```bash
# NEVER commit .env file
echo ".env" >> .gitignore

# Use environment-specific files
.env.production
.env.staging
.env.development
```

### 2. Network Security

```bash
# Firewall rules (UFW)
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp  # SSH
sudo ufw allow 3000/tcp  # Grafana (if needed)
sudo ufw enable
```

### 3. Update Dependencies

```bash
# Check for vulnerabilities
pip-audit

# Update packages
pip install --upgrade pip
pip install -r requirements.txt --upgrade
```

## Troubleshooting

### Bot Not Starting

```bash
# Check logs
docker-compose logs grid-bot

# Verify environment
docker exec grid-bot env | grep -E "API|EXCHANGE"

# Test configuration
docker exec grid-bot python -c "from config.settings import dockerttdocker exec grid-bot pys(docker exec grid-bot pythoection docker exec grid# docker exec grid-bot python -c "from config.settithon docker exec grid-bot python -c "from config.settings
    'a    'a    'a    'a    'KEY')    'a    'a    os.getenv('    'aCRET'),
    'enableRateLimit': True
})
print(exchange.fetch_balance())
"
```

### High Memory Usage

```bash
# Check memory usage
docker stats grid-bot

# Restart containe# Restart containe#start grid-bot

# Clear cache
rm -rf data/cache/*
```

## Performance Optimization

### 1. Cache Management

```python
# Clear stale OHLCV cache
find data/cachfiohlcv_cache -type f -mtime +7 -delete
```

### 2. Log Rotation

```bash
# Configure in docker-compose.yml
logging:
  drive  drive  drive  drive  drive  dri-size: "10m"
    max-file: "3"
```

### 3. Database Optimization

```bash
# MongoDB indexes
docker exec grid-bot-mongo mongosh --eval "
use gridbot
db.trades.createIndex({timestamdb.trades.createIndreateIndex({status: 1, created_at: -1})
"
```

## Support

For issues and questions:
- GitHub Issues: <your-repo-url>/issues
- Documentation: <your-docs-url>
- Telegram: <your-support-channel>
