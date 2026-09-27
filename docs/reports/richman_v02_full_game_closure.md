# Richman v0.2 —— 完整游戏闭环交付报告

- 版本：**Richman v0.2.0**（协议版本 2，存档版本 2）
- 日期：2026-09-27
- 上一版：`v0.1.0`（首版可玩局域网大富翁）
- 项目：`C:\Users\lihao\Desktop\richman`

> 本报告只写**实际做过并验证过的**事情。每个结论都标了状态：
> `IMPLEMENTED`（实现了）/ `VERIFIED`（实际验证过）/
> `PARTIAL`（部分实现或部分验证）/ `NOT IMPLEMENTED`（未实现）。
> 不使用「基本完成」「理论支持」「应该可以」这类模糊表述。

---

## 1. 当前最终完成度

这一轮的目标不是"加功能"，而是把 v0.1 从"能玩的初版"推到**可以反复和室友玩的正式版本**。

**结论：目标达成。**

| 验收门槛（来自本轮要求） | 状态 |
| --- | --- |
| 打开 Richman，主菜单像正式游戏 | 完成（含 Logo、装饰、版本号、7 个入口） |
| 创建 LAN 房间，室友输入 IP 就能进 | 完成，本机双开真实验证 |
| 选择地图和角色 | 完成（2 张地图 × 3 套规则 × 12 角色） |
| 每个人只能在自己该操作的时候操作 | 完成（统一 `LocalInteraction` 单一判定点） |
| 骰子、移动、事件、买地、升级、商店、卡牌都有明确反馈 | 完成（动画 + 浮字 + 音效 + 日志 + 弹窗） |
| Host 和 Client 看到的是同一局 | 完成，506 次状态哈希采样全部一致 |
| 有人掉线游戏不会炸，AI 顶上不卡 | 完成，双开脚本实测 |
| 资金不足会真正处理资产，而不是突然消失 | 完成（交互式债务处理面板） |
| 地产组合和升级有策略 | 完成（11 片区垄断 + 抵押保留产权 + 卖出回收率差异） |
| 卡牌有使用时机和目标 | 完成（6 种时机，UI 灰化并说明原因） |
| 一局可以完整持续几十分钟 | 完成，4 人局实测约 36 分钟 |
| 结算产生冠军和完整数据 | 完成（排名表 + 12 项统计 + 6 个趣味称号） |
| 结算后不用重启就能继续下一局 | 完成，连续 10 局「再来一局」实测无泄漏 |

代码规模（不含 `.venv`、不含 `dist/`）：

```
src/     65 文件 / 17,637 行
tools/   15 文件 /  4,334 行
tests/    7 文件 /  2,008 行
main.py 等入口约 250 行
```

---

## 2. 本轮发现的真实问题

全部通过实际运行发现，不是代码审查猜的。

| # | 问题 | 怎么发现 | 严重程度 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | `bankruptcy.pay` 把「人类可读说明」当成了流水分类，导致经济报表里出现「湖滨大道 租金」这种一次性类别，租金占比统计恒为 0 | 平衡报告 | P1 | 已修 |
| 2 | 语义化命令匹配时合并了选项 payload，但传给执行层的是原始命令，丢掉 `debt` 标记 → 债务操作后不刷新决策 → 债务阶段无决策 | soak 测试日志 | **P0** | 已修 |
| 3 | 单机模式下引擎被推进两次（`App.update` 与 `GameScene.update` 各一次）→ 游戏两倍速、阶段计时错乱 | 真实窗口对局手感 | **P0** | 已修 |
| 4 | 资产面板滚动后按钮命中判定用的是未偏移坐标 → 翻页后点错按钮 | 阅读 `handle_event` | P1 | 已修 |
| 5 | 债务处理时如果玩家没有任何可操作资产，面板里**没有出路** → 玩家和自动化测试都会卡死 | soak 卡在 `DEBT_RESOLUTION` | **P0** | 已修（补「宣告破产」按钮） |
| 6 | 自动存档每个回合都写一次 100KB 文件 → 400 次/局，且 Windows 上文件被占用会失败 | soak 日志刷屏 | P1 | 已修（节流 + 写入重试） |
| 7 | `_refresh_action_buttons` 每帧重建 Button 对象 → 大量垃圾、帧率下降 | soak 每轮耗时异常 | P1 | 已修（缓存 + 变更检测） |
| 8 | 打包后找不到 `data/*.json`：只读资源与可写数据用了同一个根目录 | EXE 启动自检 | **P0**（打包） | 已修（`resource_root` / `project_root` 分离） |
| 9 | 雪球指标把破产玩家（资产 0）算进分母 → 出现「90366 倍」的假数据 | 平衡报告 | P2 | 已修 |
| 10 | 「破产原因」统计用的是累计最大支出（通常只是买地），没有意义 | 平衡报告 | P2 | 已修（改用最后一笔支出） |

