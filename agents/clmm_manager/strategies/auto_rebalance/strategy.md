---
name: Auto Rebalance
description: 小资金 Meteora DLMM 策略；固定主池、美元定额、严格校验后才允许开仓或调仓
agent_key: null
skills: []
default_config:
  frequency_sec: 300
  total_amount_quote: 1.5
  target_usd: 100
  execution_mode: dry_run
  live_execution_enabled: false
  connector: meteora
  network: solana-mainnet-beta
  pool_address: 6e7V9eegCHw997T72MxgwwJipZ6GJyZF8NvjkzT1rvpN
  trading_pair: ANSEM-SOL
  base_mint: 9cRCn9rGT8V2imeM2BaKs13yhMEais3ruM3rPvTGpump
  quote_mint: So11111111111111111111111111111111111111112
  range_pct: 5.5
  max_range_pct: 6.5
  min_range_pct: 4.0
  max_bins: 68
  min_tvl_usd: 500000
  max_tvl_drop_pct: 50
  max_price_drop_24h_pct: 50
  max_rebalances_24h: 5
  confirmations_required: 2
  rebalance_cooldown_sec: 1800
  risk_limits:
    max_position_size_quote: 1.5
    max_open_executors: 1
    max_drawdown_pct: 5
    shutdown_drawdown_pct: 10
default_trading_context: ''
created_by: 971236605
created_at: '2026-07-21T00:00:00+00:00'
---

# ANSEM/SOL Safe Rebalance

目标是用约 `target_usd` 的小资金在指定 Meteora 主池做双边 LP。策略偏积极地每 5 分钟监控，但不因单次越界立刻关仓。

## 不可覆盖的约束

- 只使用池 `6e7V9eegCHw997T72MxgwwJipZ6GJyZF8NvjkzT1rvpN`。
- base mint 必须是 `9cRCn9rGT8V2imeM2BaKs13yhMEais3ruM3rPvTGpump`，quote mint 必须是 `So11111111111111111111111111111111111111112`。
- 禁止按 `ANSEM` 符号搜索后自动选池；地址或 mint 不一致时必须暂停。
- `target_usd` 是美元预算；`total_amount_quote` 和 `max_position_size_quote` 是 SOL 数量上限。不得把 `$100` 当成 `100 SOL`。
- agent 不得修改 `condor/**` 框架代码。当前框架风险门不能从 LP 的 `base_amount`/`quote_amount` 可靠计算敞口，因此 `live_execution_enabled` 必须保持 `false`。
- 即使外部把 `execution_mode` 改成 `loop`，只要 `live_execution_enabled=false`，也禁止调用 executor 的 `create` 或 `stop`；只生成候选方案。
- 同时最多一个活跃 executor/链上仓位。任何仓位查询失败或状态不明确都视为 `pause`，绝不能视为空仓。
- routine 给出的范围不得超过 68 bins；不要自行扩大成超过主池容量的名义百分比。

## 每个 tick 的只读检查

1. 用 `search_history` 查看最近 24 小时本策略的监控与调仓记录，取得：
   - 最近一个可信 TVL，作为 `reference_tvl_usd`；没有可信值就省略该字段。
   - 已完成调仓次数，作为 `recent_rebalance_count`。
   - 当前 position 是否已连续出现相同的 `rebalance_candidate`。
2. 用 `manage_executors(action="search", executor_types=["lp_executor"], trading_pairs=["ANSEM-SOL"], controller_ids=["<agent_id>"])` 检查 executor，避免与链上仓位重复。
3. 运行监控 routine：

