# Richman · 城市之光

**本地多人局域网大富翁**。两个人、两台电脑、同一个 WiFi，一个开房间、另一个人输入地址，
就能一起玩一整局。不需要联网、不需要账号、不需要服务器。

```
2～6 人 · 真人 + AI 混合 · 单机 / 局域网 · 中文界面 · 免安装 EXE
```

---

## 快速开始

1. 把 `Richman-v0.4.0-rc1-windows.zip` **完整解压**到任意目录
   （不要只复制 exe，整个文件夹才有运行所需的文件）
2. 双击 `Richman.exe`
3. 一个人点「局域网联机 → 创建房间」
4. 其他人在自己的电脑上点「局域网联机 → 加入房间」，输入房主发来的地址
5. 房主点「开始游戏」

**第一次玩**：主菜单 →「新手教程」，3 分钟走一遍核心玩法；
或者第一次进对局时会自动出现 6 步引导（设置里可以关掉或重新打开）。

### 联机三步

```
房主：创建房间 → 大厅里会显示一行大字「192.168.x.x:28080」→ 点「复制连接信息」发给别人
其他人：加入房间 → 粘贴地址（或直接点右侧搜到的房间卡片）→ 按 R 准备
房主：看到「已准备」→ 点「开始游戏」→ 两边同时进入对局
```

---

## 搜不到房间怎么办？

**搜不到很正常，不影响联机。**

自动发现用的是 UDP 广播，部分校园网、访客 WiFi、企业网络会屏蔽广播包。
这时让房主点「复制连接信息」，把那段文字发给你，**粘到「房主发来的地址」框里**即可。

地址里带不带端口都行：`192.168.1.5` 和 `192.168.1.5:28080` 都认。

## 连不上怎么办？

打开**联机诊断**（主菜单 →「局域网联机」→「联机诊断」），它会告诉你：

- 本机应该把哪个地址发给别人（**会自动跳过虚拟网卡**）
- 端口、自动发现是否正常
- 上一次连接的结果

里面的「**测试连接**」可以直接验证某个地址通不通，并且会**分类告诉你失败原因**：

| 结果 | 通常代表 |
| --- | --- |
| 连接成功 | 可以加入了 |
| 对方拒绝连接 | 房主还没点「创建房间」，或端口填错了 |
| 连接超时 | 防火墙 / 路由器隔离 / 不在同一个网络 |
| 网络不可达 | 填了不同网段的地址 |
| 版本不一致 | 两台电脑的游戏文件不是同一份 |
| 房间已满 / 游戏已开始 | 等下一局，或让房主开新房间 |

搞不定时点「**导出联机诊断**」，会生成一个 `Richman-network-diagnostic-*.zip`，
里面只有网络状态和最近几条网络日志（不含存档、昵称、用户名），把它发给开发者即可。

## Windows 防火墙怎么办？

第一次开房间时 Windows 会弹窗，**必须勾选「专用网络」并点「允许访问」**。
误点了取消的话，用**管理员** PowerShell 执行：

```powershell
New-NetFirewallRule -DisplayName "Richman 大富翁" -Direction Inbound `
  -Protocol TCP -LocalPort 28080 -Action Allow -Profile Private
New-NetFirewallRule -DisplayName "Richman 自动发现" -Direction Inbound `
  -Protocol UDP -LocalPort 28081 -Action Allow -Profile Private
```

> 端口以大厅里显示的为准（被占用时游戏会自动往后找，最多试 20 个）。

## 掉线怎么办？

**掉线不会毁掉这一局。**

- 掉线的一边会看到半透明提示：「与房主连接中断 / 正在重新连接…」，
  带倒计时和「第 N 次尝试」，还有【立即重试】【返回主菜单】两个按钮；
