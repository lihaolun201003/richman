# Richman · 城市之光

一款 **Windows 桌面端的局域网多人大富翁**（原创实现，非任何官方版本的移植）。

两个人、两台电脑、同一个 WiFi，一个开房间、另一个输入 IP，就能一起玩一整局。
**不需要联网、不需要账号、不需要数据库、不需要浏览器、不需要服务器。**

```
2～6 人 · 真人 + AI 混合 · 单机 / 局域网 · 中文界面 · 免安装 EXE
```

---

## 截图

| 画面 | 文件 |
| --- | --- |
| 主菜单 | `docs/reports/shots_v03/v03_menu.png` |
| 单人配置（12 个角色的能力卡） | `docs/reports/shots_v03/v03_local_setup.png` |
| 对局中（棋盘 / 片区色 / 建筑 / 资金浮字） | `docs/reports/shots_v03/v03_game_late.png` |
| 资产与债务面板 | `docs/reports/shots_v03/v03_modal_AssetPanel.png` |
| 结算界面 | `docs/reports/shots_v03/v03_game_over.png` |
| 局域网诊断 | `docs/reports/shots_v03/v03_net_diag.png` |

截图由 `tools/ui_shots.py`、`tools/ui_smoke_test.py`、`tools/launch_check.py`
自动生成，不是手绘示意图，也不是后期修的图。

---

## 一分钟上手（EXE）

1. 把 `Richman-v0.3.0-windows.zip` 解压到任意目录（**不要只复制 exe**，
   整个 `Richman` 文件夹才有运行所需的文件）
2. 双击 `Richman.exe`
3. 想自己玩：主菜单 →「单人游戏」→ 选人数/角色/地图 → 开始
4. 想联机：一个人点「创建局域网房间」，另一个人点「加入房间」

### 联机三步走

```
房主：创建局域网房间 → 大厅里会显示「本机 IP」→ 点「复制连接信息」
朋友：加入房间 → 粘贴 IP:端口 → 选角色 → 按 R 准备
房主：看到「已准备」→ 点开始游戏 → 两边同时进入对局
```

**要输入哪个 IP**：房主大厅里那一行大字，通常是 `192.168.x.x` 或 `10.x.x.x`。
不确定就连不上时，打开主菜单 →「局域网诊断」，它会告诉你本机 IP、
端口状态和下一步该做什么。

### 连不上怎么办（按顺序排查）

1. **确认同一个局域网**：两台电脑连同一个 WiFi / 同一台路由器。
   手机热点、访客 WiFi 常见「AP 隔离」，会直接阻止两台机器互访。
2. **确认网段一致**：两边都执行 `ipconfig`，看 IPv4 前三段是否相同
   （例如都是 `192.168.1.x`）。
3. **放行 Windows 防火墙**：首次开房间时会弹窗，必须勾选「专用网络」并允许。
   误点了取消就在**管理员** PowerShell 里执行：

   ```powershell
   New-NetFirewallRule -DisplayName "Richman 大富翁" -Direction Inbound `
     -Protocol TCP -LocalPort 28080 -Action Allow -Profile Private
   New-NetFirewallRule -DisplayName "Richman 自动发现" -Direction Inbound `
     -Protocol UDP -LocalPort 28081 -Action Allow -Profile Private
   ```

4. **搜不到房间不要紧**：自动发现（UDP 广播）在部分校园网/企业网会被屏蔽，
   **手动输入 IP 永远是保底方案**，不影响联机。
5. **端口**：默认 28080，被占用时程序会自动往后找（最多试 20 个），
   以大厅里显示的数字为准。

### 掉线与重连

- 客户端掉线后，屏幕会显示「与房主的连接已中断 / 正在尝试重新连接…」并倒计时。
- 房主那边游戏**继续正常进行**，日志提示「XX 与房间失去连接」。
- 30 秒内网络恢复，客户端会**自动重连成功并继续操作**。
- 超过 30 秒由 AI 接管该座位；之后即使再晚一点连回来，也能**重新接回操作**。
- 房主退出 = 本局结束，客户端会收到提示并回到主菜单，不会卡死。

---

## 玩法

**目标**：让其他所有玩家破产，成为最后存活的人。若达到轮数上限仍未分胜负，
按总资产排名判定。

**一个回合**：

```
回合开始 → 掷骰（两个六面骰）→ 逐格移动 → 落点结算 → 回合结束
```

**棋盘上怎么看信息**（v0.3 重新设计过）：

