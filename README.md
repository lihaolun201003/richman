# Richman · 城市之光

一个原创的 **Windows 桌面端局域网多人大富翁**（Monopoly-like 轻策略桌游）。

纯 Python + Pygame 实现，**不需要联网、不需要账号、不需要数据库、不需要浏览器、不需要 Docker**——
同一个 WiFi 下开一个房间，室友输入你的 IP 就能一起玩一整局。

```
2～6 人  ·  真人 + AI 混合  ·  单机 / 局域网  ·  中文界面  ·  免安装 EXE
```

---

## 截图

| 画面 | 文件 |
| --- | --- |
| 主菜单 | `docs/reports/shots/01_menu.png` |
| 单人配置（含地图与规则选择） | `docs/reports/shots/03_local_setup.png` |
| 对局中 | `docs/reports/shots/launch_03_progress.png` |
| 结算界面 | `docs/reports/shots/06e_game_over.png` |
| 三档分辨率 | `docs/reports/shots/07_res_*.png` |

这些截图由 `tools/ui_smoke_test.py` 与 `tools/launch_check.py` 自动生成，不是手绘示意图。

---

## 一分钟上手

```powershell
cd C:\Users\lihao\Desktop\richman
.\.venv\Scripts\python.exe main.py
```

或者直接双击 **`run_game.bat`**。

已有打包好的免安装版本（如果 `dist/` 存在）：

```
dist\Richman\Richman.exe
```

---

## 玩法

**目标**：让其他所有玩家破产，成为最后存活的人。若 200 轮仍未分胜负，按总资产判定。

**一个回合**：

```
回合开始 → 掷骰（两个六面骰）→ 逐格移动 → 落点结算 → 回合结束
```

移动时经过起点拿奖励，撞上路障会被迫停下。落点可能是地产、机遇、税收、
看守所、商店、奖金池——每一种都有对应的处理。

**核心决策点**：

| 系统 | 说明 |
| --- | --- |
| 买地 | 落到无主地产可以买下；同一片区的全部地产到手后，该区租金翻倍 |
| 升级 | 自己的地产最高升到 3 级，界面会直接显示「当前租金 → 升级后租金」 |
| 抵押 | 空地抵押换现金（地价 60%），保留产权但不能收租，之后可按 1.1 倍赎回 |
| 出售 | 永久失去这块地，回收已投入资金的 70% |
| 债务 | 现金不足时**打开债务处理面板自己选**抵押或出售哪块地，凑够欠款；凑不够才破产 |
| 道具 | 21 种，有使用时机限制（掷骰前 / 掷骰后 / 任意时刻…），不能用的会灰掉并说明原因 |
| 商店 | 踩到商店格可以用现金买一张道具卡 |
| 机遇 | 分成「福运格」（只有好事）与「灾祸格」（只有坏事），看图标就知道 |
| 看守所 | 付保释金或掷骰（≥6）离开，关押满 3 回合强制付费释放 |

**角色**：12 位原创角色，每位带一个被动特性（起始资金、购地折扣、收租加成、
抵押评估加成、税率减免…），全部通过统一的 Modifier 系统实现。
每个角色还绑定了 AI 风格（稳健 / 均衡 / 激进），AI 接管时会沿用它。

---

## 单机

1. 主菜单 → **单人游戏**（或按 `Enter`）
2. 填昵称、选人数（2–6）、选地图、选规则预设、选角色
3. 点 **开始游戏**
4. 轮到你时点右侧 **掷骰子**（或按 `空格`）
5. 想管资产就按 `I` 打开资产面板（升级 / 出售 / 抵押 / 赎回）

同一台电脑多人轮流玩也可以：在配置界面点座位卡片，把它从「电脑」切成「真人」，
轮到谁时界面会大字提示。

**规则预设**：

| 预设 | 起始资金 | 轮数上限 | 适合 |
| --- | --- | --- | --- |
| 快速局 | 10,000 | 100 | 20 分钟左右打完一局 |
| 标准局 | 15,000 | 200 | 默认，约 35 分钟 |
| 休闲局 | 22,000 | 260 | 资金充裕，破产更慢，边聊边玩 |

---

## 局域网联机

**房主**

1. 主菜单 → **创建局域网房间**，填房间名与端口（默认 28080）
2. 进入大厅后，右上方显示**本机 IP**（例如 `192.168.1.5`），把它和端口告诉室友
3. 大厅里可以选地图与规则，也可以点「添加电脑玩家」补位
4. 所有人准备好后点 **开始游戏**

