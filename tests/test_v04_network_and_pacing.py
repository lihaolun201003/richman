"""v0.4 新增能力的测试：只覆盖真的会出错、且错了玩家会卡住的地方。

挑选标准（不是为凑数）：
- `diagnose.parse_target`：玩家粘贴的地址千奇百怪，解析错了直接连不上；
- `netinfo.classify`：虚拟网卡判错 → 把 VirtualBox 地址发给室友（v0.3 的真实故障）；
- `lobby.start_blockers`：大厅按钮文案与开局条件必须一致，不然玩家不知道在等谁；
- 预设完整性：聚炔局缺字段会让「本局速览」显示 0（v0.3 的真实 bug）;
- `MatchAnalytics`：统计口径错了会得出错误的节奏结论。
"""
from __future__ import annotations

import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.game.analytics import MatchAnalytics, milestone_for_event  # noqa: E402
from src.game.events import EventType  # noqa: E402
from src.game.setup import create_engine, preset_by_key, preset_list  # noqa: E402
from src.network import diagnose, netinfo  # noqa: E402
from src.network.lobby import LobbyState  # noqa: E402


# ==================================================================== 地址解析

class TestParseTarget:
    """玩家会真的粘进输入框的各种写法。"""

    @pytest.mark.parametrize("text,expect", [
        ("192.168.1.10", ("192.168.1.10", 28080)),
        ("192.168.1.10:28090", ("192.168.1.10", 28090)),
        ("192.168.1.10：28090", ("192.168.1.10", 28090)),        # 中文冒号
        ('"192.168.1.10:28090"', ("192.168.1.10", 28090)),       # 带引号
        ("http://192.168.1.10:28090", ("192.168.1.10", 28090)),
        ("  192.168.1.10:28090  ", ("192.168.1.10", 28090)),
        ("192.168.1.10:28090/", ("192.168.1.10", 28090)),
        ("10.0.0.5", ("10.0.0.5", 28080)),
    ])
    def test_parses_common_pastes(self, text, expect):
        host, port, error = diagnose.parse_target(text)
        assert error == ""
        assert (host, port) == expect

    def test_empty_is_rejected_with_hint(self):
        host, _port, error = diagnose.parse_target("   ")
        assert host == ""
        assert "请输入" in error

    def test_out_of_range_port(self):
        _host, _port, error = diagnose.parse_target("192.168.1.10:99999")
        assert "端口" in error

    def test_ipv6_is_explained_not_silently_misparsed(self):
        _host, _port, error = diagnose.parse_target("fe80::1")
        assert "IPv6" in error

    def test_default_port_comes_from_caller(self):
        host, port, _error = diagnose.parse_target("192.168.1.10", default_port=28180)
        assert (host, port) == ("192.168.1.10", 28180)


# ==================================================================== 网卡归类

class TestAdapterClassify:
    """虚拟网卡判定必须准：判错就会把连不上的地址发给室友。"""

    @pytest.mark.parametrize("name,desc,if_type,expect", [
        ("本地连接* 1", "Microsoft Wi-Fi Direct Virtual Adapter", 6,
         netinfo.KIND_VIRTUAL),
        ("VirtualBox Host-Only Network", "VirtualBox Host-Only Ethernet Adapter", 6,
         netinfo.KIND_VIRTUAL),
        ("vEthernet (WSL)", "Hyper-V Virtual Ethernet Adapter", 6,
         netinfo.KIND_VIRTUAL),
        ("蓝牙网络连接", "Bluetooth Device (Personal Area Network)", 6,
         netinfo.KIND_VIRTUAL),
        ("WLAN", "Intel(R) Wi-Fi 6E AX211 160MHz", 6, netinfo.KIND_WIFI),
        ("Wi-Fi", "Realtek 802.11ac", 71, netinfo.KIND_WIFI),
        ("以太网", "Realtek Gaming GbE Family Controller", 6, netinfo.KIND_ETHERNET),
        ("Ethernet", "Intel(R) Ethernet Connection", 6, netinfo.KIND_ETHERNET),
    ])
    def test_classification(self, name, desc, if_type, expect):
        assert netinfo.classify(name, desc, if_type) == expect

    def test_virtual_wins_over_ethernet_iftype(self):
        """虚拟网卡的 IF_TYPE 也是「以太网」，名字必须优先。"""
        assert netinfo.classify("VirtualBox Host-Only Network", "", 6) == \
            netinfo.KIND_VIRTUAL

    def test_sort_puts_usable_lan_first(self):
        adapters = [
            netinfo.Adapter(ip="169.254.1.1", name="以太网", kind=netinfo.KIND_ETHERNET,
                            is_up=False),
            netinfo.Adapter(ip="192.168.1.20", name="WLAN", kind=netinfo.KIND_WIFI,
                            is_up=True),
            netinfo.Adapter(ip="10.0.0.9", name="VirtualBox", kind=netinfo.KIND_VIRTUAL,
                            is_up=True),
        ]
        ordered = sorted(adapters, key=lambda a: a.sort_key())
        assert ordered[0].ip == "192.168.1.20"      # 通的、私有的排最前
        assert ordered[-1].kind == netinfo.KIND_VIRTUAL

    def test_describe_address_flags_localhost_and_link_local(self):
        text, level = netinfo.describe_address("127.0.0.1")
        assert level == "danger" and "不要发给室友" in text
        text, level = netinfo.describe_address("169.254.10.5")
        assert level == "danger"
        text, level = netinfo.describe_address("192.168.1.5")
        assert level == "success"