| 位置 | 含义 |
| --- | --- |
| 格子外缘的粗色带 | 这块地属于哪个**片区**（未购买也能看出） |
| 格子内的小图标 | 格子类型：房子=地产、塔=枢纽、四叶草=福运、警告三角=灾祸、袋子=商店、% =税收、树=公园、旗=起点 |
| 格子中间的建筑 | **等级**：1 层楼 = 1 级，2 层 = 2 级，金色高层 = 3 级；空地是虚框 |
| 格子内缘的细色带 + 编号 | **所有者**（颜色 + 玩家编号，色觉差异也能分辨） |
| 斜纹 + 锁 | 该地产**已抵押** |
| 金色描边 | 该片区已被某个玩家**集齐（垄断）**，该区租金翻倍 |

**核心决策点**：

| 系统 | 说明 |
| --- | --- |
| 买地 | 落到无主地产可以买下；同一片区全部到手后，该区租金翻倍 |
| 升级 | 自己的地产最高 3 级，面板直接显示「当前租金 → 升级后租金」 |
| 抵押 | 空地抵押换现金（地价 60%），保留产权但不能收租，之后按 1.1 倍赎回 |
| 出售 | 永久失去这块地，回收已投入资金的 70% |
| 债务 | 现金不足时**打开债务处理面板自己选**抵押或出售哪块地；凑不够才破产 |
| 道具 | 21 种，有使用时机限制；不能用的卡会灰化并**写明原因** |
| 商店 | 踩到商店格可以用现金买一张道具卡 |
| 机遇 | 「福运格」只有好事（绿色卡片），「灾祸格」只有坏事（红色卡片） |
| 看守所 | 付保释金或掷骰（≥6）离开，关押满 3 回合强制付费释放 |

**规则预设**：

| 预设 | 起始资金 | 经过起点 | 轮数上限 | 适合 |
| --- | --- | --- | --- | --- |
| 快速局 | 10,000 | +1,500 | 100 | 20 分钟左右打完 |
| 标准局 | 15,000 | +2,000 | 200 | 默认，约 35 分钟 |
| 休闲局 | 22,000 | +2,500 | 260 | 资金充裕，边聊边玩 |

---

## 角色 / 地图 / 道具简介

**12 位角色**，每位一个被动（全部由数据文件声明，不是写死的代码分支）：

| 角色 | 称号 | 被动 |
| --- | --- | --- |
| 阿金 | 小老板 | 起始资金 +1,500 |
| 小满 | 快递骑手 | 经过起点额外 +600 |
| 老陈 | 二手房东 | 购买地产价格 −8% |
| 娜娜 | 网红主播 | 收取租金 +12% |
| 铁蛋 | 装修队长 | 保释金 −50% |
| 博楚 | 大学讲师 | 机遇事件收益 +20% |
| 阿飞 | 工地工头 | 地产升级费用 −10% |
| 胖虎 | 烧烤摊主 | 支付租金 −10% |
| 老周 | 杂货铺老板 | 商店购买道具 −25% |
| 阿珍 | 典当行掌柜 | 出售地产回收 +15% |
| 强哥 | 银行信贷员 | 抵押可多拿 30% 现金 |
| 小赵 | 税务师 | 缴纳税款 −30% |

角色选择界面里每位都有一张能力卡：头像、称号、一句话风格、被动数值、
以及 AI 接管时使用的性格（稳健 / 均衡 / 激进）。**所有数字都来自真实数据文件。**

**2 张地图**：

| 地图 | 格数 | 网格 | 地产 | 片区 | 推荐人数 |
| --- | --- | --- | --- | --- | --- |
| 城市之光 | 36 | 11×9 | 24 | 11 | 2～5 人 |
| 海滨假日 | 40 | 12×10 | 26 | 14 | 3～6 人 |

**21 种道具**，分四类：经济（免租卡、双倍租金卡、现金卡、折扣购地卡、财富税卡、
幸运卡）、移动（传送卡、指定骰子卡、再次行动卡、交换位置卡）、
攻击（抢夺卡、停留卡、路障卡、强制降级卡、产权置换卡、封锁卡）、
防御（保护卡、事件免疫卡、净化卡、保释券）、地产（免费升级卡）。

在游戏里按 `H` 可以打开完整的**规则与图鉴**（怎么玩 / 地块 / 道具 / 角色 /
地图 / 快捷键），不用翻本文件。

---

## 键盘与鼠标

| 操作 | 作用 |
| --- | --- |
| `空格` | 掷骰子 / 关闭当前事件卡 |
| `Esc` | 返回 / 暂停菜单（选道具目标时先取消选择） |
| `I` | 资产面板（升级 / 出售 / 抵押 / 赎回） |
| `1`～`5` | 使用第 N 张道具卡 |
| `H` | 规则与图鉴 |
| `T` | 主菜单打开新手教程 |
| `R` | 大厅里切换准备状态 |
| `Enter` | 主菜单快速开始单机 / 大厅里开始游戏 |
| `F1` | 调试信息（阶段 / 修订号 / 状态哈希 / 网络状态） |
| `F2` | 静音开关 |
| `F3` | 循环切换动画速度 |
| `F11` | 全屏 / 窗口切换 |

