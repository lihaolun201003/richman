"""双实例真实联机验证：启动**两个独立进程**跑完整 LAN 流程。

与 run_host_client_demo.py 的区别（很重要）：

| | run_host_client_demo | 本工具 |
| --- | --- | --- |
| 进程 | 一个进程里开两条 TCP | **两个独立进程**，各有自己的 App / 窗口 / 事件循环 |
| 驱动 | 直接调 Host/Client API | 模拟玩家点按钮、填输入框、按空格 |
| 覆盖 | 协议与状态一致性 | 建房 → 加入 → 准备 → 开局 → 对局 → 掉线 → 重连 → 结算 |

用法:
    python tools/two_instance_demo.py --rounds 6 --port 28180
    python tools/two_instance_demo.py --host-ip 192.168.1.100     # 走真实局域网地址
    python tools/two_instance_demo.py --no-disconnect             # 不测重连

最后一个参数 --host-ip 用本机的局域网 IPv4 而不是 127.0.0.1：
这条路径更接近室友真连的场景（网卡、路由表、防火墙规则都会参与）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DEFAULT = os.path.join(ROOT, "docs", "reports", "lan_demo")


def local_lan_ip() -> str:
    sys.path.insert(0, ROOT)
    from src.network import netinfo

    return netinfo.best_ip()


def wait_for_port(host: str, port: int, timeout: float = 25.0) -> bool:
    """等房主进程把端口监听起来（不是等它「窗口出现」）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.6):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def write_config(path: str, data: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_events(path: str) -> list[dict[str, Any]]:
    if not os.path.isfile(path):
        return []
    out: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def kinds(events: list[dict[str, Any]]) -> list[str]:
    return [e.get("kind", "") for e in events]


def main() -> int:
    ap = argparse.ArgumentParser(description="双实例真实联机验证")
    ap.add_argument("--rounds", type=int, default=5, help="每个进程参与多少轮")
    ap.add_argument("--port", type=int, default=28180)
    ap.add_argument("--host-ip", default="", help="用这个地址连（默认 127.0.0.1）")
    ap.add_argument("--seed", type=int, default=0, help="保留参数：随机种子不影响流程")
    ap.add_argument("--no-disconnect", action="store_true", help="跳过掉线/重连演练")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--visible", action="store_true",
                    help="显示真实窗口（默认用 dummy 驱动，避免抢占桌面焦点）")
    ap.add_argument("--timeout", type=float, default=420.0, help="总超时（秒）")
    args = ap.parse_args()

    out_dir = os.path.abspath(args.out)
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)

    connect_ip = args.host_ip or "127.0.0.1"
    env = dict(os.environ)
    env["SDL_AUDIODRIVER"] = "dummy"
    if not args.visible:
        env["SDL_VIDEODRIVER"] = "dummy"
    env["PYTHONIOENCODING"] = "utf-8"

    host_cfg = {
        "name": "房主进程", "room": "双进程测试房间", "port": args.port,
        "anim_speed": 2.0, "ai_speed": 3.0, "rounds": args.rounds,
        "max_seconds": args.timeout,
    }
    client_cfg = {
        "name": "客户端进程", "host": connect_ip, "port": args.port,
        "anim_speed": 2.0, "ai_speed": 3.0, "rounds": args.rounds,
        "max_seconds": args.timeout,
        "disconnect_at_turn": 0 if args.no_disconnect else 8,
        # 保持断开 34 秒：房主宽限期是 30 秒，因此这条路径会真的走到
        # 「AI 临时接管 → 玩家重连 → 交还控制权」，而不是直接恢复。
        "disconnect_hold": 34.0,
    }
    host_cfg_path = os.path.join(out_dir, "host_config.json")
    client_cfg_path = os.path.join(out_dir, "client_config.json")
    write_config(host_cfg_path, host_cfg)
    write_config(client_cfg_path, client_cfg)

    runner = os.path.join(ROOT, "tools", "autoplay_main.py")
    python = sys.executable

    print("=" * 78)
    print(f"双实例联机验证：端口 {args.port}　连接地址 {connect_ip}:{args.port}")
    print(f"驱动脚本 {os.path.relpath(runner, ROOT)}（两个独立进程，真实 TCP）")
    print("=" * 78)

    host_log = open(os.path.join(out_dir, "host_stdout.log"), "w", encoding="utf-8")
    client_log = open(os.path.join(out_dir, "client_stdout.log"), "w", encoding="utf-8")
    host_proc = subprocess.Popen(
        [python, runner, "--role", "host", "--config", host_cfg_path, "--out", out_dir],
        cwd=ROOT, env=env, stdout=host_log, stderr=subprocess.STDOUT)
    print(f"  房主进程已启动 pid={host_proc.pid}")

    if not wait_for_port(connect_ip, args.port, timeout=30.0):
        print("  [失败] 房主进程没有在 30 秒内监听端口")
        host_proc.terminate()
        return 2
    print(f"  房主端口 {args.port} 已就绪")

    client_proc = subprocess.Popen(
        [python, runner, "--role", "client", "--config", client_cfg_path, "--out", out_dir],
        cwd=ROOT, env=env, stdout=client_log, stderr=subprocess.STDOUT)
    print(f"  客户端进程已启动 pid={client_proc.pid}")

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        if host_proc.poll() is not None and client_proc.poll() is not None:
            break
        time.sleep(0.5)
    for proc in (host_proc, client_proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
    host_log.close()
    client_log.close()
    time.sleep(0.4)

    host_events = read_events(os.path.join(out_dir, "host.jsonl"))
    client_events = read_events(os.path.join(out_dir, "client.jsonl"))
    hk, ck = kinds(host_events), kinds(client_events)

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    check("房主创建房间", "create_room" in hk)
    check("房主端口监听", True, f"{args.port}")
    check("客户端加入成功", "joined" in ck,
          next((e.get("room", "") for e in client_events if e.get("kind") == "joined"), ""))
    check("房主看到客户端进入", "client_arrived" in hk)
    check("客户端已准备", "ready" in ck)
    check("双方进入对局",
          "engine_started" in hk and any(
              e.get("kind") == "stage" and e.get("stage") == "playing"
              for e in client_events),
          next((f"{e.get('players')} 人" for e in host_events
                if e.get("kind") == "engine_started"), ""))
    check("客户端收到快照并推进",
          any(e.get("kind") == "rounds_done" for e in client_events) or "game_over" in ck)

    if not args.no_disconnect:
        presence = [e.get("state") for e in host_events if e.get("kind") == "presence"]
        check("客户端模拟拔网线", "simulated_cable_pull" in ck)
        check("房主看到玩家掉线", "disconnected" in presence, " → ".join(presence))
        check("房主侧 AI 接管", "ai_takeover" in presence)
        check("客户端自动重连成功",
              "auto_reconnected" in ck or "reconnected" in ck)
        check("房主看到控制权恢复",
              presence.count("ai_takeover") >= 1
              and ("ready" in presence[presence.index("ai_takeover"):]
                   or "idle" in presence[presence.index("ai_takeover"):]),
              " → ".join(presence))

    # 状态一致性：两边各自记录同一时刻的 canonical hash
    client_hash = next((e.get("hash") for e in client_events
                        if e.get("kind") == "rounds_done"), None)
    host_hash = next((e.get("hash") for e in host_events
                      if e.get("kind") == "rounds_done"), None)
    if client_hash and host_hash:
        check("双方状态哈希一致", client_hash == host_hash,
              f"host={host_hash} client={client_hash}")

    print("-" * 78)
    passed = 0
    for name, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        line = f"  [{mark}] {name}"
        if detail:
            line += f"　— {detail}"
        print(line)
    print("-" * 78)
    print(f"结果：{passed}/{len(checks)} 项通过")
    print(f"  房主事件 {len(host_events)} 条 · 客户端事件 {len(client_events)} 条")
    print(f"  明细：{os.path.relpath(out_dir, ROOT)}/host.jsonl / client.jsonl")
    print(f"  标准输出：{os.path.relpath(out_dir, ROOT)}/*_stdout.log")
    if host_events:
        print("  房主关键事件：" + " → ".join(
            f"{e['kind']}" + (f"({e.get('stage')})" if e.get("stage") else "")
            for e in host_events if e.get("kind") in
            ("started", "create_room", "client_arrived", "start_game", "rounds_done",
             "game_over", "timeout"))[:200])
    if client_events:
        print("  客户端关键事件：" + " → ".join(
            f"{e['kind']}" + (f"({e.get('stage')})" if e.get("stage") else "")
            for e in client_events if e.get("kind") in
            ("started", "join_room", "joined", "ready", "simulated_cable_pull",
             "auto_reconnected", "reconnected", "rounds_done", "game_over",
             "timeout", "join_timeout", "join_failed"))[:200])
    print("=" * 78)
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
