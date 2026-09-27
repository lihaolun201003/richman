"""单进程内的自动试玩驱动：供 two_instance_demo.py 在**真实独立进程**里使用。

为什么要有这个文件：
「同一进程里开两条 TCP 连接」不能代表真实联机 —— 它跳过了真实的进程隔离、
真实的窗口事件循环、真实的握手超时。这个驱动让两个**真正独立的进程**
（各自有自己的 App / 窗口 / 事件循环 / socket）按脚本走完整流程，
而驱动脚本本身只负责启动它们并汇总结果。

它只做玩家会做的事：点按钮、填输入框、按空格掷骰；
不直接改游戏状态（除了测试用的「拔网线」）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame  # noqa: E402

from src.app import App  # noqa: E402
from src.persistence.settings import Settings  # noqa: E402

DT = 1.0 / 60.0

#: 每个阶段最多等多久（秒）
WAIT_LIMIT = 60.0


class Recorder:
    """把关键事件写进 JSONL，供汇总脚本读取。"""

    def __init__(self, path: str, role: str) -> None:
        self.path = path
        self.role = role
        self.events: list[dict[str, Any]] = []
        self.t0 = time.time()

    def log(self, kind: str, **extra: Any) -> None:
        rec = {"t": round(time.time() - self.t0, 3), "role": self.role, "kind": kind}
        rec.update(extra)
        self.events.append(rec)
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def flush(self) -> None:
        if not self.events:
            return
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"kind": "summary", "role": self.role,
                                    "count": len(self.events)}, ensure_ascii=False) + "\n")
        except OSError:
            pass


class AutoPlayer:
    """按状态推进的自动玩家。"""

    def __init__(self, app: App, role: str, cfg: dict[str, Any],
                 recorder: Recorder) -> None:
        self.app = app
        self.role = role
        self.cfg = cfg
        self.rec = recorder
        #: 用**墙上时钟**而不是累加 dt：重连尝试会同步阻塞主循环，
        #: 帧数时间会严重偏慢（实测 34 秒等成 184 秒），等待逻辑必须按真实秒算。
        self.t0 = time.time()
        self.stage = "boot"
        self.stage_t = 0.0
        self.actions = 0
        self.turns_played = 0
        self.disconnect_at_turn = int(cfg.get("disconnect_at_turn", 0) or 0)
        self.disconnect_hold = float(cfg.get("disconnect_hold", 6.0))
        self.rounds_target = int(cfg.get("rounds", 6) or 6)
        self.max_seconds = float(cfg.get("max_seconds", 300) or 300)
        self.did_disconnect = False
        self.did_reconnect = False
        self.game_over_seen = False
        self.reconnect_at = 0.0
        self._real_address: tuple[str, int] | None = None
        self._last_phase = None
        self._last_revision = -1

    @property
    def t(self) -> float:
        return time.time() - self.t0

    # ------------------------------------------------------------ 工具

    def set_stage(self, stage: str) -> None:
        if stage != self.stage:
            self.rec.log("stage", stage=stage)
            self.stage = stage
            self.stage_t = self.t

    def click(self, scene: Any, rect: pygame.Rect) -> None:
        """真的走一遍按下 + 抬起（与玩家点击一致）。"""
        for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
            scene.handle_event(pygame.event.Event(
                kind, {"pos": rect.center, "button": 1}))

    def press(self, scene: Any, key: int) -> None:
        scene.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": key,
                                                              "mod": 0,
                                                              "unicode": ""}))

    # ------------------------------------------------------------ 主循环

    def step(self) -> None:
        if self.t > self.max_seconds:
            self.rec.log("timeout", stage=self.stage)
            self.app.quit()
            return
        try:
            handler = getattr(self, f"_stage_{self.stage}", None)
            if handler is not None:
                handler()
        except Exception as exc:  # 驱动异常不能装成游戏异常
            self.rec.log("driver_error", stage=self.stage, error=repr(exc))

    def _waited(self) -> float:
        return self.t - self.stage_t

    # ---- 开局：进入联机

    def _stage_boot(self) -> None:
        if self._waited() < 0.5:
            return
        self.app.scenes.switch_to("lan_setup", mode="host" if self.role == "host"
                                  else "join")
        self.set_stage("lan_setup")

    def _stage_lan_setup(self) -> None:
        scene = self.app.scenes.current
        name = self.cfg.get("name", "玩家")
        scene.name_input.set_text(name)
        if self.role == "host":
            scene.room_input.set_text(self.cfg.get("room", "自动测试房间"))
            scene.port_host_input.set_text(str(self.cfg.get("port", 28080)))
            self.click(scene, scene.widgets[8].rect)      # 创建房间
            self.rec.log("create_room", port=self.cfg.get("port", 28080))
            self.set_stage("wait_lobby")
        else:
            scene.host_input.set_text(str(self.cfg.get("host", "127.0.0.1")))
            scene.port_input.set_text(str(self.cfg.get("port", 28080)))
            self.click(scene, scene.widgets[8].rect)      # 连接房主
            self.rec.log("join_room", host=self.cfg.get("host", "127.0.0.1"),
                         port=self.cfg.get("port", 28080))
            self.set_stage("connecting")

    def _stage_connecting(self) -> None:
        client = self.app.client
        if client is None:
            if self._waited() > 5:
                self.rec.log("join_failed")
                self.set_stage("done")
            return
        if client.connected and client.player_id:
            self.rec.log("joined", player_id=client.player_id,
                         room=client.room_name, via=client.host_address)
            self.set_stage("client_lobby")
            return
        if self._waited() > 12:
            self.rec.log("join_timeout", last_error=client.last_error)
            self.set_stage("done")

    def _stage_wait_lobby(self) -> None:
        """房主：等客户端进房间。"""
        host = self.app.host
        if host is None:
            return
        others = [s for s in host.lobby.sessions if not s.is_host and not s.is_ai]
        if others:
            self.rec.log("client_arrived", names=[s.name for s in others])
            self.set_stage("host_lobby")
            return
        if self._waited() > 30:
            self.rec.log("no_client")
            self.set_stage("done")

    def _stage_host_lobby(self) -> None:
        """房主：客户端准备后开一局（先加两个 AI 让局面更像真实宿舍局）。

        注意这里**不假设一次点击就成功**：按钮的可用状态来自上一帧的
        lobby 数据（大厅每帧根据 can_start 刷新 enabled），第一次点可能
        刚好还在禁用状态。所以这里每帧重试，直到引擎真的起来为止。
        """
        host = self.app.host
        if host is None:
            return
        if not getattr(self, "_ai_added", False):
            host.lobby.add_ai(character_id="char_nana", color_id="green")
            host.lobby.add_ai(character_id="char_tiedan", color_id="amber")
            host.broadcast_lobby()
            self._ai_added = True

        if host.engine is not None:
            self.rec.log("engine_started", players=len(host.lobby.sessions))
            self.set_stage("playing")
            return

        if self.t - self.stage_t < 0.4:
            return

        ok, reason = host.lobby.can_start()
        scene = self.app.scenes.current
        if ok and scene is not None and len(scene.widgets) > 1:
            if not getattr(self, "_start_logged", False):
                self.rec.log("start_click")
                self._start_logged = True
            self.click(scene, scene.widgets[1].rect)
            return

        if self._waited() > 25:
            self.rec.log("start_blocked", reason=reason)
            self.set_stage("done")

    def _stage_client_lobby(self) -> None:
        """客户端：准备（同样每帧重试，不假设一次按键就成功）。"""
        scene = self.app.scenes.current
        client = self.app.client
        if client is None:
            self.set_stage("done")
            return
        players = (client.lobby.get("players") or [])
        me = next((p for p in players if p["player_id"] == client.player_id), None)
        if me is None:
            return
        if me.get("ready"):
            if not getattr(self, "_ready_logged", False):
                self._ready_logged = True
                self.rec.log("ready")
            self.set_stage("playing")
            return
        if self._waited() > 0.5 and scene is not None:
            self.press(scene, pygame.K_r)

    # ---- 对局

    def _stage_playing(self) -> None:
        scene = self.app.scenes.current
        if scene is None or not hasattr(scene, "session") or scene.session is None:
            self._heartbeat(None)
            return
        st = scene.session.state
        if st is None:
            self._heartbeat(None)
            return
        self._heartbeat(st)

        if st.game_over:
            self.game_over_seen = True
            self.rec.log("game_over", rounds=st.round_number, turns=st.turn_number,
                         winner=st.winner_id)
            self.set_stage("after_game")
            return

        # 记录状态推进
        self._last_revision = st.revision
        self._last_phase = st.phase
        self._watch_presence()

        # 掉线演练：客户端在指定回合主动断开，等一会儿再用 token 重连
        if (self.role == "client" and self.disconnect_at_turn
                and not self.did_disconnect and st.turn_number >= self.disconnect_at_turn):
            self._do_disconnect()
            return
        if self.role == "client" and self.did_disconnect and not self.did_reconnect:
            if self.t - self.reconnect_at >= self.disconnect_hold:
                self._do_reconnect()
            return

        # 目标轮数达成 → 收工。
        # 注意用**全局轮数**判断，而不是「我自己操作了几次」——
        # 4 人局里客户端一局只轮到自己几次，用次数判断会永远等下去。
        if st.round_number > self.rounds_target + 1:
            # 有人掉线时先别收工：房主继续打会让进程提前退出，
            # 「掉线 → AI 接管 → 恢复控制」这条链路就永远测不完整。
            if self.role == "host" and self._any_disconnected():
                if getattr(self, "_hold_since", 0.0) == 0.0:
                    self._hold_since = self.t
                    self.rec.log("hold_for_offline_player")
                if self.t - self._hold_since < 60.0:
                    return
            if not getattr(self, "_wrapped", False):
                self._wrapped = True
                self.rec.log("rounds_done", round=st.round_number,
                             turn=st.turn_number, revision=st.revision,
                             hash=st.canonical_hash())
                self.set_stage("finish_wait")
            return

        # 正常操作：处理弹窗与决策
        if self._handle_modal(scene):
            return
        decision = getattr(scene.session, "decision", None)
        if decision is not None:
            self._play_decision(scene, decision)

    def _any_disconnected(self) -> bool:
        host = self.app.host
        if host is None:
            return False
        return any(s.disconnected and not s.is_ai for s in host.lobby.sessions)

    def _watch_presence(self) -> None:
        """房主侧：记录每个座位「掉线 / AI 接管 / 恢复」的状态变化。

        这正是房主玩家在屏幕上看到的东西（大厅卡片上的状态标签），
        记录它能证明「掉线 → AI 接管 → 恢复控制」这条链路真的发生了。
        """
        host = self.app.host
        if host is None:
            return
        seen: dict[str, str] = getattr(self, "_presence", {})
        for s in host.lobby.sessions:
            if s.is_host:
                continue
            state, label = s.presence_state()
            if seen.get(s.player_id) != state:
                seen[s.player_id] = state
                self.rec.log("presence", player=s.name, state=state,
                             disconnected_sec=int(s.disconnected_seconds))
        self._presence = seen

    def _heartbeat(self, st: Any) -> None:
        """每 3 秒记一次状态：卡住时能立刻看出卡在哪一步。"""
        if self.t - getattr(self, "_hb_at", -99) < 3.0:
            return
        self._hb_at = self.t
        if st is None:
            self.rec.log("heartbeat", note="no_state", stage=self.stage)
            return
        pd = getattr(st, "pending_decision", None)
        self.rec.log("heartbeat", round=st.round_number, turn=st.turn_number,
                     phase=st.phase.value, current=st.current_player_id,
                     decision=(f"{pd.kind}:{pd.player_id}" if pd else ""),
                     revision=st.revision, role=self.role)

    def _handle_modal(self, scene: Any) -> bool:
        modal = getattr(scene, "modal", None)
        if modal is None:
            return False
        name = type(modal).__name__
        if name == "AssetPanel":
            if getattr(modal, "busy", False):
                return True
            card, rect = modal.find_action("upgrade")
            if rect is not None:
                self.click(scene, rect)
                return True
            if modal.on_declare is not None:
                rect = modal._declare_button_rect()
                self.click(scene, rect)
                return True
            modal.close()
            scene.modal = None
            return True
        if name == "ShopDialog":
            buttons = [b for b in modal.buttons if b.enabled]
            if buttons and self.actions % 3 == 0:
                self.click(scene, buttons[0].rect)
            else:
                self.click(scene, modal.buttons[-1].rect)
            self.actions += 1
            return True
        buttons = [b for b in modal.buttons if b.enabled]
        if buttons:
            self.click(scene, buttons[0].rect)
        else:
            modal.close()
            scene.modal = None
        self.actions += 1
        return True

    def _play_decision(self, scene: Any, decision: Any) -> None:
        from src.ui.game_scene import ROLL_BUTTON_RECT

        opt = next((o for o in decision.options if o.enabled), None)
        if opt is None:
            return
        if decision.kind == "roll":
            self.click(scene, ROLL_BUTTON_RECT)
            self.turns_played += 1
        else:
            scene.session.submit("RESOLVE_DECISION", {"option_id": opt.id}, decision.id)
        self.actions += 1

    # ---- 断线与重连

    def _do_disconnect(self) -> None:
        """模拟拔网线：不仅断开当前连接，还要让**接下来 30 多秒都连不上**。

        只关掉 socket 是不够的 —— 客户端每 3 秒就会重连成功，
        房主那边 30 秒的宽限期根本到不了，「AI 接管」这条路径永远测不到。
        所以这里把重连目标临时指向 192.0.2.0/24（TEST-NET-1，保留给文档的
        不可达网段），等 disconnect_hold 秒后再换回真实地址。
        """
        client = self.app.client
        if client is None or client.conn is None:
            return
        self.rec.log("simulated_cable_pull", turn=self.turns_played)
        try:
            client.conn.sock.shutdown(2)     # 对端立刻看到连接断开
        except OSError:
            pass
        client.conn.close("test pull")
        client.connected = False
        client.disconnected_reason = "测试：模拟拔网线"
        self._real_address = self.app._client_address
        self.app._client_address = ("192.0.2.1", self._real_address[1] or 1)
        self.app.reconnector = None          # 让 App 用新地址重新开始尝试
        self.did_disconnect = True
        self.reconnect_at = self.t
        self.set_stage("disconnected")

    def _restore_address(self) -> None:
        if self._real_address:
            self.app._client_address = self._real_address
        self.app.reconnector = None          # 下帧用真实地址重新尝试
        client = self.app.client
        if client is not None:
            # App 只在「有断开原因且没有调度器」时才会重新开始重连，
            # 这里补上原因，否则插回网线后不会自动再试（真实产品里也一样：
            # 玩家点「立即重试」走的是另一条路径，自动恢复要靠这个状态）
            client.disconnected_reason = "网线已插回，正在重新连接"
        self.rec.log("cable_plugged_back")

    def _do_reconnect(self) -> None:
        client = self.app.client
        if client is None:
            return
        try:
            client.reconnect(client.host_address.rsplit(":", 1)[0],
                             int(client.host_address.rsplit(":", 1)[1]),
                             client.reconnect_token)
            self.rec.log("reconnect_attempt")
        except Exception as exc:
            self.rec.log("reconnect_error", error=repr(exc))
        self.did_reconnect = True
        self.set_stage("reconnecting")

    def _stage_disconnected(self) -> None:
        """等待客户端自己的自动重连调度器接管（模拟真实掉线体验）。

        这段等待是刻意安排的：房主宽限期 30 秒，所以「掉线 → AI 接管 →
        玩家回来 → 交还控制权」这条完整链路只有等够时间才会真的发生。
        """
        client = self.app.client
        if client is not None and client.connected:
            self.rec.log("auto_reconnected")
            self.did_reconnect = True
            self.set_stage("playing")
            return
        if self._waited() >= self.disconnect_hold:
            self._restore_address()
            self.did_reconnect = False
            self.set_stage("plug_back")
            return
        if self._waited() > self.disconnect_hold + 40:
            self.rec.log("reconnect_gave_up")
            self.set_stage("playing")

    def _stage_plug_back(self) -> None:
        """网线插回来了：等客户端下一次自动尝试成功。"""
        client = self.app.client
        if client is not None and client.connected:
            self.rec.log("reconnected", revision=client.revision,
                         player_id=client.player_id)
            self.set_stage("playing")
            return
        if self._waited() > 25:
            self.rec.log("reconnect_failed",
                         last_error=client.last_error if client else "")
            self.set_stage("finish_wait")

    def _stage_reconnecting(self) -> None:
        client = self.app.client
        if client is not None and client.connected:
            self.rec.log("reconnected", revision=client.revision,
                         player_id=client.player_id)
            self.set_stage("playing")
            return
        if self._waited() > 15:
            self.rec.log("reconnect_failed", last_error=client.last_error if client else "")
            self.set_stage("finish_wait")

    # ---- 收尾

    def _stage_finish_wait(self) -> None:
        if self._waited() > 2.0:
            self.set_stage("done")

    def _stage_after_game(self) -> None:
        if self._waited() > 1.5:
            self.set_stage("done")

    def _stage_done(self) -> None:
        self.rec.flush()
        self.app.quit()


def run(role: str, cfg: dict[str, Any], out_dir: str) -> int:
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    log_path = os.path.join(out_dir, f"{role}.jsonl")
    rec = Recorder(log_path, role)

    settings = Settings()
    settings.set("player", "nickname", cfg.get("name", role))
    settings.set_fullscreen(False)
    settings.set_resolution(1280, 720)
    settings.set("ui", "animation_speed", float(cfg.get("anim_speed", 2.0)))
    settings.set("ui", "ai_speed", float(cfg.get("ai_speed", 3.0)))
    settings.set("ui", "show_guide", False)
    settings.set("ui", "guide_done", True)
    settings.set("ui", "show_tooltips", False)

    app = App(settings)
    driver = AutoPlayer(app, role, cfg, rec)
    rec.log("started", pid=os.getpid(), python=sys.version.split()[0])

    original_update = app.update

    def update(dt: float) -> None:
        original_update(dt)
        driver.step()

    app.update = update  # type: ignore[assignment]
    try:
        app.run()
    finally:
        rec.flush()
    return 0


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="自动试玩进程（由 two_instance_demo 启动）")
    ap.add_argument("--role", required=True, choices=["host", "client"])
    ap.add_argument("--config", required=True, help="JSON 配置文件路径")
    ap.add_argument("--out", required=True, help="输出目录")
    args = ap.parse_args()
    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return run(args.role, cfg, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