# ==================================================================== 大厅状态

class TestLobbyBlockers:
    """开始按钮上的原因必须与真实开局条件一致。"""

    def _lobby(self):
        lobby = LobbyState("测试房", host_player_id="host", port=28080)
        from src.network.session import PlayerSession

        host = PlayerSession("host", "房主", is_host=True, slot=0)
        host.ready = True
        lobby.sessions.append(host)
        lobby.host_player_id = "host"
        return lobby, host

    def test_needs_min_players(self):
        lobby, _host = self._lobby()
        blockers = lobby.start_blockers()
        assert any("还需要" in b for b in blockers)

    def test_names_who_is_not_ready(self):
        lobby, _host = self._lobby()
        from src.network.session import PlayerSession

        guest = PlayerSession("g1", "小满", slot=1)
        guest.conn = object()          # 假装连上了
        lobby.sessions.append(guest)
        blockers = lobby.start_blockers()
        assert any("小满" in b and "准备" in b for b in blockers)
        assert lobby.can_start()[0] is False

    def test_host_does_not_need_ready(self):
        """房主不需要准备：只要其余人都准备好了就能开始。"""
        lobby, host = self._lobby()
        from src.network.session import PlayerSession

        guest = PlayerSession("g1", "小满", slot=1)
        guest.conn = object()
        guest.ready = True
        lobby.sessions.append(guest)
        assert lobby.can_start()[0] is True
        assert "房主" not in " ".join(lobby.start_blockers())

    def test_disconnected_player_blocks_start(self):
        lobby, _host = self._lobby()
        from src.network.session import PlayerSession

        guest = PlayerSession("g1", "小满", slot=1)
        guest.conn = object()
        guest.ready = True
        guest.mark_disconnected()
        lobby.sessions.append(guest)
        blockers = lobby.start_blockers()
        assert any("掉线" in b for b in blockers)

    def test_to_dict_exposes_blockers_for_ui(self):
        lobby, _host = self._lobby()
        data = lobby.to_dict()
        assert data["can_start"] is False
        assert data["start_blockers"]
        assert data["start_reason"] == data["start_blockers"][0]
        assert data["host_name"] == "房主"
        assert "app_version" in data


# ==================================================================== 预设完整性

class TestPresets:
    def test_all_fields_present(self):
        """v0.3 的「经过起点 +0 / 初始手牌 0 张」就是缺字段导致的。"""
        needed = {"key", "name", "desc", "starting_money", "pass_start_bonus",
                  "max_rounds", "starting_cards", "shop_base_price",
                  "bonus_pool_base", "phase_scale", "ai_think_sec"}
        for item in preset_list():
            missing = needed - set(item)
            assert not missing, f"{item.get('key')} 缺少字段 {missing}"
            assert item["pass_start_bonus"] > 0
            assert item["starting_cards"] > 0

    def test_party_preset_exists_and_is_shorter(self):
        party = preset_by_key("party")
        standard = preset_by_key("standard")
        assert party, "聚会局预设必须存在"
        # 聚会局必须是「轮数更少 + 演出更短」，而不是靠加倍租金
        assert party["max_rounds"] < standard["max_rounds"]
        assert party["phase_scale"] < 1.0
        assert party["ai_think_sec"] < standard["ai_think_sec"]
        # 但租金相关的核心数值不动
        engine = create_engine(
            [{"id": "p1", "name": "a", "character_id": "char_ajin", "is_ai": True},
             {"id": "p2", "name": "b", "character_id": "char_xiaoman", "is_ai": True}],
            preset="party")
        std = create_engine(
            [{"id": "p1", "name": "a", "character_id": "char_ajin", "is_ai": True},
             {"id": "p2", "name": "b", "character_id": "char_xiaoman", "is_ai": True}],
            preset="standard")
        assert engine.state.rules["max_property_level"] == \
            std.state.rules["max_property_level"]
        assert engine.state.rules["tax_fixed"] == std.state.rules["tax_fixed"]

    def test_engine_uses_preset_phase_scale_and_ai_think(self):
        engine = create_engine(
            [{"id": "p1", "name": "a", "character_id": "char_ajin", "is_ai": True},
             {"id": "p2", "name": "b", "character_id": "char_xiaoman", "is_ai": True}],
            preset="party")
        assert float(engine.state.rules["phase_scale"]) < 1.0
        assert float(engine.state.rules["ai_think_sec"]) < 0.45


