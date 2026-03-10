
import json
import sys
from pathlib import Path
from decimal import Decimal

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from bot_v2.models.strategy_config import StrategyConfig

def verify_config():
    config_path = PROJECT_ROOT / "config" / "strategy_configs.json"
    with open(config_path, "r") as f:
        data = json.load(f)
    
    print(f"Verifying {len(data)} symbols in config...")
    for symbol, params in data.items():
        try:
            config = StrategyConfig.from_dict(symbol, params)
            print(f"✅ {symbol}: Loaded successfully.")
            print(f"   - Grid Enabled: {config.grid_enabled}")
            print(f"   - Spacing: {config.grid_spacing_pct}")
            print(f"   - Leverage: {config.leverage}")
        except Exception as e:
            print(f"❌ {symbol}: Failed to load! Error: {e}")
            sys.exit(1)

if __name__ == "__main__":
    verify_config()
