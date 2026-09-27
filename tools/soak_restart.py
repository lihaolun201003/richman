"""App 级「再来一局」稳定性测试。

模拟玩家真实操作：进入单机对局 → 打完 → 点「再来一局」→ 循环。
检查每一局是否干净（玩家 / 地产重建、上一局数据不残留），
以及窗口 / 场景 / 音频对象是否泄漏。

用法:
    python tools/soak_restart.py --games 10
"""
from __future__ import annotations

import argparse
import gc
import os
import sys
import threading
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

DT = 1.0 / 60.0


def objects() -> int:
    gc.collect()
    return len(gc.get_objects())


def make_presser(app):
    def press(rect) -> None:
        for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
            app.scenes.handle_event(
                pygame.event.Event(kind, {"pos": rect.center, "button": 1}))
    return press


def drain(app, max_frames: int = 120000) -> bool:
    """推进到本局结束（自动处理弹窗与决策）。"""
    from src.game.commands import CommandType
    from src.ui.asset_panel import AssetPanel
    from src.ui.dialogs import GameOverDialog
    from src.ui.game_scene import ROLL_BUTTON_RECT

    press = make_presser(app)
    frames = 0
    while frames < max_frames:
        frames += 1
        scene = app.scenes.current
        modal = getattr(scene, "modal", None)
        handled = False

        if modal is not None:
            if isinstance(modal, GameOverDialog):
                return True
            if isinstance(modal, AssetPanel):
                if getattr(modal, "busy", False):
                    handled = False          # 等结算，不要重复点
                else:
                    for action in ("mortgage", "sell", "downgrade", "upgrade"):
                        card, rect = modal.find_action(action)
                        if card is not None and rect is not None:
                            press(rect)
                            break
                    else:
                        # 没有任何资产可操作 → 走破产出口
                        if modal.on_declare is not None:
                            press(modal._declare_button_rect())
                        else:
                            modal.close()
                            scene.modal = None
                    handled = True
            else:
                button = next((b for b in modal.buttons if b.enabled), None)
                if button is not None:
                    press(button.rect)
                else:
                    modal.close()
                    scene.modal = None
                handled = True

        if not handled:
            session = getattr(scene, "session", None)
            decision = session.decision if session else None
            if decision is not None:
                if decision.kind == "roll":
                    press(ROLL_BUTTON_RECT)
                else:
                    opt = next((o for o in decision.options if o.enabled), None)
                    if opt is not None:
                        session.submit(CommandType.RESOLVE_DECISION,
                                       {"option_id": opt.id}, decision.id)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
        # 关键：即使本次在操作弹窗，也必须推进一帧，
        # 否则「弹窗关掉又被重开」会让循环空转。
        app.update(DT)
        # 渲染不是被测对象，隔帧绘制即可（dummy 驱动下全场景绘制约 9ms/帧）
        if frames % 240 == 0:
            app._render()

        if frames % 6000 == 0:
            st = app.local_engine.state if app.local_engine else None
            if st is not None:
                pd = st.pending_decision
                print(f"      … 帧 {frames:6d} phase={st.phase.value:16s} "
                      f"turn={st.turn_number} round={st.round_number} "
                      f"modal={type(modal).__name__ if modal else '-'} "
                      f"decision={pd.kind if pd else '-'} debt={'是' if st.debt else '否'}")
    return False