**设计层面**也发现一个需要重新思考的点：

> 抵押的筹款能力（地价 60%）天然低于卖地（已投入资金的 70%），
> 所以「抵押能救」必然蕴含「卖地也能救」。最初想用「卖地不够但抵押够」作为
> 债务处理的触发条件，实际是空集 —— 这个分支永远不会触发。

修正后的语义是：**只要抵押就能付清，就把决定权交给玩家**
（抵押保留产权，比系统替他卖地更合理）。这既符合产品直觉，也让债务处理真正可用。

---

## 3. 修复的核心架构问题

### 3.1 数值加成收口到 Modifier 系统

**问题**：v0.1 里到处是 `player.perk_value("buy_discount")` 和 `has_status(...)` 判断，
每加一个角色 / 状态 / 卡片都要改 `economy.py`。

**做法**：新增 `src/game/modifiers.py`，定义 11 个 Hook 与 5 种运算
（`mul` / `add` / `set` / `min` / `max`），所有数值影响都声明为 Modifier：

- 角色被动 → `perk.modifiers`（数据文件里声明，代码零改动）
- 状态效果 → `StatusEffect` 创建时自动从 payload 生成
- 道具 / 事件 → `player.extra_modifiers`

经济计算只问一句 `modifiers.resolve(player, Hook.X, base)`，
返回 `(结果, breakdown)`，breakdown 能直接给 UI 展示「原价 → 折扣来自哪里 → 实付」。

**验证**：136 个测试里包含角色折扣、折扣券、状态减免、垄断加成的完整路径；
平衡报告显示这些加成确实生效（4 人局租金占收入 26.3%）。

### 3.2 资金流收口到 EconomyLedger

**问题**：v0.1 里到处 `player.money += x`，无法回答「钱从哪来、到哪去」。

**做法**：所有资金变化只允许走三个入口：

```
ledger.gain(      state, player, amount, category, detail)   # 银行 → 玩家
ledger.pay_bank(  state, player, amount, category, detail)   # 玩家 → 银行
ledger.transfer(  state, payer, receiver, amount, category, detail)  # 玩家 → 玩家
```

分类是枚举（租金 / 税收 / 购地 / 抵押 / 商店…），说明是给玩家看的文本，两者严格分开。

**收益**：
- 结算界面能显示「全场收入主要来自：租金 218,679、经过起点 180,000…」
- `tools/balance_simulation.py` 能给出真实的资金流构成
- 破产原因统计（取最后一笔支出）变得有意义

**验证**：`test_ledger_records_every_money_change`、`test_ledger_survives_serialization`
以及平衡报告里的分类数据。

### 3.3 规则闭环：交互式债务处理

**问题**：v0.1 里现金不足是**同步自动处理**的，玩家没有参与，只有结果。

**做法**：

- 新增 `GamePhase.DEBT_RESOLUTION` 阶段与 `DecisionKind.DEBT_RESOLUTION`
- `bankruptcy.pay` 分三种走向：
  1. 现金够 → 直接付清
  2. 抵押能付清 → **标记 `deferred`，交由引擎启动债务处理**，玩家自己选
  3. 卖地也不够 → 直接破产，资产整体移交债权人
- 债务处理用**资产面板**呈现（卡片式，能看等级 / 租金 / 卖价 / 抵押额），
  每操作一次按最新状态重算可选项
- 面板底部固定一个「宣告破产」出口，保证玩家永远有路可走
- 债务期间 `_pending_debt_reopen` 机制保证关掉窗口也会重开，不能靠关窗逃债

**验证**：
`test_debt_resolution_triggers_when_mortgage_can_save`、
`test_debt_resolution_pays_off_after_mortgage`、
`test_debt_resolution_can_declare_bankruptcy`、
`test_auto_liquidation_when_no_mortgage_option`、
`test_hopeless_debt_bankrupts_immediately`；
平衡报告显示每局平均抵押 8～15 次。

### 3.4 输入判定收口到 LocalInteraction

**问题**：每个控件自己判断「现在该谁操作」，容易不一致。

**做法**：`GameScene.interaction_state()` 是**全项目唯一**的判定点，
返回 10 种状态（`ROLL_DICE` / `PROPERTY_DECISION` / `DEBT` / `SHOP` / `WAITING`…），
按钮、快捷键、弹窗、棋盘点击全部读它。

**验证**：`test_ui_smoke_test` 里对局能连续推进 155 回合、结算界面正常弹出。

