# Richman 大富翁 · 城市之光

一个原创的 **Windows 桌面端局域网多人大富翁**（Monopoly-like 轻策略休闲游戏）。
纯 Python + Pygame 实现，**不需要联网、不需要账号、不需要数据库、不需要浏览器**——
同一局域网里开一个房间，室友连上就能一起玩。

- 2～6 名参与者，支持真人 + AI 混合
- 支持单机（1 人打电脑，或同一台电脑多人轮流）
- 支持局域网联机：一台机器建房（Host），其他机器通过局域网 IP 加入（Client）
- 完整的一局：掷骰 → 逐格移动 → 买地 → 升级 → 收租 → 机遇 → 道具 → 破产 → 结算

---

## 截图

（运行后可自行截图；下面几个位置留给实机画面）

| 画面 | 文件 |
| --- | --- |
| 主菜单 | `docs/reports/shots/01_menu.png` |
| 单机配置 | `docs/reports/shots/03_local_setup.png` |
| 对局中 | `docs/reports/shots/launch_03_progress.png` |
| 结算界面 | `docs/reports/shots/06e_game_over.png` |
| 三档分辨率 | `docs/reports/shots/07_res_1280x720_game.png` 等 |

仓库里已经带了这些由 `tools/ui_smoke_test.py` 与 `tools/launch_check.py` 自动生成的截图。

---

## 功能一览

**规则**

- 36 格原创城市棋盘（10×8 网格环形布局，非传统方框）
- 24 处可购买地产，分属 11 个片区；集齐一个片区即 **垄断**，该区租金翻倍
- 地产可升级 3 级（空地 → 小建筑 → 中型建筑 → 高级建筑），租金逐级提高
- 6 种格子：起点、地产、交通枢纽、机遇、税收、看守所、公园 / 奖金池、治安巡查
- 37 个机遇事件（资金类 / 移动类 / 多人互动类 / 地产类 / 状态类 / 奖励 / 惩罚）
- 12 种道具卡（免租、双倍租金、传送、交换位置、抢夺、保护、停留、指定骰子、路障、免费升级、折扣购地、现金）
- 看守所：可付保释金或掷骰（≥6 点）脱身，关押 3 回合后强制付费释放
- 税收：固定营业税 + 按资产比例的奢侈税，税金的一半注入城市奖金池
- 奖金池：踩到中央公园或奖金池格可全部领走
- 路障：放在格子上，经过的玩家会被拦下
- 破产：现金不足先自动变卖资产抵债；变卖全部仍不足则破产退出，资产整体移交债权人
- 8 个可玩角色，每位带一个轻量特性（起始资金、购地折扣、收租加成等）

**AI**

- 规则式启发式 AI，会买地、会升级、会针对领先者、会自保、会使用道具与选目标
- 难度可切换（简单 / 普通）

**联机**

- Host 权威架构：**只有 Host 运行游戏引擎**，客户端只读状态、只发意图
- TCP + 4 字节长度前缀 + UTF-8 JSON
- UDP 广播自动发现房间，也可手动输入 IP
- 断线不毁局：玩家标记掉线，30 秒内可重连，超时由 AI 接管
- Host / Client 状态一致性探针（canonical hash）

**其它**

- 存档：单机每回合自动存档 + 手动存档，读档后可继续（随机数状态一并保存）
- 设置：主音量 / 音乐 / 音效、动画速度 0.5×–2.0×、字体大小、分辨率、全屏、昵称
- 程序化音效（不依赖任何外部音频文件，缺资源也不会崩）
- 中文界面，自动在系统中寻找可用中文字体
- 三档分辨率（1280×720 / 1600×900 / 1920×1080）等比缩放，不断裂不变形
- 调试信息浮层（F1）

---

## 安装

需要 **Python 3.10 或更高版本**（Windows）。

```powershell
# 1. 进入项目目录
cd C:\Users\lihao\Desktop\richman

# 2. 创建虚拟环境
py -3.10 -m venv .venv

# 3. 安装依赖
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> PowerShell 可能因为执行策略禁止运行 `Activate.ps1`。
> 上面这条命令不需要激活虚拟环境，直接调用 `.venv` 里的解释器即可。

---

## 启动

### 最省事：双击

```
run_game.bat
```

`.bat` 只是调用 `.venv\Scripts\python.exe main.py`，
如果环境不存在会明确提示你怎么建，**不会自动下载任何东西**。

### 命令行

```powershell
.\.venv\Scripts\python.exe main.py
```

### 启动参数（可选）

```powershell
# 直接开一个局域网房间
.\.venv\Scripts\python.exe main.py --host --name 阿明 --port 28080

# 直接连到某台机器
.\.venv\Scripts\python.exe main.py --join 192.168.1.5 --port 28080 --name 小林

