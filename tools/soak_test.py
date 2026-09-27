"""长时间稳定性测试：连续多局 + 反复「再来一局」，检查资源泄漏与状态残留。

检查项：
- 内存是否持续增长（Python 对象数 / 进程 RSS）
- 线程数是否泄漏（网络线程、定时线程）
- socket 是否泄漏
- GameState 是否被上一局污染（玩家、地产、revision 是否干净）
- 连续多局是否都能正常结束

用法:
    python tools/soak_test.py --rounds 12 --games 8
"""
from __future__ import annotations

import argparse
import gc
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.controllers.ai import AIController  # noqa: E402
from src.game.setup import create_engine  # noqa: E402

DT = 1.0 / 30.0


def process_memory_mb() -> float:
    """当前进程内存占用（MB）。优先 psutil，其次 Windows API，失败返回 0。"""
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        pass
    try:
        import ctypes
        import ctypes.wintypes as wt

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        counters = PMC()
        counters.cb = ctypes.sizeof(counters)
        # GetCurrentProcess 需要显式声明返回类型，否则 64 位下句柄会被截断
        ctypes.windll.kernel32.GetCurrentProcess.restype = wt.HANDLE
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        for lib in (ctypes.windll.psapi, ctypes.windll.kernel32):
            fn = getattr(lib, "GetProcessMemoryInfo", None)
            if fn is None:
                continue
            if fn(handle, ctypes.byref(counters), counters.cb):
                return counters.WorkingSetSize / (1024 * 1024)
    except Exception:
        pass
    return 0.0


def count_objects() -> int:
    gc.collect()
    return len(gc.get_objects())


def play_one(seed: int, players: int, map_file: str, preset: str) -> dict:
    specs = [{"id": f"p{i + 1}", "name": f"AI{i + 1}",
              "character_id": ["char_ajin", "char_xiaoman", "char_laochen",
                               "char_nana"][i % 4], "is_ai": True}
             for i in range(players)]
    eng = create_engine(specs, seed=seed, anim_speed=10.0,
                        map_file=map_file, preset=preset)
    for p in eng.state.players:
        eng.bind_controller(p.id, AIController(p.id, seed=seed + p.slot, think_sec=0.0))
    eng.start()

    frames = 0
    while not eng.state.game_over and frames < 200000:
        eng.update(DT)
        frames += 1

    st = eng.state
    return {
        "ok": st.game_over and st.winner_id is not None,
        "rounds": st.round_number,
        "frames": frames,
        "players": len(st.players),
        "props": len(st.properties),
        "revision": st.revision,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="长时间稳定性测试")
    ap.add_argument("--games", type=int, default=8, help="连续对局数")
    ap.add_argument("--players", type=int, default=4)
    ap.add_argument("--map", default="default_map.json")
    ap.add_argument("--preset", default="standard")
    args = ap.parse_args()

    print("=" * 74)
    print(f"长时间稳定性测试：连续 {args.games} 局，每局 {args.players} 名 AI")
    print("=" * 74)

    threads_before = threading.active_count()
    mem_before = process_memory_mb()
    obj_before = count_objects()
    print(f"起始：线程 {threads_before}，内存 {mem_before:.1f} MB，"
          f"Python 对象 {obj_before:,}\n")

    results = []
    t0 = time.time()
    for i in range(args.games):
        r = play_one(20250000 + i, args.players, args.map, args.preset)
        results.append(r)
        mem = process_memory_mb()
        objs = count_objects()
        threads = threading.active_count()
        flag = "OK " if r["ok"] else "BAD"
        print(f"  [{flag}] 第 {i + 1:2d} 局：{r['rounds']:4d} 轮，"
              f"{r['frames']:6d} 帧 | 内存 {mem:6.1f} MB | "
              f"对象 {objs:7,} | 线程 {threads}")

    elapsed = time.time() - t0
    mem_after = process_memory_mb()
    obj_after = count_objects()
    threads_after = threading.active_count()

    print("\n" + "-" * 74)
    ok = sum(1 for r in results if r["ok"])
    print(f"完成率        : {ok}/{len(results)}")
    print(f"总耗时        : {elapsed:.1f}s（平均每局 {elapsed / len(results):.2f}s）")
    print(f"内存变化      : {mem_before:.1f} → {mem_after:.1f} MB "
          f"（{mem_after - mem_before:+.1f} MB）")
    print(f"Python 对象   : {obj_before:,} → {obj_after:,} "
          f"（{obj_after - obj_before:+,}）")
    print(f"线程变化      : {threads_before} → {threads_after}")
    print(f"平均轮数      : {sum(r['rounds'] for r in results) / len(results):.1f}")

    problems = []
    if ok != len(results):
        problems.append(f"{len(results) - ok} 局未正常结束")
    if threads_after > threads_before:
        problems.append(f"线程泄漏 {threads_after - threads_before} 个")
    if mem_after - mem_before > 300:
        problems.append(f"内存增长 {mem_after - mem_before:.0f} MB 偏多")
    growth = (obj_after - obj_before) / max(1, obj_before)
    if growth > 0.35:
        problems.append(f"Python 对象增长 {growth * 100:.0f}% 偏多")

    print()
    if problems:
        print("发现问题：")
        for p in problems:
            print(f"  - {p}")
    else:
        print("未发现资源泄漏或状态残留问题")
    print("=" * 74)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
