# Richman 当前状态审计（v02 起点）

- 审计日期：2026-09-27
- 审计方式：直接读取仓库真实代码 + 运行现有测试与工具，不依据旧需求文档推测
- 仓库状态：`aff3f28`，工作区干净，2 个提交
- 代码规模：`src` 59 文件 / 14,122 行，`tools` 5 文件 / 1,435 行，`tests` 6 文件 / 1,491 行

---

## 1. 项目结构（真实）

```
src/
├── app.py                     窗口、主循环、场景装配、网络会话
├── game/       (21 文件)      规则层，与 UI/网络完全解耦
├── controllers/ (4 文件)      Base / Local / AI / Remote
├── network/     (8 文件)      protocol / transport / host / client / session / lobby / discovery
├── ui/         (14 文件)      theme / layout / widgets / scene / animations /
│                              board_view / player_panel / property_panel / dialogs /
│                              toast / menu / lobby / setup_scenes / settings_scene / game_scene
├── audio/                     AudioManager（程序化合成 14 个音效 + BGM 接口）
├── persistence/               settings / savegame
└── utils/                     paths / logging / easing
```

---

## 2. 核心系统真实状态

### 2.1 GameEngine（`src/game/engine.py`，约 1,100 行）

**实现方式**：单一 `GameEngine` 类，`update(dt)` 驱动阶段机，`submit_command(cmd)`
统一校验执行。方法按职责分区（生命周期 / 命令入口 / 阶段推进 / 掷骰 / 移动 /
落点结算 / 机遇 / 看守所 / 地产 / 道具 / 投降 / 工具）。

**结论：结构健康，无需重构。**

存在的问题（本轮要修）：

| 问题 | 说明 |
| --- | --- |
| 演出与规则部分耦合 | `_enter_phase` 直接接收 `duration`（演出时长），规则阶段推进依赖 `phase_timer` 累加。虽然动画速度只影响时长、不影响正确性，但「阶段何时结束」与「动画放多久」是同一个数字，属于隐式耦合 |
| 债务处理不可交互 | `bankruptcy.pay()` 一步到位自动变卖，玩家没有任何选择 |
| 无资金流水 | 到处都是 `player.money += x`，没有统一出口，无法统计钱的来源 |

### 2.2 GameState（`src/game/state.py`）

字段齐全：`match_id / revision / phase / round_number / turn_number /
current_player_id / winner_id / game_over / seed / rng_counter / rules /
board / players / properties / district_props / chance_deck / dice / move_* /
pending_decision / pending_effect / debt / extra_turns / barriers /
phase_timer / decision_timer / clock / event_log / stats`。

**随机可复现**：`next_rng()` 用 `seed + counter` 构造 `random.Random`，
完全可序列化，读档后可继续同一随机序列（已实测验证）。

**结论：字段完备，直接扩展即可。**

### 2.3 Phase 系统（`src/game/phases.py`）

现有 11 个阶段：

```
GAME_SETUP / TURN_START / WAIT_ROLL / ROLLING / MOVING / ARRIVED /
RESOLVE_TILE / WAIT_DECISION / APPLY_EVENT / JAIL_DECISION / TURN_END / GAME_OVER
```

**缺口**（本轮补齐）：

- 没有独立的 `STATUS_RESOLVE`（状态结算混在 `TURN_START` 里）
- 没有 `JAIL_CHECK`（监狱判断混在 `_wait_roll` 里）
- 没有 `DEBT_RESOLUTION`（债务是同步函数，不占阶段）
- `WAIT_ROLL` 同时承担「掷骰前可用道具」与「等待掷骰」两件事

**已确认正确的部分**（不改）：

- 一个时刻只有一个主 Phase；
- `PendingDecision` 与 Phase 不冲突（决策阶段 `phase_duration = 0`，不会自动推进）；
- `game_over` 后 `_enter_phase` 直接拒绝任何阶段变更；
- 破产玩家由 `_advance_player` 跳过；
- `skip_turn` 在 `_begin_turn` 中正确消费；
- 监狱不会死锁（有 `jail_max_turns` 自动释放 + 决策超时兜底）；
- **动画不决定规则**：`MOVING` 的 `position` 在阶段结束时才落定，
  动画只是本地表现，中途跳帧不影响规则；
- 引擎不等待渲染层（`update(dt)` 由主循环驱动，与绘制完全分离）。