# 全屏 + 动画加速 + 固定随机种子（调试）
.\.venv\Scripts\python.exe main.py --fullscreen --anim-speed 1.5 --seed 12345
```

---

## 怎么玩

### 单机

1. 主菜单 → **单人游戏**（或直接按 `Enter`）
2. 填写昵称，选择参与人数（2–6）与自己的角色
3. 点 **开始游戏**
4. 轮到自己的时候点右侧 **掷骰子**（或按 `空格`）
5. 落到无主地产会弹出购买窗口；落到自己的地产可以升级；落到别人的地产要付租
6. 右侧是我的道具（`1`–`5` 键快速使用），右上角是轮次与当前行动人
7. 只剩最后一人未破产时自动结算

> 同一台电脑想多人轮流玩也可以：在配置界面把座位的主角切换成「真人」即可，
> 谁轮到谁操作，界面会大字提示该谁行动。

### 局域网（两台电脑）

**房主（Host）**

1. 主菜单 → **创建局域网房间**
2. 填房间名、端口（默认 28080），选角色，点 **创建房间**
3. 进入大厅后，右上方会显示本机的局域网 IP，例如 `192.168.1.5`
4. 把 **IP 和端口** 告诉室友
5. 其他人准备好后（房主可以自己点「添加电脑玩家」补位），点 **开始游戏**

**客户端（Client）**

1. 确认和房主在**同一个局域网 / 同一个 WiFi** 下
2. 主菜单 → **加入房间**
3. 右侧会自动列出搜索到的房间，也可以手动输入房主的 IP 与端口
4. 填昵称、选角色，点 **连接**
5. 进入大厅后按 `R` 或点按钮表示 **准备好了**
6. 等房主开始游戏

**需要输入哪个 IP？** 房主大厅界面里「本机 IP」那一行，
通常是 `192.168.x.x` 或 `10.x.x.x` 这种内网地址。

### 一台电脑上双开测试

不需要第二台电脑，本机开两个程序即可：

```powershell
# 终端 A：开房间
.\.venv\Scripts\python.exe main.py --host --name 房主 --port 28080

# 终端 B：连本机
.\.venv\Scripts\python.exe main.py --join 127.0.0.1 --port 28080 --name 客户端
```

在 B 里按 `R` 准备，回到 A 点开始游戏即可。

也可以直接跑自动化验证脚本（会真实建立 TCP 连接、跑完大厅流程与几十个回合）：

```powershell
.\.venv\Scripts\python.exe tools\run_host_client_demo.py --rounds 60 --ai 2
```

### Windows 防火墙

第一次开房间时 Windows 会弹出防火墙询问，**请勾选「专用网络」并允许访问**。
如果误点了「取消」，朋友会连不上；补救办法：

```powershell
# 以管理员身份运行 PowerShell
New-NetFirewallRule -DisplayName "Richman 大富翁" -Direction Inbound `
  -Protocol TCP -LocalPort 28080 -Action Allow -Profile Private
New-NetFirewallRule -DisplayName "Richman 自动发现" -Direction Inbound `
  -Protocol UDP -LocalPort 28081 -Action Allow -Profile Private