---

## 存档与设置位置

都在**可写的数据目录**里，打包后与 exe 同级，开发时在项目目录：

```
dist\Richman\config\user_settings.json     设置（音量 / 节奏 / 字体 / 分辨率 / 昵称）
dist\Richman\saves\*.json                  存档（自动存档 + 手动存档）
dist\Richman\logs\richman.log              运行日志（出问题时看这里）
```

自动存档在每个回合结束后写入（做了节流：至少间隔 20 秒且至少 4 个回合）。
设置界面里会直接显示当前使用的设置文件完整路径。

---

## Windows EXE

**发布包**（推荐）：

```
dist\Richman-v0.3.0-windows.zip     解压后双击 Richman.exe 即可玩
```

**自己构建**：

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe tools\build_windows.py --clean
```

产物：

```
dist\Richman\Richman.exe        主程序
dist\Richman\_internal\         依赖与游戏数据（必须一起复制）
```

> **onedir 形式，不能只复制 `Richman.exe`**。
> 要么复制整个 `Richman` 文件夹，要么直接用上面那个 zip。

验证打包结果确实能启动（不只是看退出码）：

```powershell
.\dist\Richman\Richman.exe --selftest --selftest-seconds 6
```

---

## 从源码运行（开发者）

需要 **Python 3.10 或更高**（Windows）。

```powershell
cd C:\Users\lihao\Desktop\richman
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

或者双击 `run_game.bat`（纯 ASCII，避免 cmd 编码问题）。

### 命令行参数

```powershell
.\.venv\Scripts\python.exe main.py --host --name 阿明 --port 28080
.\.venv\Scripts\python.exe main.py --join 192.168.1.5 --port 28080
.\.venv\Scripts\python.exe main.py --fullscreen --anim-speed 1.5 --seed 12345
.\.venv\Scripts\python.exe main.py --selftest        # 不打开窗口的全链路自检
.\.venv\Scripts\python.exe main.py --version
```

### 本机双开联机（一台电脑验证联机）

```powershell
# 终端 A
.\.venv\Scripts\python.exe main.py --host --name 房主 --port 28080
# 终端 B
.\.venv\Scripts\python.exe main.py --join 127.0.0.1 --port 28080 --name 客户端
```

---

## 项目结构