```text
manage_routines(action="run", name="clmm_monitor", strategy_id="clmm_manager.auto_rebalance", config={
  "connector": "<CURRENT CONFIG.connector>",
  "network": "<CURRENT CONFIG.network>",
  "pool_address": "<CURRENT CONFIG.pool_address>",
  "trading_pair": "<CURRENT CONFIG.trading_pair>",
  "base_mint": "<CURRENT CONFIG.base_mint>",
  "quote_mint": "<CURRENT CONFIG.quote_mint>",
  "target_usd": <CURRENT CONFIG.target_usd>,
  "range_pct": <CURRENT CONFIG.range_pct>,
  "max_range_pct": <CURRENT CONFIG.max_range_pct>,
  "min_range_pct": <CURRENT CONFIG.min_range_pct>,
  "max_bins": <CURRENT CONFIG.max_bins>,
  "min_tvl_usd": <CURRENT CONFIG.min_tvl_usd>,
  "reference_tvl_usd": <最近可信值；没有则不要传>,
  "max_tvl_drop_pct": <CURRENT CONFIG.max_tvl_drop_pct>,
  "max_price_drop_24h_pct": <CURRENT CONFIG.max_price_drop_24h_pct>,
  "recent_rebalance_count": <最近24h已完成次数>,
  "max_rebalances_24h": <CURRENT CONFIG.max_rebalances_24h>
})
```

routine 的 `action` 只有四种合法处理：

- `pause`：不创建、不停止、不调仓；通知具体 blockers。
- `hold`：不做写操作。
- `no_position`：仅进入开仓前检查。
- `rebalance_candidate`：仅进入调仓资格检查，不代表可以立刻关仓。

未知 action 一律按 `pause` 处理。

## 开仓计划（只读）

仅当 routine 返回 `no_position` 且 `ready_to_create=true` 时继续：

1. `manage_executors(action="search", ...)` 与 `get_portfolio_overview(include_lp_positions=true)` 必须同时确认没有活跃或状态不明的同池仓位。两者冲突时暂停。
2. 从 routine 的 `target_allocation` 读取 `base_amount` 与 `quote_amount`；用 portfolio 确认钱包同时持有足额 ANSEM 和 SOL，并额外保留 SOL 支付手续费。
3. 如果只有 SOL 或只有 ANSEM，暂停并通知用户补齐双边资产；本策略不自动换币。
4. 调用 `manage_executors(executor_type="lp_executor")` 只读获取实时 schema。schema 与下列字段冲突时暂停，不猜字段。
5. 输出候选参数，供用户检查；不得调用 `manage_executors(action="create")`：

```text
connector_name: solana-mainnet-beta
lp_provider: meteora/clmm
trading_pair: ANSEM-SOL
pool_address: 6e7V9eegCHw997T72MxgwwJipZ6GJyZF8NvjkzT1rvpN
lower_price: <suggested_range.lower>
upper_price: <suggested_range.upper>
base_amount: <target_allocation.base_amount>
quote_amount: <target_allocation.quote_amount>
side: 3
keep_position: true
extra_params: {"strategyType": 0}
```

候选参数必须使用 `base_amount`/`quote_amount`，禁止使用 `amount_quote`。

## 调仓候选评估（只读）

`rebalance_candidate` 必须同时满足以下条件才可关闭旧仓：

- 同一个 position 连续 `confirmations_required` 个 tick 越界；
- 距上次已完成调仓至少 `rebalance_cooldown_sec`；
- 最近 24 小时已完成次数小于 `max_rebalances_24h`；
- pool/mint/价格/TVL/仓位数据全部验证通过；
- 旧 executor ID 唯一且状态可停止；
- 没有另一个 opening/running LP executor。

满足条件后只输出以下建议步骤，不实际执行：

1. 建议关闭唯一旧 executor，并明确 `keep_position=true`。
2. 建议关闭后再次确认 executor 与链上 LP 状态。
3. 建议重新读取实际 ANSEM/SOL 余额后再计算新仓，不套用固定 `$100` 数量。
4. 资产比例不足时建议保持钱包余额，不自动换币。

## dry_run 与通知

- 当前版本始终不调用 create/stop，只输出精确 pool、mint、bins、价格范围和两种币数量。
- 暂停、数据冲突和候选方案都要通知。
- 不把预估手续费/APR 当成收益保证；报告中同时给出 LP 价值、手续费、调仓次数与失败原因。