**客户端**

1. 确认和房主在**同一个 WiFi / 局域网**下
2. 主菜单 → **加入房间**
3. 右侧会自动列出搜到的房间（也可以手动输入 IP 和端口）
4. 填昵称 → 连接 → 进大厅后按 `R` 准备

**要输入哪个 IP**：房主大厅里「本机 IP」那一行，通常是 `192.168.x.x` 或 `10.x.x.x`。

### 本机双开测试

```powershell
# 终端 A：开房间
.\.venv\Scripts\python.exe main.py --host --name 房主 --port 28080

# 终端 B：连本机
.\.venv\Scripts\python.exe main.py --join 127.0.0.1 --port 28080 --name 客户端
```

也可以直接跑自动化联机验证脚本（会真实建立 TCP 连接、跑完大厅流程与几十个回合）：

```powershell
.\.venv\Scripts\python.exe tools\run_host_client_demo.py --rounds 60 --ai 2
```

### 连不上怎么办

按顺序排查：

1. **Windows 防火墙**：首次开房间时会弹窗，必须勾选「专用网络」并允许。
   误点了取消就手动加规则（管理员 PowerShell）：

   ```powershell
   New-NetFirewallRule -DisplayName "Richman 大富翁" -Direction Inbound `
     -Protocol TCP -LocalPort 28080 -Action Allow -Profile Private
   New-NetFirewallRule -DisplayName "Richman 自动发现" -Direction Inbound `
     -Protocol UDP -LocalPort 28081 -Action Allow -Profile Private
   ```

2. **确认在同一网段**：两边都执行 `ipconfig`，看 IPv4 地址前三段是否一致
   （例如都是 `192.168.1.x`）。手机热点、访客 WiFi 常见「AP 隔离」会阻止互访。
3. **确认端口一致**：端口被占用时程序会自动向后找可用端口（最多试 20 个），
   以大厅里显示的数字为准。
4. **自动发现搜不到**：部分校园网 / 企业网会屏蔽 UDP 广播，直接手动输入 IP 即可。

不需要折腾路由器。

### 掉线与重连

- 客户端掉线后，房主会标记该玩家「掉线」，游戏**继续正常进行**。
- 30 秒内可以用同一个程序重连（自动带 reconnect token）；超时后由 AI 接管。
- 房主退出 = 本局结束，客户端会收到提示并回到主菜单，不会卡死。

---

## 键盘与鼠标

| 操作 | 作用 |
| --- | --- |
| `空格` | 掷骰子 / 确认主要操作 |
| `Esc` | 返回 / 暂停菜单（选卡目标时先取消选择） |
| `I` | 资产面板（升级 / 出售 / 抵押 / 赎回） |
| `H` | 规则与图鉴 |
| `1`～`5` | 使用第 N 张道具卡 |
| `R` | 大厅里切换准备状态 |
| `Enter` | 主菜单快速开始单机 |
| `F1` | 调试信息（阶段 / 修订号 / 状态哈希 / 网络状态） |
| `F2` | 静音开关 |
| `F3` | 循环切换动画速度 0.5× → 1× → 1.5× → 2× |
| `F11` | 全屏 / 窗口切换 |

游戏内按 `H` 可以打开完整的**规则与图鉴**（怎么玩 / 地块图鉴 / 道具图鉴 /
角色图鉴 / 地图 / 快捷键），第一次玩不用翻本文件。

---

## 安装与运行

需要 **Python 3.10 或更高**（Windows）。

```powershell
cd C:\Users\lihao\Desktop\richman

# 创建虚拟环境
py -3.10 -m venv .venv

# 安装依赖（只有 pygame；pytest / pyinstaller 是开发用）
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> PowerShell 可能禁止运行 `Activate.ps1`。上面的写法不需要激活虚拟环境。

启动：

```powershell
.\.venv\Scripts\python.exe main.py
```

### 命令行参数

```powershell
# 直接开房间 / 直接加入
.\.venv\Scripts\python.exe main.py --host --name 阿明 --port 28080
.\.venv\Scripts\python.exe main.py --join 192.168.1.5 --port 28080

# 全屏、动画加速、固定随机种子（调试）
.\.venv\Scripts\python.exe main.py --fullscreen --anim-speed 1.5 --seed 12345