```
richman/
├── main.py                     程序入口（--host / --join / --selftest / --version）
├── run_game.bat                一键启动
├── config/default.json         规则预设、网络端口、显示默认值
├── data/                       全部游戏数据，纯数据驱动
│   ├── default_map.json        城市之光（36 格 / 11×9）
│   ├── map_seaside.json        海滨假日（40 格 / 12×10）
│   ├── properties.json         两张地图的地产与片区定义
│   ├── chance_events.json      37 个机遇 / 灾祸事件（带正负极性）
│   ├── cards.json              21 种道具卡（使用时机 + 商店价格 + 图标名）
│   └── characters.json         12 个角色 + 6 色玩家调色板 + AI 人格
├── src/
│   ├── app.py                  窗口、主循环、场景装配、网络会话、自动重连
│   ├── version.py              版本号唯一来源
│   ├── game/                   规则层（与 UI、网络完全解耦）
│   │   ├── engine.py           阶段机 / 命令校验执行 / 债务处理 / 商店
│   │   ├── state.py            GameState：revision、随机种子、日志、流水
│   │   ├── phases.py           显式 GamePhase
│   │   ├── commands.py         Command / PendingDecision
│   │   ├── modifiers.py        Modifier 系统（角色 / 状态 / 道具统一挂钩）
│   │   ├── ledger.py           EconomyLedger（所有资金变化的唯一出口）
│   │   ├── economy.py          价格 / 租金 / 税收 / 抵押 / 出售
│   │   └── ...                 board / property / chance / cards / jail / victory
│   ├── controllers/            统一决策入口：Local / AI / Remote
│   ├── network/                TCP 协议、Host、Client、大厅、UDP 发现、自动重连
│   ├── ui/                     主题与设计系统、矢量图标、控件、场景、棋盘、
│   │                           资产面板、演出层、角色卡、教程、诊断
│   ├── audio/                  程序化音效（不依赖外部音频文件）
│   ├── persistence/            设置与存档
│   └── utils/                  路径（冻结 / 开发分离）、日志、剪贴板、缓动
├── tools/                      开发与验证工具（见下表）
├── tests/                      pytest 规则测试（138 个）
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
   所有资金变化走 `ledger.gain / pay_bank / transfer()`；
   界面的资金浮字与结算报表都从账本读，不存在第二套经济事件来源。
8. **演出不参与规则**：动画、事件卡、资金浮字全部只读状态，
   引擎按自己的节奏推进；所有演出都能点击跳过，不会卡住玩家。
9. **数据驱动**：地图、地产、事件、道具、角色全在 `data/*.json`，换地图不改引擎。

---

## 测试与验证

```powershell
# 规则测试（138 个）
.\.venv\Scripts\python.exe -m pytest tests\ -q

# AI 长局：检查死锁 / 非法状态
.\.venv\Scripts\python.exe tools\simulate_game.py --games 100 --players 4

# 经济平衡分析（生成 docs/reports/balance_report_v03.md）
.\.venv\Scripts\python.exe tools\balance_simulation.py --games 20

# 局域网双开验证（真实 TCP，含状态一致性比对）
.\.venv\Scripts\python.exe tools\run_host_client_demo.py --rounds 60 --ai 2

# 断线重连验证（拔网线 → 重连 → 继续操作；超时 AI 接管 → 接回控制权）
.\.venv\Scripts\python.exe tools\reconnect_demo.py --port 28220 --grace 4

# UI 冒烟 + 三档分辨率截图
.\.venv\Scripts\python.exe tools\ui_smoke_test.py

# 界面巡检截图（主菜单 / 配置 / 对局各阶段 / 资产 / 债务 / 商店 / 结算 / 教程）
.\.venv\Scripts\python.exe tools\ui_shots.py

# 真实窗口启动自检（含完整对局；实测 200 轮 / 646 回合 / 191 秒跑完）
.\.venv\Scripts\python.exe tools\launch_check.py --full-game

# 真实窗口逐项自检（主菜单 / 设置 / 存档 / 对局 / 退出，逐项 PASS/FAIL）
.\.venv\Scripts\python.exe main.py --launch-check
.\dist\Richman\Richman.exe --launch-check     # 打包产物同样支持

# 连续多局 / 「再来一局」的资源泄漏检查
.\.venv\Scripts\python.exe tools\soak_test.py --games 10
.\.venv\Scripts\python.exe tools\soak_restart.py --games 6
```

### 改数值 / 加内容

- `config/default.json` → `gameplay` 段：基础数值；`presets` 段：三个预设
- `tools/gen_data.py`：租金与升级费用系数、地产价格与片区划分
  （改完执行 `python tools\gen_data.py` 重新生成地图数据）
- **加事件**：往 `data/chance_events.json` 加一条，标好 `polarity`
  （fortune / disaster / neutral）
- **加道具**：往 `data/cards.json` 加一条，写清 `timing` 与 `shop_price`，
  `icon` 用 `src/ui/icons.py` 里已有的图标名（新增图标才需要写代码）
- **加角色**：往 `data/characters.json` 加一条，`perk.modifiers` 里声明数值影响，
  完全不需要改代码
- **加地图**：改 `tools/gen_data.py` 的 `MAPS` 列表，重新生成即可；
  棋盘渲染会自动适配网格尺寸

---

## 已知限制

1. **跨机联机没有在真实两台电脑上验证过**（开发环境只有一台机器）。
   验证用的是同一进程内的两个真实 TCP 连接。
   防火墙、AP 隔离、多网卡选错 IP 这类环境问题没有被真的踩过。
   → 请按 `docs/playtest/MANUAL_PLAYTEST_CHECKLIST.md` 实测一遍。
2. **重连已用真实 TCP 自动化验证**（`tools/reconnect_demo.py` 18/18 通过，
   含「模拟拔网线 → 重连 → 继续操作」与「超时 AI 接管 → 接回控制权」），
   但**没有物理拔过网线**。
3. **UDP 自动发现同样只在本机回环验证过**。
   部分网络会屏蔽广播包 —— 手动输入 IP 是保底方案，功能不受影响。
4. **没有人类长时间连续试玩过**：真实窗口下的完整对局是自动化点击跑完的；
   手感、节奏、弹窗频率这类主观体验仍需真人确认。
5. **6 人局偏长**（约 60 分钟）。想更快可以用「快速局」预设，
   或在设置里把游戏节奏调到「快」。
6. 没有回放 / 撤销 / 地图编辑器；存档没有跨版本迁移（有版本守卫，会明确报错而不是崩）。
7. 没有背景音乐资源（音效是程序合成的；BGM 接口已预留，放入
   `assets/audio/bgm_main.ogg` 即会自动播放）。

---

## 许可

本项目为原创实现，仅用于学习与自娱。
架构上参考过 `mine-monopoly` 的思路（Host 权威、阶段系统、数据驱动），
但**没有复制其任何源码或美术资源**。
游戏内所有图形（含 12 个角色头像）均由 Pygame 程序化绘制，音效由程序合成，
不包含任何第三方素材。