### 3.5 只读资源与可写数据分离

**问题**：打包后 PyInstaller 把 `--add-data` 放进 `_internal/`，
而存档 / 设置要写在 exe 旁边，v0.1 用同一个根目录 → EXE 启动即崩。

**做法**：`src/utils/paths.py` 拆成两个根：

```
resource_root()  →  sys._MEIPASS（打包）/ 项目根目录（开发）  只读资源
project_root()   →  exe 所在目录（打包）/ 项目根目录（开发）  存档、设置、日志
```

**验证**：`dist\Richman\Richman.exe --selftest` 真实启动并进入对局。

---

## 4. 游戏规则现状

### 4.1 阶段机（15 个阶段）

```
GAME_SETUP → TURN_START → WAIT_ROLL → ROLLING → MOVING → ARRIVED
          → RESOLVE_TILE ─┬→ SHOP（商店选购）
                          ├→ DEBT_RESOLUTION（债务处理）
                          ├→ WAIT_DECISION（买地 / 升级 / 选目标）
                          └→ APPLY_EVENT（机遇结算）
          → TURN_END → 下一位玩家
          GAME_OVER（随时可能进入）
```

新增阶段：`DEBT_RESOLUTION`、`SHOP`。
`DECISION_PHASES` 集合保证这些阶段不会被自动推进跳过。

### 4.2 规则要点

| 项目 | 数值 / 规则 | 状态 |
| --- | --- | --- |
| 初始资金 | 10,000 / 15,000 / 22,000（三档预设） | VERIFIED |
| 经过起点 | +2,000（角色可再加成） | VERIFIED |
| 地产升级 | 3 级，租金 Lv0→Lv3 为地价的 0.06 / 0.34 / 0.78 / 1.45 倍 | VERIFIED |
| 片区垄断 | 该区租金 ×2 | VERIFIED |
| 抵押 | 得地价 60%，不能收租、不能升级，赎回需付 110% | VERIFIED |
| 出售 | 回收已投入的 70%（抵押中 45%） | VERIFIED |
| 拆除建筑 | 返还该级升级费的 50% | VERIFIED |
| 债务处理 | 抵押能付清 → 玩家自己处置；卖光不够 → 破产 | VERIFIED |
| 破产 | 现金与地产整体移交债权人，无债权人则归银行 | VERIFIED |
| 看守所 | 保释 1,500 或掷出 ≥6；关押 3 回合强制付费释放 | VERIFIED |
| 税收 | 固定 1,200 / 按总资产 4%，税金一半注入奖金池 | VERIFIED |
| 奖金池 | 踩到中央公园或奖金池格领走全部 | VERIFIED |
| 路障 | 放置后经过的玩家被迫停下，持续 4 回合 | VERIFIED |
| 胜利 | 只剩 1 人，或达到轮数上限按总资产判定 | VERIFIED |
| 决策超时 | 真人 180 秒不操作自动执行保守选项 | VERIFIED |

---

## 5. 地产 / District

**两张地图、50 处地产、25 个片区。**

| 地图 | 格数 | 网格 | 地产 | 片区 | 推荐人数 | 地图文件 |
| --- | --- | --- | --- | --- | --- | --- |
| 城市之光 | 36 | 11×9 | 24 | 11 | 2～5 人 | `data/default_map.json` |
| 海滨假日 | 40 | 12×10 | 26 | 14 | 3～6 人 | `data/map_seaside.json` |

**格子类型**（`IMPLEMENTED` / `VERIFIED`）：

`START`（起点）、`PROPERTY`（地产）、`STATION`（交通枢纽，租金成长更快）、
`FORTUNE`（福运，只出好事）、`DISASTER`（灾祸，只出坏事）、`SHOP`（商店）、
`TAX`（税收）、`JAIL`（看守所）、`GO_TO_JAIL`（治安巡查）、
`PARK`（中央公园）、`BONUS`（奖金池）。

**数据驱动的验证**：棋盘渲染器从地图数据读取 `cols` / `rows` / `corners`，
不再硬编码网格尺寸；新增地图只需在 `tools/gen_data.py` 的 `MAPS` 列表里加一项。
实测两张地图都能正常渲染与对局，**引擎代码没有出现任何 `if map == xxx`**。

**片区的策略价值**：集齐一个片区即垄断，租金翻倍。
平衡数据显示 4 人局平均持有 18/24 处地产、平均等级 1.50 —— 说明玩家确实在买地和升级，
但达不到「无脑全 Lv3」（现金会先被租金压力吃掉）。

---

## 6. 道具 / 商店 / 事件

### 6.1 道具卡：12 → 21 种