- 房主那一边**游戏继续**，掉线玩家的卡片上会显示「掉线 12 秒」；
- 超过 30 秒由 **AI 临时接管**（卡片上写「AI 接管」），本局不会中断；
- 掉线的人**随时可以连回来**（客户端一共会尝试 150 秒）——
  连上后屏幕中间会显示「已重新连接到房间」，控制权自动交还。

房主退出 = 本局结束，其他人会收到提示并回到主菜单，不会卡死。

---

## 玩法

**目标**：让其他所有人破产，成为最后活着的人。若打到轮数上限仍未分胜负，按总资产排名判定。

**一个回合**：

```
回合开始 → 掷骰（两个六面骰）→ 逐格移动 → 落点结算 → 回合结束
```

**棋盘上怎么看信息**：

| 位置 | 含义 |
| --- | --- |
| 格子外缘的粗色带 | 这块地属于哪个**片区**（未购买也能看出） |
| 格子内的小图标 | 类型：房子=地产、塔=枢纽、四叶草=福运、警告三角=灾祸、袋子=商店、% =税收、树=公园、旗=起点 |
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

**规则预设**（时长是按真实引擎模拟出来的，见 `docs/reports/party_mode_report.md`）：

| 预设 | 起始资金 | 经过起点 | 轮数上限 | 实际时长（4～6 人真人局） |
| --- | --- | --- | --- | --- |
| 快速局 | 10,000 | +1,500 | 100 | 约 35～45 分钟 |
| 标准局 | 15,000 | +2,000 | 200 | 4 人约 60～70 分钟，人多更久 |
| 休闲局 | 22,000 | +2,500 | 260 | 边聊边玩，80 分钟以上 |
| **聚会局** | 12,000 | +1,200 | 80 | **5～6 人约 45～50 分钟** |

> 5～6 人开局时会提示「推荐使用聚会局」——只是建议，不强制。

**设置里可以调**：游戏节奏（只改演出时长）、**AI 演出速度**（正常/快速/极速，只影响等 AI 的观感，
不改变任何规则结果）、动画速度、字体大小、分辨率、音量、新手引导开关。

---

## 角色 / 地图 / 道具

**12 位角色**，每位一个被动（全部由数据文件声明，不是写死的代码分支）：

| 角色 | 称号 | 被动 | 角色 | 称号 | 被动 |
| --- | --- | --- | --- | --- | --- |
| 阿金 | 小老板 | 起始资金 +1,500 | 阿飞 | 工地工头 | 地产升级费用 −10% |
| 小满 | 快递骑手 | 经过起点额外 +600 | 胖虎 | 烧烤摊主 | 支付租金 −10% |
| 老陈 | 二手房东 | 购买地产价格 −8% | 老周 | 杂货铺老板 | 商店购买道具 −25% |
| 娜娜 | 网红主播 | 收取租金 +12% | 阿珍 | 典当行掌柜 | 出售地产回收 +15% |
| 铁蛋 | 装修队长 | 保释金 −50% | 强哥 | 银行信贷员 | 抵押可多拿 30% 现金 |
| 博楚 | 大学讲师 | 机遇事件收益 +20% | 小赵 | 税务师 | 缴纳税款 −30% |

**2 张地图**：

| 地图 | 格数 | 网格 | 地产 | 片区 | 推荐人数 |
| --- | --- | --- | --- | --- | --- |
| 城市之光 | 36 | 11×9 | 24 | 11 | 2～5 人 |
| 海滨假日 | 40 | 12×10 | 26 | 14 | 3～6 人 |

**21 种道具**，分四类：经济、移动、攻击、防御、地产。
游戏里按 `H` 可以打开完整的**规则与图鉴**（怎么玩 / 地块 / 道具 / 角色 / 地图 / 快捷键）。

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

**对局里还能看**：右下角「**本局记录**」点一下打开完整记录（按轮分组，可筛选「只看我 / 只看钱 / 只看地产」）；
结算界面右上角可以切到「**本局时间线**」，看谁在哪一轮完成了垄断、谁先破产。

---

