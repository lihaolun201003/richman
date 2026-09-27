"""真实窗口自检：验证「双击 exe」这条路径真的能跑，而不只是退出码是 0。

用途（打包产物尤其需要）：
    Richman.exe --launch-check
会真的创建一个窗口，依次：
    主菜单 → 设置（改音量/节奏并保存）→ 单人配置 → 开局 → 模拟若干回合
    → 保存 / 读取存档 → 截图 → 正常退出
并逐项打印 PASS/FAIL。任何一项失败都会返回非 0 退出码。

它同时验证两件打包后最容易坏的事：
1. **中文字体**能不能找到（找不到就是一堆方块）；
2. **可写目录**（设置 / 存档 / 日志）在 exe 旁边能不能真的写进去。

注意：这会短暂弹出一个真实窗口，因此不适合放进无人值守的 CI；
自动化回归用 `--selftest`（虚拟显示），产品级确认用这个。
"""
from __future__ import annotations

import os
import time
import traceback

from .utils.logging_setup import get_logger

log = get_logger(__name__)

DT = 1.0 / 60.0


class _Reporter:
    def __init__(self) -> None:
        self.items: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.items.append((name, bool(ok), detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" —— {detail}" if detail else ""),
              flush=True)
        return bool(ok)

    def failures(self) -> int:
        return sum(1 for _, ok, _ in self.items if not ok)