# ==================================================================== 对局统计

class TestAnalytics:
    def test_turn_and_decision_accounting(self):
        a = MatchAnalytics()
        a.add_turn_time("p1", 3.0)
        a.add_turn_time("p1", 5.0)
        a.add_turn_time("p2", 4.0)
        assert a.turns_by_player["p1"] == 2
        assert a.player_turn_avg("p1") == pytest.approx(4.0)
        a.add_decision(True, 0.4)
        a.add_decision(False, 2.5)
        assert a.decisions_ai == 1 and a.decisions_human == 1
        assert a.think_time_human == pytest.approx(2.5)

    def test_bankruptcy_rounds(self):
        a = MatchAnalytics()
        a.record_bankruptcy("p2", "电脑1", 46, debt=8400, assets=6150, properties=4)
        a.record_bankruptcy("p3", "电脑2", 88, debt=12000, assets=900, properties=1)
        assert a.first_bankrupt_round == 46
        assert a.last_bankrupt_round == 88
        assert a.bankrupt_log[0]["properties"] == 4

    def test_milestone_only_recorded_once(self):
        a = MatchAnalytics()
        a.milestone_once("mono:p1:东城", 12, 3, "垄断东城")
        a.milestone_once("mono:p1:东城", 20, 5, "垄断东城（重复）")
        assert len([m for m in a.milestones if "垄断" in m["text"]]) == 1

    def test_wealth_milestone_skips_duplicates(self):
        a = MatchAnalytics()
        a.wealth_milestone(30000, 27, 5, "破 3 万")
        a.wealth_milestone(30000, 40, 2, "破 3 万（重复）")
        assert sum(1 for m in a.milestones if m["kind"] == "wealth") == 1

    def test_roundtrip_serialization(self):
        a = MatchAnalytics()
        a.add_turn_time("p1", 3.5)
        a.record_bankruptcy("p2", "电脑1", 46, 100, 200, 3)
        a.wealth_milestone(30000, 10, 1, "破 3 万", "p1")
        restored = MatchAnalytics.from_dict(a.to_dict())
        assert restored.turns_by_player == a.turns_by_player
        assert restored.first_bankrupt_round == 46
        assert len(restored.milestones) == len(a.milestones)

    def test_milestone_from_events_counts_economy(self):
        class _Ev:
            def __init__(self, etype, player_id="p1", data=None):
                self.type = etype
                self.player_id = player_id
                self.data = data or {}
                self.message = ""

        class _State:
            round_number = 5
            turn_number = 9

            def player(self, pid):
                return None

            def player_asset_value(self, pid):
                return 0

        a = MatchAnalytics()
        st = _State()
        for etype in (EventType.PROPERTY_BOUGHT, EventType.PROPERTY_UPGRADED,
                      EventType.RENT_PAID, EventType.PROPERTY_SOLD):
            milestone_for_event(a, st, _Ev(etype))
        assert (a.bought_total, a.upgrade_total, a.rent_total, a.sold_total) == \
            (1, 1, 1, 1)


# ==================================================================== 诊断包

class TestDiagnosticBundle:
    def test_export_contains_no_private_data(self, tmp_path):
        """诊断包必须能安全地发给别人。"""
        target = str(tmp_path / "diag.zip")
        diagnose.export_bundle(target, None)
        assert os.path.isfile(target)
        with zipfile.ZipFile(target) as zf:
            names = zf.namelist()
            assert "network-report.json" in names
            assert "network-log.txt" in names
            blob = zf.read("network-report.json").decode("utf-8")
        # 不含用户名 / 家目录
        home = os.path.expanduser("~")
        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        assert home not in blob
        if len(user) > 2:
            assert user not in blob
        # 不含存档内容
        assert "saves" not in blob.lower() or "save" not in blob.lower()
        assert "app_version" in blob

    def test_bundle_filename_format(self):
        name = diagnose.bundle_filename()
        assert name.startswith("Richman-network-diagnostic-")
        assert name.endswith(".zip")

    def test_connection_test_result_labels(self):
        for status in (diagnose.TEST_OK, diagnose.TEST_TIMEOUT, diagnose.TEST_VERSION,
                       diagnose.TEST_FULL, diagnose.TEST_STARTED,
                       diagnose.TEST_PROTOCOL, diagnose.TEST_UNKNOWN):
            assert diagnose.TEST_LABELS[status]
            assert diagnose.TEST_ADVICE[status]

    def test_test_result_never_returns_bare_failure(self):
        """每个失败都必须带一句可执行的动作，不允许只回「连接失败」。"""
        result = diagnose.test_connection("127.0.0.1", 1, timeout=0.5)
        assert result.status in (diagnose.TEST_REFUSED, diagnose.TEST_UNKNOWN,
                                 diagnose.TEST_PROTOCOL, diagnose.TEST_TIMEOUT)
        assert result.advice, "失败必须给出下一步动作"
        assert result.label != "连接失败"