### 2.4 Command 系统（`src/game/commands.py`）

`Command{type, player_id, command_id, payload, client_revision, decision_id}`，
21 种命令类型常量，`CommandResult{ok, reason, events}`。

**已实现**：`command_id` 去重（`processed_commands` 最多缓存 500 条）、
`decision_id` 过期校验、错误阶段拒绝、越权拒绝。

**结论：完备，本轮只增加新命令类型（商店购买、抵押、赎回、手动变卖）。**

### 2.5 PendingDecision（`src/game/commands.py`）

`PendingDecision{id, player_id, kind, title, description, options, context,
cancellable, created_revision, seq}`，
`DecisionOption{id, label, command_type, payload, enabled, hint, danger}`。

8 种 `DecisionKind`：`roll / buy_property / upgrade_property / jail /
card_target_tile / card_target_player / card_target_own_property /
card_dice_value / chance_ack / bankruptcy / game_over`。

**已实现的关键防护**：

- 决策失败时**放回**（`_dispatch_decision_option` 的恢复逻辑），不会丢决策；
- 兜底：决策阶段无决策 → 强制推进到 `TURN_END`，杜绝卡局；
- 决策超时（默认 180 秒）自动执行保守选项。

**结论：设计正确，本轮增加 `debt_resolution` 与 `shop` 两种 kind。**

### 2.6 Controller（`src/controllers/`）

`BaseController.poll(decision, state, engine, dt) -> Command | None`，
四个实现：`LocalController` / `RemoteController`（都基于 `QueuedController`）/ `AIController`。

**结论：统一入口已成立，真人/AI/远程走同一条规则入口。**

### 2.7 AI（`src/controllers/ai.py`，约 520 行）

已具备：买地评分（租金回报率 + 片区完成度 + 高级地产 + 折扣券 + 资产排名惩罚
+ 现金安全线）、升级评分（投入产出比 + 垄断加成）、看守所决策、
道具使用（12 张卡各有策略）、目标选择（优先最富且无保护）、指定骰子点数计算。

**实测表现**（4 人局 × 60 局）：平均每局用道具 9.3 次、升级 43.1 次、购地 46.4 次。

**缺口**（本轮补）：

- 没有**人格**（稳健 / 均衡 / 激进）；
- 不使用**抵押与主动变卖**（因为现在没有这些系统）；
- 决策里没有考虑「自己下一步可能踩到什么」来做道具预判。

### 2.8 LAN Host（`src/network/host.py`，约 560 行）

`GameHost`：监听、接受、大厅、开局、命令转发、快照广播、掉线检测、
30 秒宽限转 AI、重连、心跳。

关键实现：`_conns` + `_conn_player` 映射连接与会话；
客户端命令的 `player_id` **被强制覆盖**为会话身份（防冒充）；
快照限流 80ms 且裁掉日志到最近 40 条。

### 2.9 LAN Client（`src/network/client.py`，约 380 行）

`GameClient`：连接、加入、重连、仅接受更新的快照（`rev <= local` 直接丢弃）、
本地 `state` 只读视图、命令回执。

**只读性审计（需求 30）**：静态检查 `src/network/client.py` 与
`src/ui/game_scene.py` 的客户端路径，**未发现**任何直接规则修改
（无 `money -=`、无 `owner_id =`、无 `current_player =`、无 `random.randint`）。
客户端唯一的写操作是 `self.revision / self.snapshot / self.state`（本地视图）
与 UI 状态。

### 2.10 protocol（`src/network/protocol.py`）

4 字节大端长度前缀 + UTF-8 JSON；`PROTOCOL_VERSION = 1`；17 种消息类型；
流式解码器（已验证拆包 / 粘包 / 非法长度 / 中文）；
`check_version()` 在连接时比对版本，不一致立即拒绝并给中文提示。

**结论：需求 29 已满足。**

### 2.11 revision

`GameState.bump()` 每次状态变化 `revision += 1`；
客户端 `_apply_snapshot` 只接受 `rev > local`；
`serializer.should_accept_snapshot()` 提供同一判定。
LAN 验证中 506 次采样全部一致。

### 2.12 UI Scene（`src/ui/scene.py`）

`SceneManager` 支持 `register / switch_to / push / pop`，
场景缓存复用；`transparent` 标记决定是否绘制下层。