# 启动自检（不打开真实窗口，验证数据 / 引擎 / UI 全链路）
.\.venv\Scripts\python.exe main.py --selftest

# 版本号
.\.venv\Scripts\python.exe main.py --version
```

---

## 打包成 EXE

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe tools\build_windows.py --clean
```

产物：

```
dist\Richman\Richman.exe        主程序
dist\Richman\_internal\         依赖与游戏数据
```

验证打包结果确实能跑（不只是看退出码）：

```powershell
.\dist\Richman\Richman.exe --selftest --selftest-seconds 6
```

想打成单个文件（启动稍慢）：

```powershell
.\.venv\Scripts\python.exe tools\build_windows.py --onefile
```

---

## 项目结构

```
richman/
├── main.py                     程序入口（--host / --join / --selftest / --version）
├── run_game.bat                一键启动（纯 ASCII，避免 cmd 编码问题）
├── requirements.txt
├── config/
│   ├── default.json            规则预设、网络端口、显示默认值
│   └── user_settings.json      用户设置（自动生成，不提交）
├── data/                       全部游戏数据，纯数据驱动
│   ├── default_map.json        城市之光（36 格 / 11×9 网格）
│   ├── map_seaside.json        海滨假日（40 格 / 12×10 网格）
│   ├── properties.json         两张地图的地产与片区定义
│   ├── chance_events.json      37 个机遇/灾祸事件（带正负极性）
│   ├── cards.json              21 种道具卡（带使用时机与商店价格）
│   └── characters.json         12 个角色 + 6 色玩家调色板 + AI 人格
├── src/                        约 17,000 行 Python
│   ├── app.py                  窗口、主循环、场景装配、网络会话
│   ├── version.py              版本号唯一来源
│   ├── game/                   规则层（与 UI、网络完全解耦）
│   │   ├── engine.py           引擎：阶段机 / 命令校验执行 / 债务处理 / 商店
│   │   ├── state.py            GameState：revision、随机种子、日志、流水
│   │   ├── phases.py           显式 GamePhase
│   │   ├── commands.py         Command / PendingDecision
│   │   ├── modifiers.py        Modifier 系统（角色/状态/道具统一挂钩）
│   │   ├── ledger.py           EconomyLedger（所有资金变化的唯一出口）
│   │   ├── format.py           统一金额格式化
│   │   ├── board.py            棋盘模型（网格尺寸与角索引来自地图数据）
│   │   ├── property.py         地产模型（含抵押）
│   │   ├── economy.py          价格 / 租金 / 税收 / 抵押 / 出售
│   │   ├── chance.py           事件注册表 + 正负极性格子
│   │   ├── cards.py            道具注册表 + 使用时机校验
│   │   ├── bankruptcy.py       债务与破产（含「先判断够不够」）
│   │   ├── victory.py          胜负判定 + 趣味称号
│   │   └── ...
│   ├── controllers/            统一决策入口：Local / AI / Remote
│   ├── network/                TCP 协议、Host、Client、大厅、UDP 发现
│   ├── ui/                     主题、控件、场景、棋盘、资产面板、弹窗、动画
│   ├── audio/                  程序化音效（不依赖外部音频文件）
│   ├── persistence/            设置与存档
│   └── utils/                  路径（冻结/开发分离）、日志、缓动
├── tools/                      开发与验证工具
│   ├── gen_data.py             生成地图与地产数值
│   ├── simulate_game.py        AI 长局模拟与异常检测
│   ├── balance_simulation.py   经济平衡分析 + 报告
│   ├── run_host_client_demo.py LAN 双开联机验证
│   ├── ui_smoke_test.py        UI 冒烟测试 + 三档分辨率截图
│   ├── launch_check.py         真实窗口启动自检（含完整对局）
│   ├── soak_test.py            连续多局资源泄漏检查
│   ├── soak_restart.py         「再来一局」反复进行检查
│   └── build_windows.py        PyInstaller 打包
├── tests/                      pytest 规则测试
└── docs/reports/               验证报告与截图
```

### 架构要点

1. **Host 权威**：联机模式下只有房主运行 `GameEngine` 与唯一真实 `GameState`。
   客户端只显示状态、只发 `Command`，永远不自己决定骰子、位置、钱、租金、胜负。
2. **UI 不碰状态**：点击 → `Command` → 控制器 → 引擎 → 状态变化 → 渲染器读状态。
   单机也走完全相同的命令流程，所以联机不需要另写一套规则。