| 类别 | 卡片 |
| --- | --- |
| 经济 | 免租卡、双倍租金卡、现金卡、折扣购地卡、财富税卡、幸运卡 |
| 移动 | 传送卡、指定骰子卡、再次行动卡、交换位置卡 |
| 攻击 | 抢夺卡、停留卡、路障卡、强制降级卡、产权置换卡、封锁卡 |
| 防御 | 保护卡、事件免疫卡、净化卡、保释券 |
| 地产 | 免费升级卡 |

**使用时机系统**（`IMPLEMENTED` / `VERIFIED`）：

`pre_roll`（掷骰前，17 张）、`own_turn`（自己回合，3 张）、
`any_turn`（任意时刻，1 张）；另有 `post_roll` / `on_property` / `reactive`
三种时机已在系统中实现并校验。

不能用的卡在 UI 上灰化并给出原因（例如「仅可在掷骰前使用」），
`can_use()` 是唯一判定入口。

### 6.2 商店格

踩到商店格展示 3 张随机道具，可用现金购买 **0～1 张**。
价格 900～2,200，受 `modify_shop_price` 影响（老周这个角色能便宜 25%）。
平衡数据：4 人局平均每局购买 10.8 张（含 6 人局 16.1 张）。

### 6.3 机遇事件：37 个，正负分离

| 极性 | 数量 | 出现的格子 |
| --- | --- | --- |
| fortune（福运） | 23 | 福运格 |
| disaster（灾祸） | 13 | 灾祸格 |
| neutral | 1 | 两者都可能 |

抽卡时按格子极性过滤 → **玩家看到格子的图标就知道会遇到好事还是坏事**，
不再是「完全随机正负」。
`test_fortune_tile_only_draws_positive` 与 `test_disaster_tile_only_draws_negative`
各抽 120 张验证过。

---

## 7. 角色

**8 → 12 位**，每位带一个通过 Modifier 系统实现的被动，
并绑定一个 AI 人格（稳健 / 均衡 / 激进）。

| 角色 | 特性 | Hook | AI 风格 |
| --- | --- | --- | --- |
| 阿金 | 起始资金 +1,500 | `special` | 均衡 |
| 小满 | 经过起点额外 +600 | `modify_start_reward` | 激进 |
| 老陈 | 购地 −8% | `modify_purchase_price` | 稳健 |
| 娜娜 | 收租 +12% | `modify_rent_received` | 均衡 |
| 铁蛋 | 保释金 −50% | `modify_jail_cost` | 激进 |
| 博楚 | 机遇收益 +20% | `special` | 均衡 |
| 阿飞 | 升级 −10% | `modify_upgrade_cost` | 激进 |
| 胖虎 | 付租 −10% | `modify_rent_paid` | 稳健 |
| 老周 | 商店 −25% | `modify_shop_price` | 稳健 |
| 阿珍 | 卖地回收 +15% | `modify_sell_refund` | 均衡 |
| 强哥 | 抵押多拿 30% | `modify_mortgage_value` | 稳健 |
| 小赵 | 税款 −30% | `modify_tax` | 稳健 |

**实现约束达成**：代码里**没有任何 `if character == xxx`**，
全部通过 `data/characters.json` 的 `perk.modifiers` 声明。

---

## 8. AI

### 8.1 真实能力

| 决策 | 考虑因素 | 状态 |
| --- | --- | --- |
| 是否买地 | 租金回报率、片区完成度、是否枢纽、是否有折扣券、自己的资产排名、现金安全线（人格调节） | VERIFIED |
| 是否升级 | 投入产出比、垄断加成、现金储备、人格阈值 | VERIFIED |
| 看守所 | 剩余关押回合、保释后是否仍高于安全线 | VERIFIED |
| 道具使用 | 按 21 张卡各自的策略打分（见下） | VERIFIED |
| 道具目标 | 优先最富且未受保护的对手；指定骰子卡会**逐个点数算落点收益** | VERIFIED |
| 债务处理 | 优先抵押空地（保留产权）、避开垄断区与高价地、优先能一次付清的选项 | VERIFIED |
| 商店 | 按处境选卡：缺钱买现金卡、有地买升级卡、领先买攻击卡 | VERIFIED |
| 主动理财 | 现金低于安全线且有空地 → 主动抵押换流动性（每回合最多一次） | VERIFIED |
| 赎回 | 现金充裕（>1.4× 安全线）时赎回租金潜力最大的抵押地 | IMPLEMENTED，实测触发 0 次（见下） |

### 8.2 三种人格