7 个场景：`menu / local_setup / lan_setup / save_browser / settings / lobby / game`。

### 2.13 board renderer（`src/ui/board_view.py`）

11×9 网格（含四角，对应 36 格），静态层缓存成 Surface，
每帧只叠加动态内容（归属色条、等级点、路障、棋子、高亮）。
`grid_position()` 与 `tools/gen_data.py` 的 `side_of()` 已对齐。

### 2.14 animation（`src/ui/animations.py`）

`AnimationManager` 统一速度倍率；实现 `FloatingText / DiceRollAnimation /
CardFlipAnimation / PieceMoveAnimation / Pulse / ShakeAnimation / FadeAnimation`。

**缺口**（需求 5）：**没有演出队列**。动画由 `_sync_animations` 根据阶段变化
即时触发，多个事件同帧发生时动画会叠在一起（例如骰子未停就开始移动）。

### 2.15 savegame（`src/persistence/savegame.py`）

自动存档（每回合）+ 手动存档；`SAVE_VERSION = 1`；
`load_engine(path, anim_speed)` 保留用户动画速度；
实测：读档后 `canonical_hash` 完全一致，可继续跑完整局。

**缺口**：`save_version` 有，但**没有 `game_version`**（需求 54）。

### 2.16 settings（`src/persistence/settings.py`）

`display / audio / ui / player / network` 五段；类型化读取已加保护
（配置写坏时回退默认值并记日志）。

### 2.17 cards（`src/game/cards.py` + `data/cards.json`）

**12 种**道具卡，`needs_target` 四类（tile / player / own_property / dice_value），
`can_use()` 前置校验，`valid_targets()` 由规则层给出合法目标。

**缺口**：种类偏少（需求 12 要求 18–24 种）；没有使用时机分级（需求 13）。

### 2.18 chance（`src/game/chance.py` + `data/chance_events.json`）

**37 个事件**，效果 kind 覆盖资金 / 移动 / 多人互动 / 地产 / 状态 / 奖励 / 惩罚。
`apply_effect` 返回 `(followups, 中文描述)`，需要引擎参与的动作走 followup。

**缺口**：没有正负面分离（需求 16），玩家看到「机遇」不知道会好会坏。

### 2.19 property（`src/game/property.py`）

`Property{id, tile_index, name, district, kind, price, max_level, rent_table,
upgrade_costs, level_names, owner_id, level, mortgaged}`。

**`mortgaged` 字段存在但没有任何操作入口**（既不能抵押也不能赎回）。

`sell_value = total_invested * 0.7`；`asset_value = total_invested`。

### 2.20 jail（`src/game/jail.py`）

保释 / 掷骰（≥6）/ 关押 3 回合后强制付费释放；角色保释金折扣。

### 2.21 bankruptcy（`src/game/bankruptcy.py`）

本轮上一版刚重构过：先判断「现金 + 全部资产变卖值」够不够，
够则按「先卖便宜的、等级低的」顺序变卖抵债，不够则**不再变卖**而是资产整体移交债权人。

**缺口**：整个流程是同步自动的，玩家无法参与（需求 11 要求交互式债务处理）。

### 2.22 victory（`src/game/victory.py`）

`check_winner`（只剩 1 人）/ `check_max_rounds`（200 轮按资产判定）/
`ranking`（冠军 → 未破产 → 破产顺序）/ `final_stats`。

**缺口**：没有趣味称号（需求 62）。

### 2.23 tests / tools

`tests`：6 文件 / 106 个测试，全部通过（0.5 秒）。
`tools`：`gen_data / simulate_game / run_host_client_demo / ui_smoke_test / launch_check`。

**缺口**：没有余额分析工具（需求 26）、没有重放工具（需求 55）、
没有打包脚本（需求 75）、没有第二张地图。

### 2.24 README

已是一份可用的 README（安装 / 运行 / 单机 / LAN / 双开 / 快捷键 / 架构 / 开发说明 / 已知限制）。

**缺口**：没有截图目录约定、没有 Build 章节、没有 Rules 详表。

### 2.25 Git

`aff3f28`，工作区干净，2 个提交，**没有 remote，未 push**。

---

## 3. 本轮要做的改造（按优先级）

### P0 —— 规则闭环与稳定性（先做）