3. **统一控制器**：真人 / AI / 远程玩家是同一个 `BaseController` 接口。
4. **统一决策**：所有「等待操作」都是 `PendingDecision`，提交时必须带 `decision_id`，
   从机制上避免「旧窗口回答新问题」和重复点击。
5. **显式阶段机**：`TURN_START → WAIT_ROLL → ROLLING → MOVING → ARRIVED →
   RESOLVE_TILE →（SHOP / DEBT_RESOLUTION / WAIT_DECISION）→ TURN_END`。
6. **可复现随机**：所有随机数由 `seed + counter` 决定，存档能完整还原同一局。
7. **数值统一出口**：所有加成走 `modifiers.resolve()`，
   所有资金变化走 `ledger.gain/pay_bank/transfer()`，
   因此经济报表和平衡分析都能给出真实数据。
8. **数据驱动**：地图、地产、事件、道具、角色全在 `data/*.json`，换地图不改引擎。

---

## 开发

### 跑测试与验证

```powershell
# 规则测试
.\.venv\Scripts\python.exe -m pytest tests\ -q

# AI 长局（检查死锁 / 非法状态）
.\.venv\Scripts\python.exe tools\simulate_game.py --games 100 --players 4

# 经济平衡分析（生成 docs/reports/balance_report_v01.md）
.\.venv\Scripts\python.exe tools\balance_simulation.py --games 20

# 局域网双开验证（真实 TCP）
.\.venv\Scripts\python.exe tools\run_host_client_demo.py --rounds 60 --ai 2

# UI 冒烟 + 三档分辨率截图
.\.venv\Scripts\python.exe tools\ui_smoke_test.py

# 真实窗口启动自检（含完整对局）
.\.venv\Scripts\python.exe tools\launch_check.py --full-game

# 连续多局 / 再来一局的资源泄漏检查
.\.venv\Scripts\python.exe tools\soak_test.py --games 10
.\.venv\Scripts\python.exe tools\soak_restart.py --games 6
```

### 改数值

- `config/default.json` → `gameplay` 段：基础数值
- `config/default.json` → `presets` 段：快速 / 标准 / 休闲三个预设
- `tools/gen_data.py`：租金与升级费用系数、地产价格与片区划分

改完执行 `python tools\gen_data.py` 重新生成地图数据，
再跑 `tools/balance_simulation.py` 看时长有没有失控。

### 加内容

- **加事件**：往 `data/chance_events.json` 加一条，标好 `polarity`
  （fortune / disaster / neutral），`effect.kind` 用 `src/game/chance.py` 已有类型即可。
- **加道具**：往 `data/cards.json` 加一条，写清 `timing` 与 `shop_price`，
  `effect.kind` 需要在 `engine._apply_card` 里有分支（新增效果才需要写代码）。
- **加角色**：往 `data/characters.json` 加一条，`perk.modifiers` 里声明数值影响，
  完全不需要改代码。
- **加地图**：改 `tools/gen_data.py` 的 `MAPS` 列表（声明 cols / rows / 布局 / 价格），
  重新生成即可；棋盘渲染会自动适配网格尺寸。

---

## 已知问题

- **跨机联机未在真实两台电脑上验证过**：开发机只有一台，验证用的是同一进程内的
  两个真实 TCP 连接。防火墙、AP 隔离这类环境问题没有踩过。
- **重连逻辑完整但只验证了「掉线不毁局」**，没有做「拔网线再插回来」的真机测试。
- **UDP 自动发现没跨机验证**，部分网络会屏蔽广播包（手动输入 IP 是保底方案）。
- **没有人类连续长时间试玩过**：真实窗口下的完整对局是用自动化点击跑完的，
  手感、节奏这类主观体验仍待真人确认。
- 6 人局偏长（约 60 分钟），如果想更快可以调高 `config/default.json` 里
  `presets.quick` 的租金系数或降低 `max_rounds`。
- 没有回放 / 撤销；没有地图编辑器。

---

## 许可

本项目为原创实现，仅用于学习与自娱。
架构上参考过 `mine-monopoly` 的思路（Host 权威、阶段系统、数据驱动），
但**没有复制其任何源码或美术资源**。游戏内所有图形均由 Pygame 程序化绘制，
音效由程序合成，不包含任何第三方素材。