| 人格 | 现金保留 | 买地阈值 | 升级阈值 | 攻击倾向 | 默认角色 |
| --- | --- | --- | --- | --- | --- |
| 稳健型 | ×1.6 | +12 | +6 | ×0.7 | 老陈、胖虎、老周、强哥、小赵 |
| 均衡型 | ×1.0 | 0 | 0 | ×1.0 | 阿金、娜娜、博楚、阿珍 |
| 激进型 | ×0.6 | −14 | −8 | ×1.4 | 小满、铁蛋、阿飞 |

### 8.3 关于「AI 不赎回」

实测 100+ 局中赎回次数为 0。核查后确认这是**理性选择**而不是 bug：

```
赎回成本 = 地价 × 0.6 × 1.1 = 地价 × 0.66
空地每圈租金 = 地价 × 0.06
回本需要 11 圈，而一局平均只有约 35 圈（且赎回后还要再升级才有收益）
```

所以 AI 选择把现金投到买地和升级上更划算。
赎回路径本身由 `test_redeem_pays_110_percent` 覆盖，玩家在资产面板里随时可用。

---

## 9. LAN

### 9.1 架构

```
Host                                              Client
├─ GameEngine（唯一规则权威）                      ├─ 无引擎
├─ GameState（唯一真实状态）                       ├─ GameClient（只读快照）
├─ 收款/校验所有 Command                          ├─ 只发 Command
└─ 限流广播 STATE_SNAPSHOT（≥80ms，日志裁到 40 条） └─ rev 更旧则丢弃
```

- 传输：TCP + 4 字节大端长度前缀 + UTF-8 JSON
- 协议版本：**2**（v0.1 是 1，本轮新增 SET_MAP / SET_PRESET）
- 消息类型：28 种
- 线程模型：每连接一对收发线程，主线程只做入队 / 取队列

### 9.2 验证结果（`tools/run_host_client_demo.py`）

```
验证结果：18/18 通过
  Client 真实完成自己的回合 82 次（真点击 → 真命令 → 真状态回传）
  Client 真实完成购地 16 次
  状态一致性比对 506/506 次一致，平均追平耗时 2ms
  客户端篡改本地状态 → Host 侧数值不变
  客户端伪造他人决策 → 被拒绝，原决策保持
  掉线后 Host 继续运行（rev 521 → 529）
```

一致性比对方式：每次采样先让客户端追平 Host 当前 `revision`，
再逐字段比对 `canonical_hash`（players / positions / money / properties / phase / turn / bonus_pool）。

### 9.3 大厅与开局

大厅支持：房间信息（IP / 端口 / 地图 / 规则）、座位列表（角色 / 准备 / 掉线标记）、
添加与移除 AI、踢人、切换地图、切换规则预设、开始游戏（只有房主可点，条件不足时显示原因）。
`IMPLEMENTED` / `VERIFIED`（本机双开）。

---

## 10. Reconnect / Disconnect

| 能力 | 实现 | 验证 |
| --- | --- | --- |
| 检测掉线并标记玩家 | `IMPLEMENTED` | `VERIFIED`（双开脚本 [7] 段） |
| 掉线后游戏继续运行 | `IMPLEMENTED` | `VERIFIED`（Host 单独推进 240 帧） |
| 30 秒宽限 + 超时 AI 接管 | `IMPLEMENTED` | `PARTIAL`（逻辑完整，未等待 30 秒实测接管） |
| token 校验重连 + 补发最新快照 | `IMPLEMENTED` | `PARTIAL`（协议与 Host 逻辑完整，只验证了「掉线不毁局」，**没做拔网线再插回来的真机测试**） |
| 房主退出 → 客户端收到提示并回主菜单 | `IMPLEMENTED` | `VERIFIED`（客户端 `DISCONNECT` 处理 + 弹窗 + 回菜单） |

**诚实说明**：重连是这一轮**唯一没有真正验证到位**的联机能力。

---

## 11. UI / UX

### 11.1 页面清单

| 页面 | 内容 |
| --- | --- |
| 主菜单 | Logo、漂浮装饰、程序化棋盘剪影、单人 / 建房 / 加入 / 存档 / 设置 / **规则图鉴** / 退出 |
| 单人配置 | 昵称、人数、座位切换、12 角色、**地图选择**、**规则预设选择** |
| 局域网配置 | 建房（房间名 / 端口）+ 加入（IP / 端口 / 自动发现列表） |
| 大厅 | 房间信息、座位卡、加 AI、准备、踢人、**地图与规则切换**、开始 |
| 对局 | 玩家面板、棋盘、中央提示、资产按钮、图鉴按钮、菜单、掷骰、道具、地块信息、事件日志 |
| **资产面板** | 卡片式地产管理：升级 / 出售 / 抵押 / 赎回 / 拆除，显示现金 / 总资产 / 预计租金 |
| **债务处理** | 复用资产面板，标题显示欠款与缺口，底部有「宣告破产」出口 |
| **商店** | 卡片式展示 3 张道具，显示名称 / 描述 / 时机 / 价格，可买 0～1 张 |
| **规则与图鉴** | 6 个标签页：怎么玩 / 地块图鉴 / 道具图鉴 / 角色图鉴 / 地图 / 快捷键 |
| 弹窗 | 买地 / 升级、看守所、机遇卡、道具选目标、骰子点数、暂停、结算 |
| 设置 | 音量 ×3、静音、动画速度 ×4、字体 ×3、分辨率 ×4、全屏、昵称、调试开关 |

