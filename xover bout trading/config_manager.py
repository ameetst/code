import json
import os

CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "crossover_enable_p52h": True,
    "crossover_threshold": 0.75,
    "crossover_enable_lookback": True,
    "crossover_max_lookback": 21,
    "crossover_enable_liquidity": True,
    "breakout_enable_p52h": True,
    "breakout_p52h_thresh": 0.95,
    "breakout_enable_p6mh": True,
    "breakout_p6mh_thresh": 0.95,
    "breakout_enable_sharpe": True,
    "breakout_rfr": 6.0,
    "breakout_sharpe_percentile": 50,
    "breakout_enable_liquidity": True
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
                config = DEFAULT_CONFIG.copy()
                config.update(data)
                return config
        except Exception:
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()

def save_config(config_dict):
    config = load_config()
    config.update(config_dict)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)
