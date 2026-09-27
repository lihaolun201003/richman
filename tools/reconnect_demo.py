"""断线 → 重连 的真实集成验证。

为什么单独写这个脚本：
v0.2 报告里唯一明确标注「没有验证到位」的能力就是重连 ——
当时只验证了「掉线不毁局」，没有验证「掉线之后还能接回来继续操作」。
这个脚本在同一个进程里建立**两个真实 TCP 连接**，然后用「直接关掉 socket」
来模拟拔网线，完整验证下面这条链路：

    Client 正常对局
      → 粗暴断开（不发 LEAVE，模拟网线被拔）
      → Host 检测掉线并标记玩家，本局继续
      → Client 用原 reconnect_token 重连
      → 收到 RECONNECT_OK + 最新快照，本地状态追平 Host
      → Host 把座位从「掉线 / AI 接管」恢复成 RemoteController
      → Client 能继续正常操作（掷骰 / 决策都真的生效）

还会验证两件容易出错的事：
    1) 用错误 token 重连必须被拒绝，并给出中文提示；
    2) 超过宽限期后 Host 用 AI 接管，之后玩家重连要能把控制权接回来。

用法:
    python tools/reconnect_demo.py
    python tools/reconnect_demo.py --port 28210 --verbose
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.controllers.base import RemoteController  # noqa: E402
from src.game.commands import Command, CommandType  # noqa: E402
from src.game.phases import GamePhase  # noqa: E402
from src.network import protocol as proto  # noqa: E402
from src.network.client import GameClient  # noqa: E402
from src.network.host import GameHost  # noqa: E402

DT = 1.0 / 60.0


class Reporter:
    def __init__(self) -> None:
        self.steps: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.steps.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" —— {detail}" if detail else ""))
        return ok

    def summary(self) -> int:
        total = len(self.steps)
        ok = sum(1 for _, o, _ in self.steps if o)
        print("-" * 78)
        print(f"重连验证结果：{ok}/{total} 通过")
        failed = [s for s in self.steps if not s[1]]
        for name, _, detail in failed:
            print(f"  - {name}：{detail}")
        return len(failed)


def pump(host: GameHost, client: GameClient, seconds: float = 0.25) -> None:
    end = time.time() + seconds
    while time.time() < end:
        host.update(DT)
        if client is not None:
            client.update(DT)
        time.sleep(DT / 2)


def wait_for(predicate, host: GameHost, client: GameClient | None,
             timeout: float = 12.0, label: str = "") -> bool:
    start = time.time()
    while time.time() - start < timeout:
        host.update(DT)
        if client is not None:
            client.update(DT)
        if predicate():
            return True
        time.sleep(DT / 2)
    if label:
        print(f"    （等待超时：{label}）")
    return False


def drive_host(host: GameHost) -> int:
    """帮房主这个「真人座位」做决策。

    房主在这台测试进程里也是真人控制器，没人点它就不会掷骰，
    整局会卡在房主回合 —— 那样根本走不到客户端回合。
    """
    if host.engine is None:
        return 0
    st = host.engine.state
    pd = st.pending_decision
    if pd is None or pd.player_id != host.host_player_id:
        return 0
    ctrl = host.engine.controller_of(host.host_player_id)
    if ctrl is None:
        return 0
    if pd.kind == "roll":
        ctrl.submit(Command(ctype=CommandType.ROLL_DICE, player_id=host.host_player_id,
                            payload={}, decision_id=pd.id))
    else:
        opt = next((o for o in pd.options if o.enabled), None)
        if opt is None:
            return 0
        ctrl.submit(Command(ctype=CommandType.RESOLVE_DECISION,
                            player_id=host.host_player_id,
                            payload={"option_id": opt.id}, decision_id=pd.id))
    return 1


def abrupt_disconnect(client: GameClient) -> None:
    """模拟拔网线：不发 LEAVE，直接把 socket 关掉。"""
    conn = client.conn
    if conn is not None:
        try:
            conn.sock.shutdown(2)          # SHUT_RDWR
        except OSError:
            pass
        conn.close("simulated cable pull")
    client.connected = False
    client.conn = None
    client.disconnected_reason = "模拟拔网线"


def main() -> int:
    ap = argparse.ArgumentParser(description="断线重连集成验证")
    ap.add_argument("--port", type=int, default=28220)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--grace", type=float, default=-1.0,
                    help="覆盖 Host 的宽限期（秒），-1 表示用默认值")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    rep = Reporter()
    print("=" * 78)
    print("断线重连集成验证（真实 TCP，同进程双端）")
    print("=" * 78)

    import src.network.host as host_mod

    if args.grace > 0:
        host_mod.RECONNECT_GRACE_SEC = args.grace

    host = GameHost(room_name="重连验证房", host_name="房主",
                    host_character="char_ajin", port=args.port)
    host.start()
    print(f"\n[1] 房主监听端口 {host.port}")
    rep.check("房主启动", host.running, f"端口 {host.port}")

    client = GameClient()
    try:
        client.connect(args.host, host.port, "测试客户端", "char_xiaoman")
    except Exception as exc:
        rep.check("客户端建立连接", False, str(exc))
        host.stop()
        return 1

    ok = wait_for(lambda: bool(client.player_id), host, client, timeout=8.0,
                  label="JOIN_ACCEPTED")
    token = client.reconnect_token
    rep.check("客户端加入房间并获得重连凭证", ok and bool(token),
              f"player_id={client.player_id} token={'有' if token else '无'}")

    host.lobby.add_ai(character_id="char_nana", color_id="green")
    client.send_ready(True)
    host.broadcast_lobby()
    pump(host, client, 0.4)

    ok, reason = host.try_start_game()
    rep.check("对局开始", ok, reason or f"seed={host.state.seed if host.state else '-'}")
    ok = wait_for(lambda: client.state is not None, host, client, timeout=8.0,
                  label="首帧快照")
    rep.check("客户端收到首帧快照", ok,
              f"rev={client.revision}" if client.state else "")

    # -------- 客户端正常操作几次，确认基线可用
    acts = 0
    for _ in range(3600):
        host.update(DT)
        client.update(DT)
        drive_host(host)
        if client.state is None:
            continue
        pd = client.my_decision()
        if pd is not None:
            if pd.kind == "roll":
                client.send_command(CommandType.ROLL_DICE, {}, pd.id)
            else:
                opt = next((o for o in pd.options if o.enabled), None)
                if opt is not None:
                    client.send_command(CommandType.RESOLVE_DECISION,
                                        {"option_id": opt.id}, pd.id)
            acts += 1
        if acts >= 3:
            break
        time.sleep(DT / 4)
    rep.check("断线前客户端能正常操作", acts >= 3, f"完成 {acts} 次操作")

    # -------- 粗暴断开
    print("\n[2] 模拟拔网线（直接关闭 socket，不发 LEAVE）")
    rev_before = client.revision
    abrupt_disconnect(client)
    ok = wait_for(lambda: host.lobby.session_by_token(token) is not None
                  and host.lobby.session_by_token(token).disconnected,
                  host, None, timeout=8.0, label="Host 检测掉线")
    rep.check("Host 检测到掉线并标记玩家", ok)
    hp = host.state.player(client.player_id) if host.state else None
    rep.check("掉线玩家的状态被标记", bool(hp and hp.disconnected),
              f"disconnected={hp.disconnected if hp else '-'}")

    # 掉线后 Host 仍然继续推进
    rev0 = host.state.revision
    end = time.time() + 3.0
    while time.time() < end and host.state.revision == rev0:
        host.update(DT)
        drive_host(host)
        time.sleep(DT / 2)
    rep.check("掉线后 Host 继续推进对局", host.state.revision > rev0,
              f"rev {rev0} → {host.state.revision}")

    # -------- 错误 token 必须被拒绝
    print("\n[3] 用错误的 token 重连（应当被拒绝）")
    bad = GameClient()
    try:
        bad.connect(args.host, host.port, "假冒客户端")
        wait_for(lambda: bool(bad.last_error), host, bad, timeout=8.0, label="RECONNECT_FAIL")
        bad.reconnect_token = "not-a-real-token"
        bad.reconnect(args.host, host.port, "not-a-real-token")
        ok = wait_for(lambda: "重连失败" in bad.last_error or "重连失败" in "".join(bad.messages),
                      host, bad, timeout=8.0, label="RECONNECT_FAIL")
        rep.check("错误 token 被拒绝且给出中文提示", ok,
                  bad.last_error or ("；".join(bad.messages[-2:])))
    finally:
        bad.close(notify=False)

    # -------- 用原 token 重连
    print("\n[4] 用原 token 重连")
    try:
        client.reconnect(args.host, host.port, token)
        ok = wait_for(lambda: client.connected, host, client, timeout=10.0,
                      label="RECONNECT_OK")
        rep.check("重连成功（收到 RECONNECT_OK）", ok,
                  f"player_id={client.player_id}")
    except Exception as exc:
        rep.check("重连成功（收到 RECONNECT_OK）", False, str(exc))
        host.stop()
        return rep.summary()

    hp = host.state.player(client.player_id) if host.state else None
    rep.check("Host 清除掉线标记", bool(hp and not hp.disconnected),
              f"disconnected={hp.disconnected if hp else '-'}")
    ctrl = host.engine.controller_of(client.player_id) if host.engine else None
    rep.check("Host 恢复该座位的远程控制器", isinstance(ctrl, RemoteController),
              type(ctrl).__name__ if ctrl else "无")

    # 快照追平 + 状态一致
    host.push_snapshot()
    ok = wait_for(lambda: client.revision >= host.state.revision, host, client,
                  timeout=8.0, label="快照追平")
    same = (client.state is not None
            and client.state.canonical_hash() == host.state.canonical_hash())
    rep.check("客户端追平并拿到最新快照", ok and same,
              f"client rev={client.revision} host rev={host.state.revision}")
    rep.check("重连后状态与 Host 完全一致", same,
              "hash 一致" if same else "hash 不一致")

    # -------- 重连后还能继续操作
    print("\n[5] 重连后继续操作")
    acted = 0
    rev_at_start = host.state.revision
    for _ in range(3600):
        host.update(DT)
        client.update(DT)
        drive_host(host)
        pd = client.my_decision()
        if pd is not None:
            if pd.kind == "roll":
                client.send_command(CommandType.ROLL_DICE, {}, pd.id)
            else:
                opt = next((o for o in pd.options if o.enabled), None)
                if opt is not None:
                    client.send_command(CommandType.RESOLVE_DECISION,
                                        {"option_id": opt.id}, pd.id)
            acted += 1
        if acted >= 1 and host.state.revision > rev_at_start + 2:
            break
        time.sleep(DT / 4)
    rep.check("重连后客户端能继续操作", acted >= 1,
              f"重连后完成 {acted} 次操作，rev {rev_at_start} → {host.state.revision}")

    # 注意：Host 的快照是限流广播的（≥80ms），客户端必然短暂落后一两帧。
    # 这里验证的是「最终一致」：先让客户端追平 Host 当前 revision，再比 hash。
    host.push_snapshot()
    caught = wait_for(lambda: client.revision >= host.state.revision, host, client,
                      timeout=8.0, label="操作后追平")
    same = (client.state is not None
            and client.state.canonical_hash() == host.state.canonical_hash())
    rep.check("继续操作后两端最终一致", caught and same,
              f"client rev={client.revision} host rev={host.state.revision}"
              + ("" if same else "；" + "; ".join(
                  host.state.diff_summary(client.state)[:3]) if client.state else ""))

    # -------- 超过宽限期后 AI 接管，再重连要能接回控制权
    if args.grace > 0:
        print("\n[6] 超过宽限期 → AI 接管 → 再重连接回控制权")
        abrupt_disconnect(client)
        deadline = time.time() + args.grace + 10.0
        while time.time() < deadline:
            host.update(DT)
            drive_host(host)
            session = host.lobby.session_by_token(token)
            if session is not None and session.bot_takeover:
                break
            time.sleep(DT / 2)
        session = host.lobby.session_by_token(token)
        ok = session is not None and session.bot_takeover
        hp = host.state.player(client.player_id) if host.state else None
        rep.check("超时后由 AI 接管", ok and bool(hp and hp.bot_controlled),
                  f"bot_controlled={hp.bot_controlled if hp else '-'}")
        try:
            client.reconnect(args.host, host.port, token)
            ok = wait_for(lambda: client.connected, host, client, timeout=10.0,
                          label="再次重连")
        except Exception as exc:
            ok = False
            print(f"    重连异常：{exc}")
        hp = host.state.player(client.player_id) if host.state else None
        ctrl = host.engine.controller_of(client.player_id) if host.engine else None
        rep.check("AI 接管后仍可重连接回操作",
                  ok and hp is not None and not hp.bot_controlled
                  and isinstance(ctrl, RemoteController),
                  f"bot_controlled={hp.bot_controlled if hp else '-'} "
                  f"controller={type(ctrl).__name__ if ctrl else '-'}")

    client.close(notify=False)
    host.stop()
    time.sleep(0.2)
    return rep.summary()


if __name__ == "__main__":
    raise SystemExit(main())