### 11.2 结算界面

新增内容：6 个趣味称号（地产大亨 / 最佳房东 / 卡牌大师 / 运势之王 / 散财童子 / 守财奴
/ 倒霉蛋），以及全场资金流摘要（「全场收入主要来自：租金 …、经过起点 …」）。
称号**只影响展示，不参与胜负**。

### 11.3 统一收口

- **金额格式**：全项目统一 `¥ 15,000` / `+ ¥ 800` / `- ¥ 800`（`src/game/format.py`）
- **输入层级**：`GAME_OVER > MODAL > 目标选择 > 道具手牌 > 操作区 > 棋盘`，
  弹窗出现时吞掉全部输入，杜绝穿透点击
- **音效**：14 个程序化音效，13 个已接入事件（骰子 / 移动 / 买地 / 升级 / 收租 /
  机遇 / 看守所 / 破产 / 胜利 / 回合开始 / 悬停 / 点击 / 错误）

---

## 12. Save

| 能力 | 状态 |
| --- | --- |
| 每回合自动存档（已节流为 ≥20 秒且 ≥4 回合） | `VERIFIED` |
| 手动存档 / 存档列表 / 读档 | `VERIFIED` |
| 存档保存随机数状态（seed + counter） | `VERIFIED`（读档后 hash 完全一致） |
| 读档后能继续跑到结束 | `VERIFIED`（实测续跑至 150 轮结束） |
| `save_version` + 版本守卫 | `VERIFIED`（高版本存档明确报错，不 traceback） |
| `game_version` 记录 | `IMPLEMENTED`（存档头含 `meta`，但未做跨版本迁移） |
| 各阶段（WAIT_ROLL / WAIT_DECISION / DEBT_RESOLUTION）存档 | `IMPLEMENTED`：`PendingDecision` 与 `debt` 都完整序列化 |

---

## 13. 自动长局

### 13.1 AI 长局模拟（`tools/simulate_game.py`）

逐帧检查 12 项不变量：位置越界、稳定阶段负现金、地产指向无效玩家、
地产属于已破产玩家、同格多主人、决策目标无效、向已破产玩家提问、
决策无选项、GameOver 无赢家、决策卡死超时、阶段卡死。

**本轮结果**：2 / 4 / 6 人各 15 局，**全部正常结束，异常 0**。

### 13.2 经济平衡（`tools/balance_simulation.py` → `docs/reports/balance_report_v01.md`）

| 场景 | 完成率 | 平均轮数 | 估算时长 | 首次破产 | 平均破产人数 |
| --- | --- | --- | --- | --- | --- |
| 2 人 · 城市之光 | 20/20 | 142 | 19 分钟 | 第 141 轮 | 0.8 |
| 3 人 · 城市之光 | 20/20 | 145 | 29 分钟 | 第 96 轮 | 1.9 |
| **4 人 · 城市之光** | **20/20** | **136** | **36 分钟** | 第 68 轮 | 2.8 |
| 6 人 · 城市之光 | 20/20 | 164 | 66 分钟 | 第 59 轮 | 4.7 |
| 4 人 · 海滨假日 | 20/20 | 132 | 35 分钟 | 第 69 轮 | 2.9 |

**4 人局 36 分钟，落在 25～50 分钟的目标区间内。**

资金流构成（占全部收入）与雪球指标：

| 场景 | 租金 | 经过起点 | 机遇 | 雪球比 |
| --- | --- | --- | --- | --- |
| 2 人 | 14.7% | 38.3% | 4.7% | 1.22 |
| 4 人 | 26.3% | 27.1% | 4.3% | 1.09 |
| 6 人 | 36.9% | 24.4% | 4.3% | 2.09 |

雪球比（冠军总资产 ÷ 未破产对手平均资产）均 < 3，说明领先者仍可被追赶。
银行净投放为正（起点奖励多于税收回收），4 人局约 +33,000。

### 13.3 长时间稳定性（`tools/soak_test.py` / `tools/soak_restart.py`）