def _writable_probe(folder: str) -> tuple[bool, str]:
    from .utils.paths import ensure_dir

    try:
        ensure_dir(folder)
        path = os.path.join(folder, ".write_probe")
        with open(path, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(path)
        return True, folder
    except OSError as exc:
        return False, f"{folder}（{exc}）"


def run_launch_check(seconds: float = 12.0) -> int:
    """在真实窗口里跑一遍关键流程。返回 0 表示全部通过。"""
    import pygame

    from .app import App
    from .persistence import savegame
    from .persistence.settings import Settings
    from .ui.game_scene import ROLL_BUTTON_RECT
    from .ui.layout import translate_event
    from .utils.paths import logs_path, project_root, user_config_dir

    rep = _Reporter()
    print("=" * 70, flush=True)
    print("Richman 真实窗口自检", flush=True)
    print("=" * 70, flush=True)
    print(f"  项目根目录 : {project_root()}", flush=True)

    settings = Settings.load()
    settings.set_fullscreen(False)
    if settings.width < 1200:
        settings.set_resolution(1600, 900)

    try:
        app = App(settings)
    except Exception:
        print("[FAIL] 窗口创建失败：", flush=True)
        traceback.print_exc()
        return 1

    print(f"  视频驱动   : {pygame.display.get_driver()}", flush=True)
    print(f"  窗口尺寸   : {app.screen.get_size()}", flush=True)
    print(f"  中文字体   : {app.fonts.font_name}", flush=True)
    print(flush=True)
    rep.check("创建真实窗口", True, f"{app.screen.get_size()}")
    rep.check("找到中文字体", not app.fonts.warning, app.fonts.font_name)
    ok, where = _writable_probe(user_config_dir())
    rep.check("设置目录可写", ok, where)
    ok, where = _writable_probe(savegame.saves_path())
    rep.check("存档目录可写", ok, where)
    ok, where = _writable_probe(logs_path())
    rep.check("日志目录可写", ok, where)

    shots: list[str] = []
    deadline = time.time() + max(4.0, seconds)

    def pump(seconds_: float, shot_name: str = "") -> None:
        end = time.time() + seconds_
        while time.time() < end:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    app.running = False
                app.scenes.handle_event(translate_event(event, app.viewport))
            app.update(DT)
            app._render()
            time.sleep(DT)
        if shot_name:
            path = os.path.join(logs_path(), shot_name)
            try:
                pygame.image.save(app.screen, path)
                shots.append(path)
            except Exception as exc:
                print(f"    （截图失败：{exc}）", flush=True)

    try:
        print("\n[1] 主菜单", flush=True)
        app.scenes.switch_to("menu")
        pump(1.2, "launch_menu.png")
        rep.check("主菜单渲染", app.scenes.current is not None)

        print("[2] 设置（改动 → 保存 → 重新读取）", flush=True)
        app.scenes.switch_to("settings", back="menu")
        pump(0.6)
        app.settings.set("audio", "sfx_volume", 0.42)
        app.settings.set("ui", "animation_speed", 1.0)
        saved = app.settings.save()
        rep.check("设置可以写入磁盘", saved, app.settings.path)
        reloaded = Settings.load(app.settings.path)
        rep.check("设置能被重新读回", abs(reloaded.sfx_volume - 0.42) < 1e-6,
                  f"sfx_volume={reloaded.sfx_volume}")

        print("[3] 单人配置", flush=True)
        app.scenes.switch_to("local_setup")
        pump(0.5, "launch_setup.png")
        rep.check("配置界面渲染", True)

        print("[4] 开局并模拟操作", flush=True)
        specs = [
            {"id": "p1", "name": "自检玩家", "character_id": "char_ajin",
             "color_id": "red", "is_ai": False, "is_host": True},
            {"id": "p2", "name": "电脑甲", "character_id": "char_xiaoman",
             "color_id": "blue", "is_ai": True},
            {"id": "p3", "name": "电脑乙", "character_id": "char_laochen",
             "color_id": "green", "is_ai": True},
        ]
        app.settings.set("ui", "animation_speed", 4.0)
        app.start_local_game(specs)
        pump(1.5, "launch_game.png")
        engine = app.local_engine
        rep.check("进入对局", engine is not None and engine.state is not None)

        rolls = 0
        modals = 0
        frames = 0
        while time.time() < deadline and frames < 60 * 90:
            frames += 1
            scene = app.scenes.current
            modal = getattr(scene, "modal", None)
            if modal is not None:
                modals += 1
                button = next((b for b in modal.buttons if b.enabled), None)
                if button is not None:
                    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                        app.scenes.handle_event(
                            pygame.event.Event(kind, {"pos": button.rect.center,
                                                      "button": 1}))
                else:
                    modal.close()
                    scene.modal = None
            else:
                session = getattr(scene, "session", None)
                decision = session.decision if session else None
                if decision is not None:
                    if decision.kind == "roll":
                        rolls += 1
                        for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                            app.scenes.handle_event(
                                pygame.event.Event(kind, {"pos": ROLL_BUTTON_RECT.center,
                                                          "button": 1}))
                    else:
                        opt = next((o for o in decision.options if o.enabled), None)
                        if opt is not None:
                            session.submit("RESOLVE_DECISION", {"option_id": opt.id},
                                           decision.id)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    app.running = False
            app.update(DT)
            app._render()
            time.sleep(0.004)

        st = engine.state if engine else None
        if st is not None:
            print(f"    推进到第 {st.round_number} 轮 / {st.turn_number} 回合，"
                  f"真人掷骰 {rolls} 次，处理弹窗 {modals} 次", flush=True)
            pygame.image.save(app.screen, os.path.join(logs_path(), "launch_play.png"))
        rep.check("真实窗口下能连续推进对局", bool(st) and st.turn_number >= 3,
                  f"回合 {st.turn_number if st else 0}")
        rep.check("真人座位真的掷出过骰子", rolls > 0, f"{rolls} 次")
        rep.check("对局中没有负现金的存活玩家",
                  all(p.money >= 0 or p.bankrupt or st.debt is not None
                      for p in st.players) if st else False)

        print("[5] 存档 → 读取", flush=True)
        if engine is not None:
            try:
                path = savegame.save_to_slot(engine, "launchcheck")
                loaded = savegame.load_engine(path, anim_speed=4.0)
                same = loaded.state.canonical_hash() == engine.state.canonical_hash()
                rep.check("存档写入并可完整读回", same, os.path.basename(path))
            except Exception as exc:
                rep.check("存档写入并可完整读回", False, str(exc))

        print("[6] 正常退出", flush=True)
        app._teardown_network()
        pygame.quit()
        rep.check("正常退出", True, f"截图 {len(shots)} 张，位于 {logs_path()}")
    except Exception:
        rep.check("真实窗口流程无异常", False)
        traceback.print_exc()
        try:
            pygame.quit()
        except Exception:
            pass

    print("-" * 70, flush=True)
    total = len(rep.items)
    passed = total - rep.failures()
    print(f"真实窗口自检：{passed}/{total} 通过", flush=True)
    for name, ok, detail in rep.items:
        if not ok:
            print(f"  - {name}：{detail}", flush=True)
    return 1 if rep.failures() else 0