| # | 项目 | 现状 | 目标 |
| --- | --- | --- | --- |
| 1 | 债务处理 | 全自动变卖，玩家无参与 | 新增 `DEBT_RESOLUTION` 阶段 + 弹窗，玩家可逐项变卖/抵押直到够付 |
| 2 | 抵押系统 | 字段存在但无操作 | 抵押换现金（不能收租、不能升级）、按 1.1 倍赎回 |
| 3 | 资产面板 | 无 | 完整资产管理界面（卡片布局，可升级/出售/抵押/赎回） |
| 4 | 阶段机补全 | 缺 STATUS_RESOLVE / JAIL_CHECK / DEBT_RESOLUTION | 补齐，并把「掷骰前动作」从 WAIT_ROLL 中拆出来 |
| 5 | 演出队列 | 无，动画会叠 | 引入 PresentationQueue，多个事件按序播放 |
| 6 | 输入优先级 | 各 Widget 自己判断 | 统一 `LocalInteractionState` 单一出口 |

### P1 —— 内容与体验

| # | 项目 | 目标 |
| --- | --- | --- |
| 7 | 道具扩展 | 12 → 20 种，含使用时机（PRE_ROLL / POST_ROLL / ON_PROPERTY / REACTIVE / ANY_TURN） |
| 8 | 商店格 | 地图加入 SHOP，踩到展示 3 张卡可买 0–1 张 |
| 9 | 机遇拆分 | FORTUNE（正面）/ DISASTER（风险）分开，地图视觉区分 |
| 10 | 地产组强化 | 保留垄断翻倍，增加「集齐后提升升级上限」的轻量奖励 |
| 11 | 第二张地图 | 40 格「海滨假日」，验证数据驱动 |
| 12 | 地图选择 UI | 大厅展示地图卡，仅 Host 可改 |
| 13 | 规则 Preset | 快速局 / 标准局 / 休闲局，靠 GameConfig 参数 |
| 14 | AI 2.0 | 人格（稳健/均衡/激进）+ 抵押与变卖决策 + 道具预判 |
| 15 | Modifier 系统 | 角色/状态/卡牌统一挂钩，消除散落的 if |
| 16 | EconomyLedger | 所有资金变化带 reason，统计钱的来源 |
| 17 | 帮助 / 图鉴 | 主菜单与 ESC 均可打开 |
| 18 | 金额格式统一 | 统一 MoneyFormatter |

### P2 —— 收口

| # | 项目 | 目标 |
| --- | --- | --- |
| 19 | 结算增强 | 完整统计 + 趣味称号 |
| 20 | 音效接事件 | 补齐 16 类音效触发 |
| 21 | UI 微动效统一 | 统一 duration / easing |
| 22 | 分辨率与字体验收 | 三档分辨率 × 三档字体 |
| 23 | 余额分析工具 | `tools/balance_simulation.py` + 报告 |
| 24 | 打包 | PyInstaller 脚本，实际 build 并 smoke test |
| 25 | 长时间 soak | 连续多局 + 再来一局 10 次 |
| 26 | README 重写 | 加入规则、Build、Known Issues |

---

## 4. 明确不动的东西

以下已确认工作正常，**不为了「架构好看」重写**：

- `GameState` 的字段设计、`seed + counter` 随机方案、`canonical_hash`；
- `Command` / `PendingDecision` / `Controller` 三层抽象；
- `GameEngine` 的整体方法与分区结构（只做增量）；
- TCP + 长度前缀 + JSON 的传输方案（性能实测无问题）；
- `BoardView` 的静态缓存渲染方案；
- `SceneManager` 的场景栈设计；
- 现有的 106 个测试（只增不改）。

---

## 5. 审计结论

第一版的主体架构是**健康的**：Host 权威、命令-事件-状态分离、
统一控制器、显式阶段机、可复现随机、数据驱动，这些关键决策都成立，
不需要推翻。

本轮的真实工作是三件事：

1. **把规则闭环补完整** —— 债务处理从「自动」变成「玩家参与」，
   并补上抵押与资产面板，让「破产前自救」成为有策略的操作；
2. **把内容做厚** —— 道具 12→20、商店格、机遇正负分离、第二张地图、
   规则 Preset、图鉴，让一局游戏有更多决策点；
3. **把体验收口** —— 演出队列、输入优先级、金额格式、
   统一 Toast/Tooltip、结算增强、打包，让它像一个正式游戏。

以上全部完成后，再跑完整验证矩阵并出报告。