```
连续 10 局（无 UI）：10/10 完成，内存无增长，线程 1→1，对象 +181
「再来一局」6 局：局面干净 6/6，线程 1→1，对象 +1,175（容差内）
```

---

## 14. localhost 联机实测

`tools/run_host_client_demo.py` 在**同一个进程内建立两个真实 TCP 连接**
（Host 监听 127.0.0.1，Client 连接），双方各自跑独立的循环，
由两个不同的控制器分别驱动两侧的真人座位。

```
[PASS] 房间启动 —— 端口 28110
[PASS] Client 收到 JOIN_ACCEPTED
[PASS] Host 大厅显示 2 人 / Client 收到大厅状态 / 看到房主
[PASS] Host 添加 AI 后 Client 同步到 4 个座位
[PASS] 全部准备完成 / Host 开始游戏 / Client 收到首帧快照
[PASS] Client 真实完成自己的回合 82 次
[PASS] Client 真实完成购地 16 次
[PASS] 状态一致性比对 506/506 次一致，平均追平 2ms
[PASS] 客户端篡改本地状态不影响 Host
[PASS] 客户端无法替他人做决策
[PASS] Host 检测到 Client 掉线 / 掉线后继续运行 / 玩家被正确标记
[PASS] 房间正常关闭
验证结果：18/18 通过
```

**局限**：这是本机回环，不是两台物理机器之间的验证。

---

## 15. 性能

| 项目 | 实测 |
| --- | --- |
| 引擎纯逻辑推进 | 约 0.2 秒跑完一整局（400+ 回合，含 AI 决策） |
| 真实窗口帧率 | 1600×900 下稳定 60 FPS（`clock.tick(60)`，棋盘静态层已缓存） |
| 状态快照体积 | 约 15～40 KB（日志裁到 40 条），广播限流 80ms |
| 网络追平耗时 | 平均 2ms |
| 单局内存 | 30 MB 上下，连续 10 局无增长 |

**本轮的优化**：
- 按钮对象缓存（每帧重建 → 变更时才重建）
- 自动存档节流（每回合 → ≥20 秒且 ≥4 回合）
- 棋盘静态层缓存（v0.1 已有，本轮确认仍有效）

---

## 16. 打包

**实际 build 并验证过。**

```powershell
.\.venv\Scripts\python.exe tools\build_windows.py --clean
```

```
dist\Richman\Richman.exe              主程序 2.7 MB
dist\Richman\_internal\               依赖与游戏数据，整个目录 28.3 MB
```

产物自检（不是只看退出码）：

```
$ .\dist\Richman\Richman.exe --selftest --selftest-seconds 5
Richman v0.2.0 (version (0, 2, 0))
项目根目录: C:\Users\lihao\Desktop\richman\dist\Richman
中文字体: msyhl.ttc
地图: 城市之光 36格, 海滨假日 40格
预设: 标准局, 休闲局, 快速局
道具: 21 种
已进入对局，开始推进…
推进结果: 6721 帧，第 1 轮 / 1 回合，阶段 WAIT_ROLL
资金流水 2 条，玩家 2 人，地产 24 处
状态检查: 通过
启动自检完成
```

也就是说：**EXE 真的能启动、能读数据、能建引擎、能画界面、能进对局**。

---

## 17. 已知限制

按「影响程度」排序：

1. **跨机联机未在真实两台电脑上验证** —— 开发机只有一台。
   防火墙、AP 隔离、多网卡选错 IP 这些真实环境问题没有踩过。这是最大的未知。
2. **重连只验证了「掉线不毁局」**，没有做「拔网线 → 插回来 → 继续玩」的真机测试。
3. **UDP 自动发现没有跨机验证**，部分网络屏蔽广播包（手动输入 IP 是保底方案）。
4. **没有人类连续长时间试玩过** —— 真实窗口下的完整对局是自动化点击跑完的
   （138 轮 / 407 回合 / 覆盖 99 秒连续渲染），但手感、节奏偏慢或偏快这类主观体验，
   仍然需要真人上机确认。
5. **AI 从不赎回抵押地** —— 经核查是理性选择（回本期 11 圈 > 局均 35 圈），
   不是 bug，但确实意味着这个功能只有玩家会用。
6. **6 人局偏长**（约 60 分钟）—— 人数多导致每轮时间长。
   想更快可以用「快速局」预设。
7. **没有回放 / 撤销 / 地图编辑器**。
8. **存档跨版本迁移未实现** —— 有 `save_version` 与版本守卫，但没写 migrate 逻辑。
9. **`game_version` 只记录未利用** —— 存档头里有，但没有基于它做兼容判断。

---