```

如果端口被占用，房主程序会**自动向后寻找可用端口**（最多试 20 个），
请以大厅里显示的端口为准。

---

## 快捷键

| 按键 | 作用 |
| --- | --- |
| `Enter` | 主菜单：直接开始单机 |
| `Esc` | 返回 / 暂停菜单（联机时只影响本机，不能暂停别人） |
| `空格` | 掷骰子 |
| `1`–`5` | 使用第 N 张道具卡 |
| `R` | 大厅里切换准备状态 |
| `F1` | 调试信息浮层（phase / revision / 状态哈希 / 网络状态） |
| `F2` | 静音开关 |
| `F3` | 循环切换动画速度 0.5× → 1× → 1.5× → 2× |
| `F11` | 全屏 / 窗口 |

---

## 项目结构

```
richman/
├── main.py                 程序入口
├── run_game.bat            一键启动
├── requirements.txt
├── config/
│   ├── default.json        默认配置（规则数值、网络端口、显示参数）
│   └── user_settings.json  用户设置（首次保存后生成，不提交到 git）
├── data/                   全部游戏数据，纯数据驱动
│   ├── default_map.json    36 格地图
│   ├── properties.json     地产定义与片区、租金表
│   ├── chance_events.json  37 个机遇事件
│   ├── cards.json          12 种道具卡
│   └── characters.json     8 个角色 + 6 色玩家调色板
├── saves/                  存档（自动存档 autosave.json）
├── src/
│   ├── app.py              应用主体：窗口、主循环、场景装配、网络会话
│   ├── game/               游戏规则（与 UI、网络完全解耦）
│   │   ├── engine.py       引擎：阶段机、命令校验执行、状态变更
│   │   ├── state.py        GameState：revision、随机种子、日志
│   │   ├── phases.py       显式 GamePhase 定义
│   │   ├── commands.py     Command / PendingDecision 模型
│   │   ├── board.py        棋盘模型（环形索引、路径、经过起点）
│   │   ├── property.py     地产模型
│   │   ├── player.py       玩家与状态效果
│   │   ├── economy.py      价格 / 租金 / 税收计算（唯一出口）
│   │   ├── movement.py     移动规划与路障拦截
│   │   ├── chance.py       机遇事件注册表与效果执行
│   │   ├── cards.py        道具卡注册表
│   │   ├── jail.py         看守所
│   │   ├── bankruptcy.py   债务与破产清算
│   │   ├── victory.py      胜负判定与结算统计
│   │   ├── serializer.py   状态序列化 / 存档 / 版本校验
│   │   └── setup.py        对局装配（单机与 Host 共用）
│   ├── controllers/        统一决策入口：Local / AI / Remote
│   ├── network/            TCP 协议、Host、Client、大厅、UDP 发现
│   ├── ui/                 主题、控件、场景、棋盘渲染、弹窗、动画
│   ├── audio/              音频管理（程序化音效）
│   ├── persistence/        设置与存档
│   └── utils/              路径、日志、缓动
├── tools/                  开发与验证工具
│   ├── gen_data.py         生成地图与地产数值
│   ├── simulate_game.py    AI 长局模拟（无 UI）
│   ├── run_host_client_demo.py  LAN 双开联机验证
│   ├── ui_smoke_test.py    UI 冒烟测试 + 三档分辨率截图
│   └── launch_check.py     真实窗口启动自检
├── tests/                  pytest 规则测试（106 个）
└── docs/reports/           验证报告与截图
```

### 架构要点

1. **Host 权威**：局域网模式下只有 Host 运行 `GameEngine` 和唯一真实的 `GameState`。
   客户端只显示状态、只发 `Command`，永远不自己决定骰子、位置、钱、租金、胜负。
2. **UI 不碰状态**：按钮点击 → `Command` → 控制器 → 引擎 → 状态变化 → 渲染器读取状态。
   单机模式也走完全相同的命令流程，所以联机不需要另写一套规则。
3. **统一控制器**：真人（`LocalController`）、AI（`AIController`）、远程玩家（`RemoteController`）
   是同一个接口，引擎只向「当前玩家的控制器」请求一个 Command。
4. **统一决策**：所有「等待真人操作」都表达为 `PendingDecision`，
   提交时必须带 `decision_id`，从根本上避免「旧窗口回答新问题」和重复点击。
5. **显式阶段机**：`GAME_SETUP → TURN_START → WAIT_ROLL → ROLLING → MOVING → ARRIVED
   → RESOLVE_TILE →（必要时 WAIT_DECISION）→ TURN_END → 下一玩家`，
   没有用一堆布尔量表达流程。
6. **可复现随机**：所有随机数由 `seed + counter` 决定，因此存档能完整还原、
   同一 seed 能复现同一局。
7. **数据驱动**：地图、地产、事件、道具、角色全在 `data/*.json`，
   换地图不需要改引擎代码。

---

## 开发说明

### 跑测试

```powershell
# 规则测试（106 个）
.\.venv\Scripts\python.exe -m pytest tests\ -q

# AI 长局模拟：100 局，检查是否有死锁 / 非法状态
.\.venv\Scripts\python.exe tools\simulate_game.py --games 100 --players 4

# 局域网双开联机验证（真实 TCP，跑大厅 + 多回合 + 断线）
.\.venv\Scripts\python.exe tools\run_host_client_demo.py --rounds 60 --ai 2

# UI 冒烟测试 + 三档分辨率截图
.\.venv\Scripts\python.exe tools\ui_smoke_test.py

# 真实窗口启动自检（用真实 SDL 视频驱动开窗口）
.\.venv\Scripts\python.exe tools\launch_check.py
```

### 改数值

平衡数值集中在两处：

- `config/default.json` → `gameplay` 段：初始资金、经过起点奖励、保释金、税率、最大轮数等
- `tools/gen_data.py`：租金系数、升级费用系数、地产价格与片区划分。
  改完执行 `python tools\gen_data.py` 会重新生成 `data/default_map.json` 与 `data/properties.json`

改完建议跑 `tools\simulate_game.py --games 50` 看平均轮数有没有失控。

### 加内容

- **加机遇事件**：往 `data/chance_events.json` 加一条，`effect.kind` 用
  `src/game/chance.py` 里已有的类型即可，不需要写代码。
- **加道具卡**：往 `data/cards.json` 加一条，`effect.kind` 用 `src/game/engine.py`
  的 `_apply_card` 里已支持的类型。
- **加角色**：往 `data/characters.json` 加一条，`perk.kind` 用
  `src/game/economy.py` / `player.py` 里已解释的类型。
- **换地图**：改 `tools/gen_data.py` 的 `LAYOUT` 与 `PRICES`（格子数需为 4 的倍数加角格配置一致），
  再调整 `src/ui/board_view.py` 里的 `COLS` / `ROWS` / 角格索引。

---

## 已知限制

- 联机模式只支持**局域网**（同一网段），没有公网穿透，也不打算做。
- 重连依赖房主保留座位 30 秒；超过后由 AI 接管，此时原玩家回来会作为新玩家排队。
- 结算界面在联机模式下由房主的「再来一局」决定，客户端只能返回大厅。
- 没有地图编辑器，第二张地图需要按上面的步骤手工加。

---

## 许可

本项目为原创实现，仅用于学习与自娱。
参考过 `mine-monopoly` 的架构思路（Host 权威、阶段系统、数据驱动），
但**没有复制其任何源码或美术资源**。
