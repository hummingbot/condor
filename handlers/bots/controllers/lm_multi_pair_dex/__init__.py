"""
LMMultiPairDEX Controller Module for Condor.

Market making multi-coppia ottimizzato per DEX con order book:
- XRPL DEX (latenza 3-5s, fee ~$0.00001)
- Hyperliquid (latenza 0.2ms, maker rebate -0.01%)
"""

import io
from typing import Any, Dict, List, Optional, Tuple

from .._base import BaseController, ControllerField
from .chart import generate_chart, generate_preview_chart
from .config import DEFAULTS, FIELD_ORDER, FIELDS, WIZARD_STEPS, generate_id, validate_config
from .analysis import analyze_liquidity, format_liquidity_summary


class LMMultiPairDEXController(BaseController):
    controller_type = "lm_multi_pair_dex"
    display_name = "Liquidity Mining Multi-Pair DEX"
    description = "Market making multi-coppia per DEX order book (XRPL, Hyperliquid)"

    @classmethod
    def get_defaults(cls) -> Dict[str, Any]:
        return DEFAULTS.copy()

    @classmethod
    def get_fields(cls) -> Dict[str, ControllerField]:
        return FIELDS

    @classmethod
    def get_field_order(cls) -> List[str]:
        return FIELD_ORDER

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        return validate_config(config)

    @classmethod
    def generate_chart(
        cls,
        config: Dict[str, Any],
        candles_data: List[Dict[str, Any]],
        current_price: Optional[float] = None,
    ) -> io.BytesIO:
        return generate_chart(config, candles_data, current_price)

    @classmethod
    def generate_id(cls, config: Dict[str, Any], existing_configs: List[Dict[str, Any]]) -> str:
        return generate_id(config, existing_configs)

    @classmethod
    def analyze_liquidity(cls, config: Dict[str, Any], market_data: Dict[str, Any]) -> Dict[str, Any]:
        """Analizza liquidità per le coppie configurate."""
        return analyze_liquidity(config, market_data)

    @classmethod
    def format_analysis(cls, analysis: Dict[str, Any]) -> str:
        """Formatta l'analisi per visualizzazione."""
        return format_liquidity_summary(analysis)


__all__ = [
    "LMMultiPairDEXController",
    "DEFAULTS",
    "FIELDS",
    "FIELD_ORDER",
    "WIZARD_STEPS",
    "validate_config",
    "generate_id",
    "generate_chart",
    "generate_preview_chart",
    "analyze_liquidity",
    "format_liquidity_summary",
]
