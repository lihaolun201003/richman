"""性能预算实测：把每个界面的每帧耗时量出来，而不是凭感觉说「不卡」。

口径说明（很重要，别把数字看串了）：
- 默认用 SDL dummy 驱动 ⇒ 测的是**逻辑更新 + 渲染到离屏画面**的耗时，
  不含真正把画面刷到屏幕（那部分由显示器刷新率决定，程序管不了多少）。
- 加 `--visible` 会用真实窗口测一次，数字会更接近玩家感受。
- 关心的是**有没有哪一屏明显比别人慢**（差异 > 3 倍就是问题），
  以及有没有超过 16.7ms（60 FPS 的预算）。

用法:
    python tools/perf_check.py                 # 离屏测量（快）
    python tools/perf_check.py --visible       # 真实窗口测量
    python tools/perf_check.py --frames 240    # 每屏测多少帧
    python tools/perf_check.py --out docs/reports/perf_v04.md
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from typing import Any, Callable

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

DT = 1.0 / 60.0
#: 60 FPS 的每帧预算
FRAME_BUDGET_MS = 16.7


class PerfHarness:
    def __init__(self, visible: bool, frames: int) -> None:
        if not visible:
            os.environ["SDL_VIDEODRIVER"] = "dummy"
        from src.app import App
        from src.persistence.settings import Settings

        settings = Settings()
        settings.set_fullscreen(False)
        settings.set_resolution(1600, 900)
        settings.set("ui", "animation_speed", 1.0)
        settings.set("ui", "ai_speed", 1.0)
        settings.set("ui", "show_guide", False)
        settings.set("ui", "guide_done", True)
        self.frames = frames
        self.app = App(settings)
        self.app.running = True

    def pump(self, count: int = 1) -> None:
        for _ in range(count):
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.app.running = False
            self.app.update(DT)
            self.app.scenes.draw(self.app.virtual)
            if self.app.settings.fullscreen or os.environ.get("SDL_VIDEODRIVER") != "dummy":
                pass

    def measure(self, name: str, setup: Callable[[], None] | None = None,
                warmup: int = 20) -> dict[str, Any]:
        if setup is not None:
            setup()
        self.pump(warmup)
        samples: list[float] = []
        render_only: list[float] = []
        for _ in range(self.frames):
            t0 = time.perf_counter()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.app.running = False
            self.app.update(DT)
            t1 = time.perf_counter()
            self.app.scenes.draw(self.app.virtual)
            t2 = time.perf_counter()
            samples.append((t2 - t0) * 1000.0)
            render_only.append((t2 - t1) * 1000.0)
        samples.sort()
        p95 = samples[int(len(samples) * 0.95)] if samples else 0.0
        return {
            "scene": name,
            "avg_ms": statistics.mean(samples) if samples else 0.0,
            "p95_ms": p95,
            "max_ms": max(samples) if samples else 0.0,
            "render_avg_ms": statistics.mean(render_only) if render_only else 0.0,
            "frames": len(samples),
            "over_budget": sum(1 for s in samples if s > FRAME_BUDGET_MS),
        }


def start_game(h: PerfHarness, players: int) -> Any:
    specs = [
        {"id": "p1", "name": "我", "character_id": "char_ajin",
         "color_id": "red", "is_ai": False, "is_host": True},
    ]
    chars = ["char_xiaoman", "char_laochen", "char_nana", "char_tiedan",
             "char_zhou", "char_afei"]
    colors = ["blue", "green", "amber", "purple", "orange", "teal"]
    for i in range(1, players):
        specs.append({
            "id": f"p{i + 1}", "name": f"电脑{i}",
            "character_id": chars[(i - 1) % len(chars)],
            "color_id": colors[(i - 1) % len(colors)],
            "is_ai": True,
        })
    h.app.settings.set("ui", "animation_speed", 8.0)   # 让对局快速推进
    h.app._last_local_options = {"map_file": "default_map.json", "preset": "party"}
    h.app.start_local_game(specs)
    h.pump(30)
    return h.app.local_engine


def run_measurements(h: PerfHarness) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    h.app.settings.set("ui", "animation_speed", 1.0)
    results.append(h.measure("主菜单（含动画装饰）",
                             lambda: h.app.scenes.switch_to("menu")))
    results.append(h.measure("单人配置",
                             lambda: h.app.scenes.switch_to("local_setup")))
    results.append(h.measure("设置",
                             lambda: h.app.scenes.switch_to("settings", back="menu")))
    results.append(h.measure("建房页",
                             lambda: h.app.scenes.switch_to("lan_setup", mode="host")))
    results.append(h.measure("联机诊断",
                             lambda: h.app.scenes.switch_to("net_diag", back="menu")))

    # 大厅（房主 + 2 AI + 2 个模拟远程座位）
    def setup_lobby() -> None:
        h.app.start_host("性能测试房间", "我", "char_ajin", 28390)
        host = h.app.host
        host.lobby.add_ai(character_id="char_nana", color_id="green")
        host.lobby.add_ai(character_id="char_tiedan", color_id="amber")
        host.broadcast_lobby()
        h.pump(6)

    results.append(h.measure("大厅（4 人）", setup_lobby))

    # 对局：4 人与 6 人
    def setup_game4() -> None:
        engine = start_game(h, 4)
        for _ in range(120):     # 推进几步，让棋盘上有建筑与棋子
            h.pump(1)
            scene = h.app.scenes.current
            if getattr(scene, "modal", None) is not None:
                btn = next((b for b in scene.modal.buttons if b.enabled), None)
                if btn is not None:
                    btn.on_click()
                else:
                    scene.modal.close()
                    scene.modal = None
        h.app.settings.set("ui", "animation_speed", 4.0)

    results.append(h.measure("对局 · 4 人", setup_game4))

    def setup_game6() -> None:
        engine = start_game(h, 6)
        for _ in range(120):
            h.pump(1)
            scene = h.app.scenes.current
            if getattr(scene, "modal", None) is not None:
                btn = next((b for b in scene.modal.buttons if b.enabled), None)
                if btn is not None:
                    btn.on_click()
                else:
                    scene.modal.close()
                    scene.modal = None

    results.append(h.measure("对局 · 6 人", setup_game6))

    # 资产面板
    def setup_asset() -> None:
        scene = h.app.scenes.current
        scene.open_asset_panel()
        h.pump(24)

    results.append(h.measure("资产面板", setup_asset))

    # 本局记录
    def setup_log() -> None:
        scene = h.app.scenes.current
        if getattr(scene, "modal", None) is not None:
            scene.modal.close()
            scene.modal = None
        scene.open_player_log()
        h.pump(24)

    results.append(h.measure("本局记录", setup_log))

    # 结算
    def setup_over() -> None:
        scene = h.app.scenes.current
        if getattr(scene, "modal", None) is not None:
            scene.modal.close()
            scene.modal = None
        from src.ui.dialogs import GameOverDialog
        from src.game import victory

        st = scene.session.state
        scene.modal = GameOverDialog(
            victory.ranking(st), st.round_number, victory.final_stats(st),
            timeline=list(getattr(st.analytics, "milestones", []) or []))
        h.pump(24)

    results.append(h.measure("结算（含时间线数据）", setup_over))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="性能预算实测")
    ap.add_argument("--frames", type=int, default=180, help="每屏测量帧数")
    ap.add_argument("--visible", action="store_true", help="用真实窗口测量")
    ap.add_argument("--out", default="", help="把报告写到 Markdown")
    args = ap.parse_args()

    h = PerfHarness(args.visible, args.frames)
    print("=" * 82)
    print(f"性能实测：每屏 {args.frames} 帧　驱动={'真实窗口' if args.visible else '离屏(dummy)'}"
          f"　分辨率 1600×900（逻辑）")
    print("=" * 82)
    results = run_measurements(h)
    h.app._teardown_network()
    pygame.quit()

    print(f"{'界面':<22}{'平均':>9}{'P95':>9}{'最慢':>9}{'纯渲染':>10}{'超预算帧':>10}")
    print("-" * 82)
    for r in results:
        print(f"{r['scene']:<22}{r['avg_ms']:>8.2f}ms{r['p95_ms']:>8.2f}ms"
              f"{r['max_ms']:>8.2f}ms{r['render_avg_ms']:>9.2f}ms"
              f"{r['over_budget']:>7}/{r['frames']}")
    print("-" * 82)
    slowest = max(results, key=lambda r: r["avg_ms"])
    print(f"最慢的一屏：{slowest['scene']}　平均 {slowest['avg_ms']:.2f}ms"
          f"（预算 {FRAME_BUDGET_MS}ms）")
    print(f"结论：{'全部在 60FPS 预算内' if all(r['avg_ms'] < FRAME_BUDGET_MS for r in results) else '存在超出预算的界面'}")

    if args.out:
        out = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write("# v0.4 性能实测\n\n")
            f.write(f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"- 每屏帧数：{args.frames}，分辨率 1600×900（逻辑）\n")
            f.write(f"- 驱动：{'真实窗口' if args.visible else '离屏（SDL dummy）'}"
                    "——离屏不含刷屏到显示器的时间\n")
            f.write(f"- 预算：{FRAME_BUDGET_MS} ms/帧（60 FPS）\n\n")
            f.write("| 界面 | 平均 | P95 | 最慢 | 其中纯渲染 | 超过预算的帧 |\n")
            f.write("| --- | --- | --- | --- | --- | --- |\n")
            for r in results:
                f.write(f"| {r['scene']} | {r['avg_ms']:.2f} ms | {r['p95_ms']:.2f} ms | "
                        f"{r['max_ms']:.2f} ms | {r['render_avg_ms']:.2f} ms | "
                        f"{r['over_budget']}/{r['frames']} |\n")
            f.write(f"\n最慢的一屏：{slowest['scene']}（平均 {slowest['avg_ms']:.2f} ms）\n")
        print(f"报告已写入 {out}")
    print("=" * 82)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
