---
类型: 因子假设
状态: 待验证
创建时间: 2026-06-30
来源: 机油手第6-10课、第12课
---

# K线均线支撑压力因子

## 视频启发

素材覆盖 K线、单根K线形态、均线、支撑压力、左侧和右侧交易。它们都属于价格序列特征，但容易被过拟合。

## 可量化假设

### 假设一：K线形态是 OHLC 压缩特征

把“阳线、阴线、上影线、下影线、实体大小”转成数值特征，而不是直接相信形态名称。

可观测变量：

- body size。
- upper shadow。
- lower shadow。
- close location value。
- high-low range。
- gap。

搜索关键词：

- candlestick pattern profitability
- OHLC technical indicators
- candlestick patterns statistical test

### 假设二：均线有效性依赖市场状态

均线可能在趋势市场有效，在震荡市场失效。

可观测变量：

- MA slope。
- price MA distance。
- moving average crossover。
- volatility regime。
- trend strength。

搜索关键词：

- moving average trading rule profitability
- trend following equity market
- moving average crossover out of sample

### 假设三：支撑压力来自价格聚集和成交密集

支撑压力不要手动画线，而要用历史高低点、成交密集区、整数价位和 VWAP 区域代理。

可观测变量：

- distance to rolling high。
- distance to rolling low。
- volume by price。
- price clustering。
- breakout return。

搜索关键词：

- support resistance technical analysis
- price clustering stock market
- breakout strategy volume confirmation

### 假设四：左侧与右侧是入场时机问题

左侧交易更接近均值回归，右侧交易更接近动量确认。

搜索关键词：

- mean reversion entry timing
- momentum confirmation
- breakout pullback strategy
- stop loss take profit trading rules

## 回测建议

- 所有形态参数必须滚动生成，不能回看未来高低点。
- 支撑压力不能用“事后看起来明显”的点位。
- 止盈止损要按真实可交易价格模拟。
- 价格形态策略必须和交易成本、换手率、容量一起看。