## 存档与设置位置

都在**可写的数据目录**里，打包后与 exe 同级：

```
dist\Richman\config\user_settings.json     设置（音量 / 节奏 / AI 速度 / 字体 / 分辨率 / 昵称）
dist\Richman\saves\*.json                  存档（自动存档 + 手动存档）
dist\Richman\logs\richman.log              运行日志（出问题时看这里）
dist\Richman\diag\*.zip                    导出的联机诊断包
```

自动存档在每个回合结束后写入（节流：至少间隔 20 秒且至少 4 个回合）。
设置界面里会直接显示当前使用的设置文件完整路径。

---

## 从源码运行（开发者）

需要 **Python 3.10 或更高**（Windows）。

```powershell
cd 你解压/克隆到的目录\richman
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

或者双击 `run_game.bat`。

### 命令行参数

```powershell
.\.venv\Scripts\python.exe main.py --host --name 阿明 --port 28080
.\.venv\Scripts\python.exe main.py --join 192.168.1.5 --port 28080
.\.venv\Scripts\python.exe main.py --fullscreen --anim-speed 1.5 --seed 12345
.\.venv\Scripts\python.exe main.py --selftest        # 不打开窗口的全链路自检
.\.venv\Scripts\python.exe main.py --version
```

### Windows EXE

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe tools\build_windows.py --clean
```

产物 `dist\Richman\Richman.exe`（onedir 形式，**不能只复制 exe**）。

验证打包结果确实能启动：

```powershell
.\dist\Richman\Richman.exe --selftest --selftest-seconds 6
.\dist\Richman\Richman.exe --launch-check
```

---

## 项目结构

```
richman/
├── main.py                     程序入口（--host / --join / --selftest / --version）
├── config/default.json         规则预设（含聚会局）、网络端口、显示默认值
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
│   │   ├── analytics.py        对局分析（时间去向 / 破产轮次 / 里程碑）
│   │   ├── state.py            GameState：revision、随机种子、日志、流水
│   │   ├── ledger.py           EconomyLedger（所有资金变化的唯一出口）
│   │   └── ...                 board / property / chance / cards / jail / victory
│   ├── controllers/            统一决策入口：Local / AI / Remote
│   ├── network/                TCP 协议、Host、Client、大厅、UDP 发现、自动重连
│   │   ├── netinfo.py          网卡枚举（区分 Wi-Fi / 以太网 / 虚拟网卡）
│   │   └── diagnose.py         诊断检测、连接测试分类、诊断包导出
│   ├── ui/                     主题、矢量图标、控件、场景、棋盘、资产面板、
│   │                           演出层、新手引导、本局记录、诊断中心
│   ├── audio/                  程序化音效（不依赖外部音频文件）
│   ├── persistence/            设置与存档
│   └── utils/                  路径（冻结 / 开发分离）、日志、剪贴板、缓动
├── tools/                      开发与验证工具（见下表）
├── tests/                      pytest 规则测试
└── docs/reports/               验证报告与截图
```

### 架构要点

1. **Host 权威**：联机模式下只有房主运行 `GameEngine` 与唯一真实 `GameState`。
   客户端只显示状态、只发 `Command`，永远不自己决定骰子、位置、钱、租金、胜负。
2. **UI 不碰状态**：点击 → `Command` → 控制器 → 引擎 → 状态变化 → 渲染器读状态。
3. **统一控制器**：真人 / AI / 远程玩家是同一个 `BaseController` 接口。
4. **统一决策**：所有「等待操作」都是 `PendingDecision`，提交时必须带 `decision_id`。
5. **显式阶段机**：`TURN_START → WAIT_ROLL → ROLLING → MOVING → ARRIVED →
   RESOLVE_TILE →（SHOP / DEBT_RESOLUTION / WAIT_DECISION）→ TURN_END`。
