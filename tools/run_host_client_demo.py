"""LAN Host + Client 真实双进程/双连接联机验证。

这不是「能不能连上」的冒烟测试，而是完整跑一遍真实多人流程：
    Host 建房 → Client 加入 → 双方准备 → Host 加 AI → 开局
    → Client 掷骰 → Host 判骰 → Client 移动 → Client 购地
    → 双方核对 money / position / phase / revision 完全一致
    → 连续推进几十个回合，比对 canonical hash

用法:
    python tools/run_host_client_demo.py                  # 本机 127.0.0.1 双开
    python tools/run_host_client_demo.py --rounds 60      # 指定回合数
    python tools/run_host_client_demo.py --port 28080
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.network import protocol as proto  # noqa: E402
from src.network.client import GameClient  # noqa: E402
from src.network.host import GameHost  # noqa: E402
from src.game.commands import Command, CommandType  # noqa: E402
from src.game.phases import GamePhase  # noqa: E402

DT = 1.0 / 60.0
CHECK_INTERVAL = 0.25


class Reporter:
    """记录每一步的验证结果。"""

    def __init__(self) -> None:
        self.steps: list[tuple[str, bool, str]] = []
        self.failures = 0

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.steps.append((name, ok, detail))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f" —— {detail}" if detail else ""))
        if not ok:
            self.failures += 1
        return ok

    def summary(self) -> int:
        total = len(self.steps)
        ok = sum(1 for _, o, _ in self.steps if o)
        print("-" * 78)
        print(f"验证结果：{ok}/{total} 通过")
        if self.failures:
            print("失败项：")
            for name, o, detail in self.steps:
                if not o:
                    print(f"  - {name}：{detail}")
        return self.failures


def wait_until(predicate, host: GameHost, client: GameClient, timeout: float = 15.0,
               label: str = "") -> bool:
    """在推进双端主循环的同时等待条件成立。"""
    start = time.time()
    while time.time() - start < timeout:
        host.update(DT)
        client.update(DT)
        if predicate():
            return True
        time.sleep(DT)
    return False


def compare_states(host: GameHost, client: GameClient) -> tuple[bool, str]:
    """比对 Host 与 Client 的核心状态。"""
    if host.engine is None or client.state is None:
        return False, "状态尚未就绪"
    hs = host.engine.state
    cs = client.state
    if hs.revision != cs.revision:
        return False, f"revision 不一致 host={hs.revision} client={cs.revision}"
    if hs.canonical_hash() != cs.canonical_hash():
        diffs = hs.diff_summary(cs)
        return False, "hash 不一致：" + "; ".join(diffs[:5])
    return True, f"rev={hs.revision} hash={hs.canonical_hash()}"


def sync_and_compare(host: GameHost, client: GameClient,
                     timeout: float = 4.0) -> tuple[bool, str, float]:
    """让客户端追平 Host 当前 revision，再做一致性比对。

    Host 的 revision 天然会短暂领先于广播（限流发送），
    所以验证的是「最终一致」而不是「逐帧一致」。
    """
    host.push_snapshot()          # 立即推送当前权威状态
    target = host.state.revision
    start = time.time()
    while client.revision < target and time.time() - start < timeout:
        client.update(DT)
        time.sleep(DT / 2)
    lag = time.time() - start
    if client.revision != target:
        return False, f"客户端未追上：host={target} client={client.revision}", lag
    ok, detail = compare_states(host, client)
    return ok, detail, lag


def main() -> int:
    ap = argparse.ArgumentParser(description="LAN Host/Client 双开联机验证")
    ap.add_argument("--port", type=int, default=28110)
    ap.add_argument("--rounds", type=int, default=40, help="要连续验证的回合数")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--ai", type=int, default=2, help="额外加入的 AI 数量")
    args = ap.parse_args()

    rep = Reporter()
    print("=" * 78)
    print("LAN 双开联机验证（Host + Client 两个真实连接）")
    print("=" * 78)

    # ---------------- 1. 建房
    print("\n[1] Host 创建房间")
    host = GameHost(
        room_name="验收测试房",
        host_name="房主阿明",
        host_character="char_ajin",
        port=args.port,
        max_players=6,
        min_players=2,
        anim_speed=6.0,          # 加速演出，让几十回合能在合理时间内跑完
    )
    try:
        host.start()
    except Exception as exc:
        print(f"  房间启动失败：{exc}")
        return 1
    rep.check("房间启动", host.running, f"端口 {host.port}")
    rep.check("房主座位就绪", host.lobby.player_count == 1,
              f"当前 {host.lobby.player_count} 人")

    # ---------------- 2. Client 加入
    print("\n[2] Client 加入房间")
    client = GameClient()
    try:
        client.connect(args.host, host.port, "客户端小林", "char_xiaoman")
    except Exception as exc:
        print(f"  连接失败：{exc}")
        host.stop()
        return 1

    ok = wait_until(lambda: client.connected and client.player_id != "", host, client,
                    timeout=8.0)
    rep.check("Client 收到 JOIN_ACCEPTED", ok,
              f"player_id={client.player_id or '-'}")
    if not ok:
        host.stop()
        return rep.summary()

    ok = wait_until(lambda: host.lobby.player_count == 2, host, client, timeout=5.0)
    rep.check("Host 大厅显示 2 人", ok, f"lobby={host.lobby.player_count}")
    rep.check("Client 收到大厅状态", bool(client.lobby.get("players")),
              f"{len(client.lobby.get('players', []))} 个座位")
    rep.check("Client 看到的房主", client.host_player_id == host.host_player_id,
              f"{client.host_player_id}")

    # ---------------- 3. Host 添加 AI
    print("\n[3] Host 添加 AI 并让所有人准备")
    for i in range(args.ai):
        host.lobby.add_ai(character_id="char_laochen" if i == 0 else "char_nana")
    host.broadcast_lobby()
    ok = wait_until(lambda: len(client.lobby.get("players", [])) == 2 + args.ai,
                    host, client, timeout=5.0)
    rep.check(f"Client 同步到 {2 + args.ai} 个座位", ok,
              f"client 看到 {len(client.lobby.get('players', []))} 个")

    if not host.lobby.session(client.player_id).ready:
        client.send_ready(True)
    ok = wait_until(
        lambda: all(p.get("ready") for p in client.lobby.get("players", []))
        and host.lobby.ready,
        host, client, timeout=5.0)
    rep.check("全部准备完成", ok, f"host.ready={host.lobby.ready}")

    # ---------------- 4. 开局
    print("\n[4] 开始游戏")
    ok, reason = host.try_start_game()
    rep.check("Host 开始游戏", ok, reason or f"match_id={host.state.match_id}")
    if not ok:
        host.stop()
        client.close()
        return rep.summary()

    ok = wait_until(lambda: client.state is not None, host, client, timeout=8.0)
    rep.check("Client 收到首帧状态快照", ok,
              f"rev={client.revision}" if ok else "超时未收到")
    if not ok:
        host.stop()
        client.close()
        return rep.summary()

    # ---------------- 5. 逐回合验证
    print(f"\n[5] 连续推进并核对状态（目标 {args.rounds} 回合）")
    my_turn_seen = 0
    my_roll_seen = 0
    my_buy_seen = 0
    hash_checks = 0
    hash_ok = 0
    lags: list[float] = []
    last_check = 0.0
    last_diag = 0.0
    start_round = host.state.round_number
    deadline = time.time() + 240.0

    while host.state.turn_number < args.rounds and not host.state.game_over:
        if time.time() > deadline:
            rep.check("回合推进（时间上限内）", False, "推进超时")
            break
        host.update(DT)
        client.update(DT)
        time.sleep(DT)      # 按真实帧率运行，贴近实际游戏

        # 房主是真人座位，这里模拟房主的 UI 操作（点击骰子 / 购买）
        host_decision = host.state.pending_decision
        if host_decision is not None and host_decision.player_id == host.host_player_id:
            if host_decision.kind == "roll":
                opt = host_decision.option("roll")
                if opt is not None:
                    submit_host(host, opt.command_type, dict(opt.payload), host_decision.id)
            elif host_decision.kind == "buy_property":
                price = int(host_decision.context.get("price", 0))
                me = host.state.player(host.host_player_id)
                if choice_buy(me, price):
                    submit_host(host, "BUY_PROPERTY",
                                {"property_id": host_decision.context.get("property_id")},
                                host_decision.id)
                else:
                    submit_host(host, "SKIP_PROPERTY", {}, host_decision.id)
            elif host_decision.kind == "upgrade_property":
                submit_host(host, "SKIP_PROPERTY", {}, host_decision.id)
            elif host_decision.kind == "jail":
                submit_host(host, "ROLL_FOR_JAIL", {}, host_decision.id)
            else:
                first = next((o for o in host_decision.options if o.enabled), None)
                if first is not None:
                    submit_host(host, first.command_type, dict(first.payload), host_decision.id)

        # 客户端在自己的决策时真实操作
        decision = client.my_decision()
        if decision is not None:
            if decision.kind == "roll":
                my_turn_seen += 1
                opt = next((o for o in decision.options if o.id == "roll"), None)
                if opt is not None:
                    client.resolve_decision(decision.id, opt.id)
                    my_roll_seen += 1
            elif decision.kind == "buy_property":
                prop_id = decision.context.get("property_id")
                prop = client.state.properties.get(str(prop_id)) if client.state else None
                price = decision.context.get("price", 0)
                me = client.my_player()
                if choice_buy(me, price):
                    client.resolve_decision(decision.id, "buy")
                    my_buy_seen += 1
                else:
                    client.resolve_decision(decision.id, "skip")
            elif decision.kind == "jail":
                client.resolve_decision(decision.id, "roll")
            else:
                first = next((o for o in decision.options if o.enabled), None)
                if first is not None:
                    client.resolve_decision(decision.id, first.id)

        now = time.time()
        if now - last_check >= CHECK_INTERVAL:
            last_check = now
            same, detail, lag = sync_and_compare(host, client, timeout=4.0)
            hash_checks += 1
            if same:
                hash_ok += 1
                lags.append(lag)
            else:
                rep.check("Host/Client 状态一致", False, detail)
                break
        if now - last_diag >= 10.0:
            last_diag = now
            conn = client.conn
            print(f"    · 回合 {host.state.turn_number:3d} | host_rev={host.state.revision:4d} "
                  f"client_rev={client.revision:4d} | phase {host.state.phase.value} | "
                  f"inbox={conn.inbox.qsize() if conn else -1} | "
                  f"一致性 {hash_ok}/{hash_checks}")

    rep.check(f"Client 真实完成自己的回合（{my_turn_seen} 次掷骰决策）",
              my_turn_seen > 0, f"掷骰 {my_roll_seen} 次")
    rep.check(f"Client 真实完成购地（{my_buy_seen} 次）", my_buy_seen > 0,
              "" if my_buy_seen else "本局未遇到可买地块")
    avg_lag = (sum(lags) / len(lags) * 1000) if lags else 0.0
    rep.check(f"状态一致性比对（{hash_checks} 次采样）",
              hash_checks > 0 and hash_ok == hash_checks,
              f"{hash_ok}/{hash_checks} 次一致，平均追平耗时 {avg_lag:.0f}ms")

    hs = host.state
    cs = client.state
    print(f"\n  推进结果：回合 {hs.turn_number}，轮次 {start_round}→{hs.round_number}，"
          f"phase={hs.phase.value}")
    print(f"  Host  : " + summarize(hs))
    print(f"  Client: " + summarize(cs))

    # ---------------- 6. Client 无规则权威
    print("\n[6] 验证 Client 不拥有规则权威")
    before_money = client.my_player().money if client.my_player() else 0
    if client.state is not None and client.my_player() is not None:
        # 客户端直接改本地视图，不应影响 Host，也不应让服务端状态变化
        client.my_player().money = before_money + 999999
        host.update(DT)
        client.update(DT)
        time.sleep(0.4)
        host.update(DT)
        client.update(DT)
        hostile_money = host.state.player(client.player_id).money
        rep.check("客户端篡改本地状态不影响 Host",
                  hostile_money == before_money,
                  f"host 侧仍为 {hostitive_str(hostile_money)}")
        # 客户端伪造一个不属于自己的决策响应
        if host.state.pending_decision is not None:
            pd = host.state.pending_decision
            if pd.player_id != client.player_id:
                client.send_command("RESOLVE_DECISION", {"option_id": pd.options[0].id},
                                    decision_id=pd.id)
                time.sleep(0.3)
                host.update(DT)
                rep.check("客户端无法替他人做决策",
                          host.state.pending_decision is not None
                          and host.state.pending_decision.id == pd.id,
                          "Host 拒绝了越权决策")

    # ---------------- 7. 断线处理
    print("\n[7] 验证断线不会毁局")
    rev_before = host.state.revision
    money_before = host.state.player(client.player_id).money
    client.close(notify=False)
    ok = wait_until(lambda: not host.lobby.session(client.player_id).connected,
                    host, client, timeout=8.0)
    rep.check("Host 检测到 Client 掉线", ok, "")
    # Host 单独继续推进，不应崩溃
    for _ in range(240):
        host.update(DT)
        time.sleep(DT / 2)
    rep.check("掉线后 Host 继续运行", host.running and host.state.revision > rev_before,
              f"rev {rev_before} → {host.state.revision}")
    rep.check("掉线玩家被标记",
              host.state.player(client.player_id).disconnected,
              f"disconnected=True, money={host.state.player(client.player_id).money}")

    # ---------------- 8. 关门
    print("\n[8] 关闭房间")
    host.stop()
    rep.check("房间正常关闭", not host.running, "")

    print("=" * 78)
    return rep.summary()


def choice_buy(me, price) -> bool:
    """客户端在购地决策上的简单策略：留够 3000 现金就买。"""
    if me is None:
        return False
    return me.money - price >= 3000


def submit_host(host: GameHost, ctype: str, payload: dict, decision_id: str) -> None:
    """模拟房主本机的 UI 操作：经由 LocalController 投递命令。"""
    cmd = Command(ctype=ctype, player_id=host.host_player_id, payload=payload,
                  decision_id=decision_id)
    host.host_controller.submit(cmd)


def hostitive_str(v: int) -> str:
    return f"{v:,}"


def summarize(state) -> str:
    if state is None:
        return "无状态"
    parts = []
    for p in state.players:
        tag = "AI" if p.is_ai else "人"
        parts.append(f"{p.name}({tag}) 资金{p.money:,} 位置{p.position} 地产{len(state.properties_of(p.id))}")
    return " | ".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