## 18. 下一阶段

按投入产出比排序：

1. **跨机联机实测 + 防火墙引导完善**（最高优先级）
   在真实两台机器 + 路由器环境下走一遍，把常见失败（防火墙、AP 隔离、
   多网卡选错 IP）的提示做得更明确。这是当前最大的未验证风险。
2. **重连真机验证**：把「拔网线 → 插回来 → 继续玩」这条路走通。
3. **真人试玩调优**：邀请 3～6 人实测一局，重点看节奏、弹窗频率、
   自动存档提示是否干扰、6 人局是否过长。
4. **AI 深化**：让 AI 会评估「对手下一步可能踩到什么」来做道具预判；
   增加会算概率的「精算型」人格。
5. **视觉打磨**：地产建筑外观随等级变化（现在是圆点）、结算排名曲线图、
   移动时镜头跟随。
6. **存档迁移**：实现 `save_version` 的 migrate 链，让旧存档能升级。
7. **音效与 BGM**：接入合法的 CC0 音源替换程序合成音效。

---

## 附录 A：本轮验证命令与结果

| 命令 | 结果 |
| --- | --- |
| `python -m pytest tests/ -q` | **136 passed** |
| `python tools/simulate_game.py --games 15 --players {2,4,6}` | **各 15/15 正常结束，异常 0** |
| `python tools/balance_simulation.py --games 20` | **5 组各 20/20 完成，报告已生成** |
| `python tools/run_host_client_demo.py --rounds 60 --ai 2` | **18/18 通过，506/506 一致性采样** |
| `python tools/ui_smoke_test.py` | **18/18 通过（含三档分辨率）** |
| `python tools/launch_check.py --full-game` | **真实窗口跑完一整局并弹出结算** |
| `python tools/soak_test.py --games 10` | **10/10，无资源泄漏** |
| `python tools/soak_restart.py --games 6` | **局面干净 6/6，无资源泄漏** |
| `python tools/build_windows.py --clean` | **产物生成 + `--selftest` 真实启动通过** |
| `main.py --selftest` | **数据 / 引擎 / UI 全链路通过** |

## 附录 B：本轮新增与修改的文件

**新增（主要）**

```
src/game/modifiers.py          Modifier 系统（11 个 Hook）
src/game/ledger.py             EconomyLedger
src/game/format.py             统一金额格式化
src/version.py                 版本号唯一来源
src/ui/asset_panel.py          资产面板（含债务处理）
src/ui/help_scene.py           规则与图鉴
tools/balance_simulation.py    经济平衡分析
tools/soak_test.py             连续多局资源检查
tools/soak_restart.py          「再来一局」检查
tools/build_windows.py         PyInstaller 打包
tests/test_debt_and_assets.py  债务 / 抵押 / 商店 / 新道具测试（30 个）
docs/reports/current_state_audit_v02.md
docs/reports/balance_report_v01.md
docs/reports/richman_v02_full_game_closure.md
data/map_seaside.json          第二张地图
```

**修改（主要）**

```
src/game/engine.py        债务处理 / 资产操作 / 商店 / 流水接入 / payload 合并修复
src/game/economy.py       全部改为 Modifier 驱动 + 抵押计算 + 自救选项
src/game/bankruptcy.py    延迟破产 + 分类与说明分离 + 变卖资金流修正
src/game/phases.py        DEBT_RESOLUTION / SHOP
src/game/commands.py      资产与商店命令、债务与商店决策
src/game/cards.py         时机系统 / 商店价格 / 新目标类型
src/game/chance.py        正负极性 + 事件收益修正 + lucky
src/game/player.py        extra_modifiers / 新状态 / 负面状态集合
src/game/setup.py         多地图 / 规则预设
src/game/board.py         通用网格与角索引
src/game/victory.py       趣味称号 / 收支明细 / 资金流摘要
src/network/lobby.py      地图与预设选择
src/network/host.py       地图/预设消息 + 开局使用
src/network/protocol.py   协议版本 2 + 新消息类型
src/ui/game_scene.py      资产面板 / 商店 / 输入状态 / 性能优化 / 存档节流
src/ui/dialogs.py         商店对话框 / 结算增强
src/ui/board_view.py      任意网格适配
src/ui/setup_scenes.py    地图与预设选择器
src/ui/lobby.py           地图与预设显示与切换
src/utils/paths.py        只读资源与可写数据分离
src/persistence/settings.py  game 段 + 可写目录
data/cards.json           12 → 21 种
data/characters.json      8 → 12 位 + AI 人格
data/chance_events.json   加极性标定
tools/gen_data.py         多地图生成器
main.py                   --selftest / --version
README.md                 全面重写
```