6. **可复现随机**：所有随机数由 `seed + counter` 决定，存档能完整还原同一局。
7. **数值统一出口**：所有加成走 `modifiers.resolve()`，所有资金变化走
   `ledger.gain / pay_bank / transfer()`。
8. **演出不参与规则**：动画、事件卡、资金浮字、破产演出全部只读状态；
   演出有优先级（破产/掉线/重连 > 收租提示），且都能点击跳过。
9. **数据驱动**：地图、地产、事件、道具、角色全在 `data/*.json`，换地图不改引擎。

---

## 验证工具

```powershell
# 规则测试
.\.venv\Scripts\python.exe -m pytest tests\ -q

# AI 长局：检查死锁 / 非法状态
.\.venv\Scripts\python.exe tools\simulate_game.py --games 100 --players 4

# 节奏与聚会模式对比（生成 docs/reports/party_mode_report.md）
.\.venv\Scripts\python.exe tools\party_mode_simulation.py --games 3

# 双实例真实联机（两个独立进程：建房 → 加入 → 准备 → 对局 → 掉线 → AI 接管 → 重连）
.\.venv\Scripts\python.exe tools\two_instance_demo.py --rounds 3
.\.venv\Scripts\python.exe tools\two_instance_demo.py --host-ip 192.168.1.100   # 换成你自己的局域网 IP

# 性能预算实测（生成 docs/reports/perf_v04.md）
.\.venv\Scripts\python.exe tools\perf_check.py --frames 120

# 界面回归截图（生成 docs/reports/shots_v04/）
.\.venv\Scripts\python.exe tools\v04_shots.py

# 真实窗口启动自检 / 逐项自检 / 资源泄漏检查
.\.venv\Scripts\python.exe tools\launch_check.py --full-game
.\.venv\Scripts\python.exe main.py --launch-check
.\.venv\Scripts\python.exe tools\soak_test.py --games 10
.\.venv\Scripts\python.exe tools\soak_restart.py --games 6
```

### 改数值 / 加内容

- `config/default.json` → `gameplay` 段：基础数值；`presets` 段：四个预设
- `tools/gen_data.py`：租金与升级费用系数、地产价格与片区划分
  （改完执行 `python tools\gen_data.py` 重新生成地图数据）
- **加事件**：往 `data/chance_events.json` 加一条，标好 `polarity`
- **加道具**：往 `data/cards.json` 加一条，写清 `timing` 与 `shop_price`
- **加角色**：往 `data/characters.json` 加一条，`perk.modifiers` 里声明数值影响，不需要改代码
- **加地图**：改 `tools/gen_data.py` 的 `MAPS` 列表，重新生成即可

---

## 已知限制（v0.4.0-rc1）

1. **跨机联机仍未在两台真实电脑上验证过**（开发环境只有一台机器）。
   已经验证的是：**同一台电脑上两个独立进程**跑完整局 + 掉线 + AI 接管 + 重连 +
   状态哈希一致（`tools/two_instance_demo.py`，13/13 通过）。
   但**防火墙、AP 隔离、真机拔网线**这些只有两台电脑才碰得到 → 请按
   `docs/playtest/TWO_PC_LAN_TEST.md` 实测一遍（10～15 分钟）。
2. **UDP 自动发现同理**：只在本机验证过广播收发，跨机广播在部分网络会被屏蔽。
   手动输入地址永远是保底方案。
3. **没有人类长时间连续试玩过**：手感、弹窗频率这类主观体验仍需真人确认。
4. 没有回放 / 撤销 / 地图编辑器；存档没有跨版本迁移（有版本守卫，会明确报错而不是崩）。
5. 没有背景音乐资源（音效是程序合成的；BGM 接口已预留，
   放入 `assets/audio/bgm_main.ogg` 即会自动播放）。

---

## 许可

本项目为原创实现，仅用于学习与自娱。
游戏内所有图形（含 12 个角色头像）均由 Pygame 程序化绘制，音效由程序合成，
不包含任何第三方素材。
