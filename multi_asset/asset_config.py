"""
Per-Asset Configuration for Mr Martingale Multi-Asset Grid Bot

Each coin gets its own config, state, log, and command queue.
This module generates per-asset configs from optimized parameters.
"""
import json
import os
from pathlib import Path

MULTI_ASSET_DIR = Path(__file__).parent
CONFIGS_DIR = MULTI_ASSET_DIR / "configs"
STATE_DIR = MULTI_ASSET_DIR / "state"
LOGS_DIR = MULTI_ASSET_DIR / "logs"
CMDS_DIR = MULTI_ASSET_DIR / "commands"

# Optimized parameters per asset (from walk-forward optimization)
ASSET_PARAMS = {
    'ETH': {
        'coin': 'ETH',
        'ema_span': 55,
        'ma_period': 21,
        'ma_type': 'sma',
        'long_trigger_pct': 0.3,
        'short_trigger_pct': 1.5,
        'tp_pct': 0.3,
        'max_hold_bars': 20,
        'leverage_long': 20,
        'leverage_short': 15,
        'max_leverage': 25,
        'sz_decimals': 4,
        'candle_interval': '4h',
        'base_margin_pct': 0.016,
        'num_levels': 5,
        'multiplier': 2.0,
        'level_gaps': [0.5, 1.5, 3.0, 3.0],
    },
    'SOL': {
        'coin': 'SOL',
        'ema_span': 21,
        'ma_period': 14,
        'ma_type': 'sma',
        'long_trigger_pct': 0.3,
        'short_trigger_pct': 1.5,
        'tp_pct': 0.3,
        'max_hold_bars': 20,
        'leverage_long': 20,
        'leverage_short': 15,
        'max_leverage': 20,
        'sz_decimals': 2,
        'candle_interval': '4h',
        'base_margin_pct': 0.016,
        'num_levels': 5,
        'multiplier': 2.0,
        'level_gaps': [0.5, 1.5, 3.0, 3.0],
    },
    'XRP': {
        'coin': 'XRP',
        'ema_span': 55,
        'ma_period': 21,
        'ma_type': 'sma',
        'long_trigger_pct': 0.5,
        'short_trigger_pct': 1.5,
        'tp_pct': 0.3,
        'max_hold_bars': 20,
        'leverage_long': 20,
        'leverage_short': 15,
        'max_leverage': 20,
        'sz_decimals': 0,
        'candle_interval': '4h',
        'base_margin_pct': 0.016,
        'num_levels': 5,
        'multiplier': 2.0,
        'level_gaps': [0.5, 1.5, 3.0, 3.0],
    },
}


def generate_coin_config(coin: str) -> dict:
    """Generate full runtime config for a specific coin."""
    if coin not in ASSET_PARAMS:
        raise ValueError(f"No config for {coin}. Available: {list(ASSET_PARAMS.keys())}")

    params = ASSET_PARAMS[coin]

    # Compute cumulative drops
    cum_drops = []
    acc = 0.0
    for g in params['level_gaps']:
        acc += g
        cum_drops.append(acc / 100)

    return {
        **params,
        'cum_drops': cum_drops,
        'state_file': str(STATE_DIR / f"grid_state_{coin}.json"),
        'log_file': str(LOGS_DIR / f"grid_bot_{coin}.log"),
        'command_dir': str(CMDS_DIR / coin),
        'paper_trade': True,  # ALWAYS paper trade for new assets
        'poll_seconds': 300,
        'taker_fee': 0.000432,
        'maker_fee': 0.000144,
    }


def save_coin_config(coin: str):
    """Save coin config as JSON for runtime use."""
    cfg = generate_coin_config(coin)
    CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    path = CONFIGS_DIR / f"{coin}.json"
    with open(path, 'w') as f:
        json.dump(cfg, f, indent=2)
    print(f"Saved config: {path}")
    return path


def save_all_configs():
    """Generate and save configs for all eligible coins."""
    for coin in ASSET_PARAMS:
        save_coin_config(coin)


if __name__ == '__main__':
    save_all_configs()