def build_specs() -> list[dict]:
    return [
        {"id": "p1", "name": "本机玩家", "character_id": "char_ajin",
         "color_id": "red", "is_ai": False, "is_host": True},
        {"id": "p2", "name": "电脑甲", "character_id": "char_xiaoman",
         "color_id": "blue", "is_ai": True},
        {"id": "p3", "name": "电脑乙", "character_id": "char_laochen",
         "color_id": "green", "is_ai": True},
        {"id": "p4", "name": "电脑丙", "character_id": "char_nana",
         "color_id": "amber", "is_ai": True},
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="再来一局稳定性测试")
    ap.add_argument("--games", type=int, default=10)
    ap.add_argument("--preset", default="quick", help="用快速局缩短单局时间")
    args = ap.parse_args()

    from src.app import App
    from src.persistence.settings import Settings

    settings = Settings()
    settings.set_fullscreen(False)
    settings.set("ui", "animation_speed", 30.0)
    settings.set("game", "preset", args.preset)

    app = App(settings)
    app.running = True
    specs = build_specs()

    print("=" * 74)
    print(f"「再来一局」稳定性测试：连续 {args.games} 局（预设 {args.preset}）")
    print("=" * 74)

    threads0 = threading.active_count()
    objs0 = objects()
    t0 = time.time()
    results: list[dict] = []

    for i in range(args.games):
        app.start_local_game(specs)
        finished = drain(app)
        engine = app.local_engine
        st = engine.state if engine else None
        # 本测试关心的是「反复再来一局会不会泄漏 / 残留」，不是单局能否打完：
        #   - 新局面被完整重建（玩家、地产都在）
        #   - 状态自洽（位置合法、结束与赢家一致）
        #   - 本局确实在推进（回合数增长）
        progressed = bool(st is not None and st.turn_number >= 12)
        clean = bool(
            st is not None
            and len(st.players) == len(specs)
            and len(st.properties) > 0
            and all(0 <= p.position < st.board.tile_count for p in st.players)
            and st.round_number >= 1
            and (st.game_over == (st.winner_id is not None))
        )
        if not finished:
            clean = clean and progressed
        if not clean:
            print("      [诊断] "
                  f"players={len(st.players) if st else '-'}/"
                  f"{len(specs)} props={len(st.properties) if st else '-'} "
                  f"pos={[p.position for p in st.players] if st else '-'} "
                  f"tile={st.board.tile_count if st else '-'} "
                  f"round={st.round_number if st else '-'} "
                  f"turns={st.turn_number if st else '-'} "
                  f"over={st.game_over if st else '-'} "
                  f"winner={st.winner_id if st else '-'} "
                  f"finished={finished} progressed={progressed}")
        winner = st.player(st.winner_id).name if st and st.winner_id else "无"
        results.append({"finished": finished, "clean": clean,
                        "rounds": st.round_number if st else 0,
                        "turns": st.turn_number if st else 0, "winner": winner})
        # 通过标准是「局面干净 + 确实在推进」；
        # 是否打完整局由 ui_smoke_test / simulate_game 负责验证。
        flag = "OK " if clean else "BAD"
        print(f"  [{flag}] 第 {i + 1:2d} 局：{results[-1]['rounds']:4d} 轮 / "
              f"{results[-1]['turns']:4d} 回合，赢家 {winner} | "
              f"对象 {objects():,} | 线程 {threading.active_count()}")
        app.restart_current_game()
        for _ in range(6):
            app.update(DT)
            app._render()

    elapsed = time.time() - t0
    objs1 = objects()
    threads1 = threading.active_count()
    # 通过标准：局面干净且确实在推进（是否打完整局由其它测试负责）
    ok = sum(1 for r in results if r["clean"])
    finished_count = sum(1 for r in results if r["finished"])

    print("\n" + "-" * 74)
    print(f"局面干净    : {ok}/{len(results)}")
    print(f"其中打完整局: {finished_count}/{len(results)}")
    print(f"总耗时      : {elapsed:.1f}s")
    print(f"Python 对象 : {objs0:,} → {objs1:,} （{objs1 - objs0:+,}）")
    print(f"线程        : {threads0} → {threads1}")
    print(f"平均轮数    : {sum(r['rounds'] for r in results) / len(results):.1f}")

    problems = []
    if ok != len(results):
        problems.append(f"{len(results) - ok} 局异常")
    if threads1 > threads0:
        problems.append(f"线程泄漏 {threads1 - threads0} 个")
    growth = (objs1 - objs0) / max(1, objs0)
    if growth > 0.5:
        problems.append(f"对象增长 {growth * 100:.0f}%")

    print()
    if problems:
        print("发现问题：")
        for p in problems:
            print(f"  - {p}")
    else:
        print("「再来一局」反复进行未发现状态残留或资源泄漏")
    print("=" * 74)
    try:
        app._teardown_network()
        pygame.quit()
    except Exception:
        pass
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
