"""
Grid analysis for arbitrage controller - finds optimal entry points using grid strategy.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from decimal import Decimal

import numpy as np

logger = logging.getLogger(__name__)


class GridAnalyzer:
    """
    Analyzes price grids to find optimal arbitrage entry points.
    Helps determine best price levels for entering arbitrage positions.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize grid analyzer.
        
        Args:
            config: Configuration dict with grid_analysis settings
        """
        grid_config = config.get("grid_analysis", {})
        self.enabled = grid_config.get("enabled", False)
        self.num_levels = grid_config.get("num_levels", 10)
        self.spread_percentage = grid_config.get("spread_percentage", 0.01)
        
        self.exchange1 = config.get("exchange_pair_1", {}).get("connector_name", "")
        self.pair1 = config.get("exchange_pair_1", {}).get("trading_pair", "")
        self.exchange2 = config.get("exchange_pair_2", {}).get("connector_name", "")
        self.pair2 = config.get("exchange_pair_2", {}).get("trading_pair", "")
        
        self.current_price1 = None
        self.current_price2 = None
        self.grid_levels = []
    
    async def analyze(self, price1: float, price2: float, order_book1: List, order_book2: List) -> Dict[str, Any]:
        """
        Perform grid analysis to find optimal entry points.
        
        Args:
            price1: Current price on exchange 1
            price2: Current price on exchange 2
            order_book1: Order book for exchange 1
            order_book2: Order book for exchange 2
        
        Returns:
            Dict with analysis results
        """
        if not self.enabled:
            return {"enabled": False, "message": "Grid analysis disabled"}
        
        self.current_price1 = price1
        self.current_price2 = price2
        
        # Generate price grids
        grid1 = self._generate_price_grid(price1, self.num_levels, self.spread_percentage)
        grid2 = self._generate_price_grid(price2, self.num_levels, self.spread_percentage)
        
        # Calculate arbitrage opportunities at each grid level
        opportunities = []
        
        for level1 in grid1:
            for level2 in grid2:
                # Calculate spread
                spread = abs(level1 - level2) / level1
                
                # Check if price1 is lower (buy on 1, sell on 2)
                if level1 < level2:
                    direction = "buy_ex1_sell_ex2"
                    profitability = (level2 - level1) / level1
                else:
                    direction = "buy_ex2_sell_ex1"
                    profitability = (level1 - level2) / level2
                
                # Check liquidity at these price levels
                liquidity1 = self._check_liquidity(order_book1, level1)
                liquidity2 = self._check_liquidity(order_book2, level2)
                
                opportunities.append({
                    "price1": level1,
                    "price2": level2,
                    "spread": spread,
                    "profitability": profitability,
                    "direction": direction,
                    "liquidity1": liquidity1,
                    "liquidity2": liquidity2,
                    "feasible": profitability > 0 and liquidity1 > 0 and liquidity2 > 0
                })
        
        # Sort by profitability
        opportunities.sort(key=lambda x: x["profitability"], reverse=True)
        
        # Calculate optimal grid level
        optimal = self._find_optimal_level(opportunities)
        
        self.grid_levels = opportunities[:self.num_levels]
        
        return {
            "enabled": True,
            "current_prices": {
                "exchange1": price1,
                "exchange2": price2
            },
            "spread": abs(price1 - price2) / price1 if price1 > 0 else 0,
            "optimal_entry": optimal,
            "grid_levels": self.grid_levels[:5],  # Top 5 opportunities
            "num_levels_analyzed": len(opportunities),
            "feasible_opportunities": sum(1 for o in opportunities if o["feasible"])
        }
    
    def _generate_price_grid(self, base_price: float, num_levels: int, spread_pct: float) -> List[float]:
        """Generate price grid around base price."""
        grid = []
        for i in range(-num_levels // 2, num_levels // 2 + 1):
            price = base_price * (1 + i * spread_pct)
            grid.append(price)
        return sorted(grid)
    
    def _check_liquidity(self, order_book: List, target_price: float) -> float:
        """Check liquidity at target price level."""
        if not order_book:
            return 0.0
        
        # Sum amounts up to target price
        total_volume = 0
        for price, amount in order_book:
            if price <= target_price:
                total_volume += amount
            else:
                break
        
        return total_volume
    
    def _find_optimal_level(self, opportunities: List[Dict]) -> Optional[Dict]:
        """Find optimal grid level balancing profitability and liquidity."""
        feasible = [o for o in opportunities if o["feasible"]]
        
        if not feasible:
            return None
        
        # Score each opportunity: profitability * sqrt(liquidity)
        for o in feasible:
            min_liquidity = min(o["liquidity1"], o["liquidity2"])
            o["score"] = o["profitability"] * np.sqrt(min_liquidity)
        
        # Return highest score
        return max(feasible, key=lambda x: x["score"])
    
    def get_recommended_amount(self, optimal_level: Dict, total_capital: float) -> float:
        """
        Get recommended trade amount based on grid analysis.
        
        Args:
            optimal_level: Optimal grid level from analyze()
            total_capital: Total capital in quote asset
        
        Returns:
            Recommended trade amount
        """
        if not optimal_level:
            return total_capital * 0.1  # Conservative default
        
        # Use liquidity constraints
        max_liquidity = min(optimal_level["liquidity1"], optimal_level["liquidity2"])
        
        # Don't use more than 20% of available liquidity
        safe_amount = max_liquidity * 0.2
        
        # Don't exceed total capital
        return min(safe_amount, total_capital)
    
    def should_enter_position(self, analysis: Dict, min_profitability: float) -> Tuple[bool, str]:
        """
        Determine if should enter position based on grid analysis.
        
        Returns:
            (should_enter, reason)
        """
        if not self.enabled:
            return True, "Grid analysis disabled, using standard logic"
        
        if not analysis.get("optimal_entry"):
            return False, "No optimal entry found in grid analysis"
        
        optimal = analysis["optimal_entry"]
        current_spread = analysis.get("spread", 0)
        
        # Check if optimal profitability meets minimum
        if optimal["profitability"] >= min_profitability:
            return True, f"Optimal grid entry found with {optimal['profitability']*100:.2f}% profit"
        
        # Check if current spread is better than optimal
        if current_spread >= min_profitability:
            return True, f"Current spread ({current_spread*100:.2f}%) meets minimum"
        
        return False, f"No profitable entry found. Best: {optimal['profitability']*100:.2f}% < {min_profitability*100:.2f}%"
    
    def get_entry_prices(self, analysis: Dict) -> Tuple[Optional[float], Optional[float]]:
        """
        Get recommended entry prices from grid analysis.
        
        Returns:
            (price1, price2) for entry
        """
        if not self.enabled or not analysis.get("optimal_entry"):
            return None, None
        
        optimal = analysis["optimal_entry"]
        return optimal["price1"], optimal["price2"]


async def run_grid_analysis(
    config: Dict[str, Any],
    price1: float,
    price2: float,
    order_book1: List,
    order_book2: List
) -> Dict[str, Any]:
    """
    Run grid analysis and return results.
    
    Args:
        config: Controller configuration
        price1: Current price on exchange 1
        price2: Current price on exchange 2
        order_book1: Order book for exchange 1
        order_book2: Order book for exchange 2
    
    Returns:
        Analysis results dict
    """
    analyzer = GridAnalyzer(config)
    return await analyzer.analyze(price1, price2, order_book1, order_book2)
