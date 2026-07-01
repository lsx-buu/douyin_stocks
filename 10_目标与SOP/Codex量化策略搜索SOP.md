---
类型: SOP
状态: 生效
创建时间: 2026-06-30
来源: 机油手《股市生存指南》问AI素材重构
---

# Codex量化策略搜索SOP

## 任务目标

当用户要求“继续搜量化交易策略”时，Codex 不应只搜索中文短线话术，而要按以下链路推进：

盘面经验 → 可观测变量 → 数据字段 → 英中文关键词 → 论文/开源实现 → 回测设计 → 失败边界。

## 第一步：识别策略主题

先判断用户目标属于哪一类：

- 市场微观结构：盘口、委比、内外盘、成交明细、订单簿。
- 成交活跃度：量比、换手率、成交量、资金关注度。
- 开盘行为：集合竞价、开盘跳空、开盘成交额、竞价未成交量。
- 价格形态：K线、均线、支撑压力、突破和回踩。
- 风控和执行：止损、止盈、T+1、滑点、涨跌停。
- 市场状态：指数、大盘环境、行业和风格轮动。
- 短线情绪与题材：冰点、退潮、回暖、主线、龙头、分歧修复、赚钱效应。

## 第二步：把话术改写成可搜索问题

不要搜索“主力怎么骗散户”。

改成：

- order imbalance 是否预测短期收益。
- buyer initiated volume 与后续价格反应的关系。
- abnormal turnover 是否代表 momentum、attention 或 reversal。
- opening auction imbalance 是否预测开盘后收益。
- support/resistance breakout 是否存在样本外显著性。
- thematic momentum 是否能解释题材扩散和龙头带动。
- market breadth sentiment 是否能作为短线仓位过滤器。
- limit-up spillover 是否能验证前排、后排和补涨的收益差异。

## 第三步：优先搜索英文主关键词

英文资料通常更容易找到论文、数据字段和开源代码。

优先用：

- market microstructure
- order imbalance
- limit order book imbalance
- queue imbalance
- opening auction
- call auction
- relative volume
- turnover ratio
- float turnover
- trade classification
- buyer initiated volume
- price impact
- intraday momentum
- reversal
- support resistance
- moving average crossover
- candlestick pattern profitability
- market breadth
- investor sentiment
- thematic momentum
- industry momentum
- sector rotation
- lead-lag effect
- limit-up spillover
- dynamic position sizing

## 第四步：补中文/A股约束关键词

英文拿到理论后，再补中文场景：

- A股 集合竞价 因子
- A股 盘口 委比
- A股 换手率 因子
- A股 量比 短线
- A股 涨跌停 T+1 回测
- A股 日内成交明细 主动买卖
- A股 订单簿 高频 因子
- A股 短线情绪 因子
- A股 涨停 跌停 炸板 回测
- A股 题材轮动 龙头 溢价
- A股 昨日涨停指数 策略
- A股 大小盘 风格轮动 因子

## 第五步：必须落到实验表

每个搜索结果必须回答：

- 需要什么数据粒度：日线、分钟、tick、逐笔成交、L2盘口。
- 是否会受 T+1、涨跌停、停牌、集合竞价规则影响。
- 入场时间和出场时间是否真实可执行。
- 信号是否存在偷看未来。
- 是否考虑交易成本、冲击成本和容量。
- 适合横截面选股、择时、事件研究，还是执行优化。

## 第六步：不要把结论写死

每张策略卡只写：

- 假设。
- 可观测变量。
- 搜索关键词。
- 验证方法。
- 可能失效原因。

不要写“这个指标一定有效”。
