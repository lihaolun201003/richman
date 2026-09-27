"""矢量图标库：所有图形都用 pygame 图元画出来，不依赖任何字体或图片资源。

为什么需要它：
- v0.2 用 `▶` `▮` `◆` 这类文字符号当图标，而中文字体不一定有这些码位，
  结果是主菜单第一眼就能看到两个「豆腐块」；
- 地图格子上只有文字，看不出格子类型，玩家无法凭图形预期会发生什么；
- 21 张道具卡在数据里已经有 icon 字段，但从来没人把它画出来。

约定：
- 每个图标函数签名为 `_icon_xxx(surface, rect, color, ink)`，
  rect 是图标应该占据的方框（内部已按最小边自适应），ink 是描边/深色；
- `draw_icon()` 是统一入口，未知名字退化成圆点，绝不抛异常；
- 只使用 pygame.draw 的图元，因此任何分辨率、任何字体环境下都稳定。
"""
from __future__ import annotations

import math
from typing import Callable

import pygame

#: 图标画布内部的留白比例（0.12 表示四周各留 12%）
PAD = 0.12


def _box(rect: pygame.Rect) -> tuple[pygame.Rect, int]:
    """把矩形收缩成正方形，返回 (方框, 最小边)。"""
    size = max(4, min(rect.width, rect.height))
    pad = int(size * PAD)
    inner = pygame.Rect(0, 0, size - pad * 2, size - pad * 2)
    inner.center = rect.center
    return inner, max(3, size - pad * 2)


def _ratio(value: float) -> int:
    return int(round(value))


# ==================================================================== 地形 / 格子

def _icon_start(surface, rect, color, ink):
    """起点：旗子。"""
    inner, s = _box(rect)
    pole_x = inner.x + s * 0.3
    pygame.draw.line(surface, ink, (pole_x, inner.y + s * 0.1),
                     (pole_x, inner.bottom), max(2, s // 12))
    flag = [(pole_x, inner.y + _ratio(s * 0.08)),
            (inner.right, inner.y + _ratio(s * 0.26)),
            (pole_x, inner.y + _ratio(s * 0.46))]
    pygame.draw.polygon(surface, color, flag)
    pygame.draw.polygon(surface, ink, flag, max(1, s // 20))
    pygame.draw.circle(surface, color, (pole_x, inner.bottom - _ratio(s * 0.04)),
                       max(2, _ratio(s * 0.08)))


def _icon_property(surface, rect, color, ink):
    """地产：房子。"""
    inner, s = _box(rect)
    roof = [(inner.left, inner.y + _ratio(s * 0.42)),
            (inner.centerx, inner.y + _ratio(s * 0.06)),
            (inner.right, inner.y + _ratio(s * 0.42))]
    body = pygame.Rect(inner.x + _ratio(s * 0.14), inner.y + _ratio(s * 0.40),
                       _ratio(s * 0.72), _ratio(s * 0.54))
    pygame.draw.rect(surface, color, body)
    pygame.draw.polygon(surface, color, roof)
    pygame.draw.rect(surface, ink, body, max(1, s // 18))
    pygame.draw.polygon(surface, ink, roof, max(1, s // 18))
    door = pygame.Rect(0, 0, _ratio(s * 0.2), _ratio(s * 0.26))
    door.midbottom = body.midbottom
    pygame.draw.rect(surface, ink, door)


def _icon_building(surface, rect, color, ink, floors: int = 1, high: bool = False):
    """建筑：按等级画 1～3 层，升级一眼可见。"""
    inner, s = _box(rect)
    floors = max(1, min(3, floors))
    unit = s / 3.2
    base_y = inner.bottom - _ratio(s * 0.06)
    for i in range(floors):
        w = unit * (1.25 if high else 1.05) - i * unit * 0.12
        h = unit * (0.95 if high else 0.86)
        x = inner.centerx - w / 2
        y = base_y - (i + 1) * h
        r = pygame.Rect(int(x), int(y), int(w), int(h))
        pygame.draw.rect(surface, color, r)
        pygame.draw.rect(surface, ink, r, max(1, s // 20))
        # 窗
        win = pygame.Rect(0, 0, max(2, int(r.width * 0.2)), max(2, int(r.height * 0.18)))
        win.center = (r.centerx, r.centery)
        pygame.draw.rect(surface, ink, win)


def _icon_station(surface, rect, color, ink):
    """交通枢纽：楼 + 塔尖。"""
    inner, s = _box(rect)
    body = pygame.Rect(0, 0, _ratio(s * 0.62), _ratio(s * 0.52))
    body.midbottom = (inner.centerx, inner.bottom - _ratio(s * 0.05))
    pygame.draw.rect(surface, color, body)
    pygame.draw.rect(surface, ink, body, max(1, s // 18))
    spire = [(body.centerx, inner.y + _ratio(s * 0.04)),
             (body.centerx + _ratio(s * 0.1), body.y),
             (body.centerx - _ratio(s * 0.1), body.y)]
    pygame.draw.polygon(surface, color, spire)
    pygame.draw.polygon(surface, ink, spire, max(1, s // 20))
    for i in range(2):
        pygame.draw.line(surface, ink,
                         (body.x + _ratio(s * 0.1), body.y + _ratio(s * 0.14) * (i + 1)),
                         (body.right - _ratio(s * 0.1), body.y + _ratio(s * 0.14) * (i + 1)),
                         max(1, s // 24))


def _icon_fortune(surface, rect, color, ink):
    """福运：四叶草 / 星光。"""
    inner, s = _box(rect)
    r = s * 0.2
    cx, cy = inner.centerx, inner.centery
    for k in range(4):
        ang = math.pi / 2 * k - math.pi / 4
        px = cx + math.cos(ang) * r * 0.85
        py = cy + math.sin(ang) * r * 0.85
        pygame.draw.circle(surface, color, (int(px), int(py)), int(r))
        pygame.draw.circle(surface, ink, (int(px), int(py)), int(r), max(1, s // 22))
    # 中间的光点
    pygame.draw.circle(surface, color, (cx, cy), max(2, int(r * 0.5)))


def _icon_disaster(surface, rect, color, ink):
    """灾祸：警示三角 + 感叹号。"""
    inner, s = _box(rect)
    tri = [(inner.centerx, inner.y + _ratio(s * 0.06)),
           (inner.right, inner.bottom - _ratio(s * 0.08)),
           (inner.left, inner.bottom - _ratio(s * 0.08))]
    pygame.draw.polygon(surface, color, tri)
    pygame.draw.polygon(surface, ink, tri, max(2, s // 14))
    bx = inner.centerx
    pygame.draw.line(surface, ink, (bx, inner.y + _ratio(s * 0.34)),
                     (bx, inner.y + _ratio(s * 0.62)), max(2, s // 12))
    pygame.draw.circle(surface, ink, (bx, inner.bottom - _ratio(s * 0.19)),
                       max(2, s // 16))


def _icon_shop(surface, rect, color, ink):
    """商店：购物袋 + 提手。"""
    inner, s = _box(rect)
    body = pygame.Rect(inner.x + _ratio(s * 0.1), inner.y + _ratio(s * 0.3),
                       _ratio(s * 0.8), _ratio(s * 0.64))
    pygame.draw.rect(surface, color, body)
    pygame.draw.rect(surface, ink, body, max(1, s // 16))
    pygame.draw.arc(surface, ink, pygame.Rect(inner.x + _ratio(s * 0.26),
                                              inner.y + _ratio(s * 0.08),
                                              _ratio(s * 0.48), _ratio(s * 0.44)),
                    math.pi, math.tau, max(2, s // 14))


def _icon_tax(surface, rect, color, ink):
    """税收：百分比符号（用图元画，不依赖字形）。"""
    inner, s = _box(rect)
    r = max(2, int(s * 0.14))
    pygame.draw.circle(surface, color, (inner.x + int(s * 0.22), inner.y + int(s * 0.24)),
                       r)
    pygame.draw.circle(surface, ink, (inner.x + int(s * 0.22), inner.y + int(s * 0.24)),
                       r, max(1, s // 22))
    pygame.draw.circle(surface, color, (inner.right - int(s * 0.22),
                                        inner.bottom - int(s * 0.24)), r)
    pygame.draw.circle(surface, ink, (inner.right - int(s * 0.22),
                                      inner.bottom - int(s * 0.24)), r, max(1, s // 22))
    pygame.draw.line(surface, color, (inner.right - int(s * 0.18), inner.y + int(s * 0.12)),
                     (inner.x + int(s * 0.18), inner.bottom - int(s * 0.12)),
                     max(2, s // 12))


def _icon_jail(surface, rect, color, ink):
    """看守所：栏杆。"""
    inner, s = _box(rect)
    frame = pygame.Rect(inner.x + _ratio(s * 0.08), inner.y + _ratio(s * 0.06),
                        _ratio(s * 0.84), _ratio(s * 0.86))
    pygame.draw.rect(surface, ink, frame, max(1, s // 16))
    for i in range(3):
        x = frame.x + int(frame.width * (0.25 + i * 0.25))
        pygame.draw.line(surface, color, (x, frame.y + 2), (x, frame.bottom - 2),
                         max(2, s // 12))


def _icon_police(surface, rect, color, ink):
    """巡查（押送看守所）：警灯。"""
    inner, s = _box(rect)
    body = pygame.Rect(inner.x + _ratio(s * 0.06), inner.y + _ratio(s * 0.44),
                       _ratio(s * 0.88), _ratio(s * 0.5))
    pygame.draw.rect(surface, color, body)
    pygame.draw.rect(surface, ink, body, max(1, s // 18))
    lamp = [(inner.centerx - _ratio(s * 0.26), body.y),
            (inner.centerx + _ratio(s * 0.26), body.y),
            (inner.centerx, inner.y + _ratio(s * 0.06))]
    pygame.draw.polygon(surface, color, lamp)
    pygame.draw.polygon(surface, ink, lamp, max(1, s // 20))


def _icon_park(surface, rect, color, ink):
    """公园：树。"""
    inner, s = _box(rect)
    trunk = pygame.Rect(0, 0, _ratio(s * 0.14), _ratio(s * 0.28))
    trunk.midbottom = (inner.centerx, inner.bottom - _ratio(s * 0.04))
    pygame.draw.rect(surface, ink, trunk)
    pygame.draw.circle(surface, color, (inner.centerx, inner.y + _ratio(s * 0.36)),
                       _ratio(s * 0.34))
    pygame.draw.circle(surface, ink, (inner.centerx, inner.y + _ratio(s * 0.36)),
                       _ratio(s * 0.34), max(1, s // 20))


def _icon_trophy(surface, rect, color, ink):
    """奖金池 / 胜利：奖杯。"""
    inner, s = _box(rect)
    cup = pygame.Rect(inner.x + _ratio(s * 0.22), inner.y + _ratio(s * 0.08),
                      _ratio(s * 0.56), _ratio(s * 0.44))
    pygame.draw.polygon(surface, color, [
        (cup.x, cup.y), (cup.right, cup.y),
        (cup.centerx + _ratio(s * 0.12), cup.bottom),
        (cup.centerx - _ratio(s * 0.12), cup.bottom),
    ])
    pygame.draw.rect(surface, color, pygame.Rect(inner.x + _ratio(s * 0.34),
                                                 cup.bottom, _ratio(s * 0.32),
                                                 _ratio(s * 0.16)))
    base = pygame.Rect(inner.x + _ratio(s * 0.2), inner.bottom - _ratio(s * 0.16),
                       _ratio(s * 0.6), _ratio(s * 0.14))
    pygame.draw.rect(surface, color, base)
    pygame.draw.rect(surface, ink, base, max(1, s // 20))


def _icon_dice(surface, rect, color, ink):
    inner, s = _box(rect)
    r = pygame.Rect(inner.x + _ratio(s * 0.08), inner.y + _ratio(s * 0.08),
                    _ratio(s * 0.84), _ratio(s * 0.84))
    pygame.draw.rect(surface, color, r, border_radius=max(2, s // 8))
    pygame.draw.rect(surface, ink, r, max(1, s // 18), border_radius=max(2, s // 8))
    pip = max(2, s // 12)
    for dx, dy in ((0.26, 0.26), (0.74, 0.26), (0.5, 0.5), (0.26, 0.74), (0.74, 0.74)):
        pygame.draw.circle(surface, ink,
                           (r.x + int(r.width * dx), r.y + int(r.height * dy)), pip)


# ==================================================================== 道具卡

def _icon_coin(surface, rect, color, ink):
    inner, s = _box(rect)
    r = _ratio(s * 0.42)
    pygame.draw.circle(surface, color, inner.center, r)
    pygame.draw.circle(surface, ink, inner.center, r, max(2, s // 14))
    pygame.draw.circle(surface, ink, inner.center, max(2, int(r * 0.42)), max(1, s // 22))


def _icon_cash(surface, rect, color, ink):
    inner, s = _box(rect)
    r = pygame.Rect(inner.x, inner.y + _ratio(s * 0.2), inner.width, _ratio(s * 0.6))
    pygame.draw.rect(surface, color, r, border_radius=max(2, s // 16))
    pygame.draw.rect(surface, ink, r, max(1, s // 18), border_radius=max(2, s // 16))
    pygame.draw.circle(surface, ink, r.center, max(2, int(s * 0.13)), max(1, s // 26))


def _icon_shield(surface, rect, color, ink):
    inner, s = _box(rect)
    pts = [(inner.centerx, inner.y + _ratio(s * 0.06)),
           (inner.right - _ratio(s * 0.06), inner.y + _ratio(s * 0.24)),
           (inner.centerx, inner.bottom - _ratio(s * 0.04)),
           (inner.x + _ratio(s * 0.06), inner.y + _ratio(s * 0.24))]
    pygame.draw.polygon(surface, color, pts)
    pygame.draw.polygon(surface, ink, pts, max(2, s // 14))


def _icon_guard(surface, rect, color, ink):
    _icon_shield(surface, rect, color, ink)
    inner, s = _box(rect)
    pygame.draw.line(surface, ink, (inner.x + int(s * 0.34), inner.centery),
                     (inner.centerx - int(s * 0.04), inner.y + int(s * 0.6)),
                     max(2, s // 12))
    pygame.draw.line(surface, ink, (inner.centerx - int(s * 0.04), inner.y + int(s * 0.6)),
                     (inner.right - int(s * 0.3), inner.y + int(s * 0.36)),
                     max(2, s // 12))


def _icon_portal(surface, rect, color, ink):
    inner, s = _box(rect)
    r = pygame.Rect(inner.x, inner.y, inner.width, inner.height)
    for k, width in enumerate((0, 2, 4)):
        rr = r.inflate(-int(s * 0.18 * k), -int(s * 0.18 * k))
        pygame.draw.ellipse(surface, color if k % 2 == 0 else ink,
                            rr, max(1, s // 18) if k else 0)
    pygame.draw.ellipse(surface, ink, r, max(2, s // 16))


def _icon_swap(surface, rect, color, ink):
    inner, s = _box(rect)
    y1 = inner.y + int(s * 0.34)
    y2 = inner.y + int(s * 0.68)
    w = max(2, s // 12)
    pygame.draw.line(surface, color, (inner.x, y1), (inner.right - int(s * 0.2), y1), w)
    pygame.draw.polygon(surface, color, [(inner.right - int(s * 0.24), y1 - int(s * 0.14)),
                                        (inner.right - int(s * 0.24), y1 + int(s * 0.14)),
                                        (inner.right, y1)])
    pygame.draw.line(surface, color, (inner.right, y2), (inner.x + int(s * 0.2), y2), w)
    pygame.draw.polygon(surface, color, [(inner.x + int(s * 0.24), y2 - int(s * 0.14)),
                                         (inner.x + int(s * 0.24), y2 + int(s * 0.14)),
                                         (inner.x, y2)])


def _icon_hand(surface, rect, color, ink):
    inner, s = _box(rect)
    palm = pygame.Rect(inner.x + _ratio(s * 0.16), inner.y + _ratio(s * 0.42),
                       _ratio(s * 0.68), _ratio(s * 0.5))
    pygame.draw.rect(surface, color, palm, border_radius=max(2, s // 10))
    pygame.draw.rect(surface, ink, palm, max(1, s // 20), border_radius=max(2, s // 10))
    for i in range(3):
        x = palm.x + int(palm.width * (0.2 + i * 0.3))
        pygame.draw.line(surface, color, (x, palm.y), (x, inner.y + _ratio(s * 0.16)),
                         max(3, s // 10))
    pygame.draw.line(surface, color, (inner.x + _ratio(s * 0.12), palm.y + _ratio(s * 0.1)),
                     (inner.x + _ratio(s * 0.02), inner.y + _ratio(s * 0.5)),
                     max(3, s // 12))


def _icon_anchor(surface, rect, color, ink):
    inner, s = _box(rect)
    pygame.draw.circle(surface, color, (inner.centerx, inner.y + _ratio(s * 0.16)),
                       max(2, _ratio(s * 0.12)))
    pygame.draw.line(surface, color, (inner.centerx, inner.y + _ratio(s * 0.28)),
                     (inner.centerx, inner.bottom - _ratio(s * 0.14)), max(2, s // 12))
    pygame.draw.line(surface, color, (inner.x + _ratio(s * 0.28), inner.y + _ratio(s * 0.4)),
                     (inner.right - _ratio(s * 0.28), inner.y + _ratio(s * 0.4)),
                     max(2, s // 14))
    pygame.draw.arc(surface, color, pygame.Rect(inner.x + _ratio(s * 0.08),
                                                inner.y + _ratio(s * 0.36),
                                                _ratio(s * 0.84), _ratio(s * 0.6)),
                    math.pi, math.tau, max(2, s // 10))


def _icon_cone(surface, rect, color, ink):
    inner, s = _box(rect)
    tri = [(inner.centerx, inner.y + _ratio(s * 0.08)),
           (inner.right - _ratio(s * 0.06), inner.bottom - _ratio(s * 0.22)),
           (inner.x + _ratio(s * 0.06), inner.bottom - _ratio(s * 0.22))]
    pygame.draw.polygon(surface, color, tri)
    pygame.draw.polygon(surface, ink, tri, max(2, s // 16))
    base = pygame.Rect(inner.x, inner.bottom - _ratio(s * 0.2), inner.width,
                       _ratio(s * 0.18))
    pygame.draw.rect(surface, color, base, border_radius=max(2, s // 20))
    pygame.draw.rect(surface, ink, base, max(1, s // 22), border_radius=max(2, s // 20))


def _icon_hammer(surface, rect, color, ink, down: bool = False):
    inner, s = _box(rect)
    head = pygame.Rect(inner.x + _ratio(s * 0.08), inner.y + _ratio(s * 0.1),
                       _ratio(s * 0.56), _ratio(s * 0.3))
    pygame.draw.rect(surface, color, head, border_radius=max(2, s // 20))
    pygame.draw.rect(surface, ink, head, max(1, s // 20), border_radius=max(2, s // 20))
    pygame.draw.line(surface, color, (head.centerx + _ratio(s * 0.06), head.bottom),
                     (inner.right - _ratio(s * 0.1), inner.bottom - _ratio(s * 0.06)),
                     max(3, s // 9))
    if down:
        pygame.draw.line(surface, ink, (inner.x + _ratio(s * 0.1), inner.bottom - _ratio(s * 0.2)),
                         (inner.right - _ratio(s * 0.1), inner.bottom - _ratio(s * 0.2)),
                         max(2, s // 16))


def _icon_tag(surface, rect, color, ink):
    inner, s = _box(rect)
    pts = [(inner.x + _ratio(s * 0.08), inner.y + _ratio(s * 0.12)),
           (inner.right - _ratio(s * 0.28), inner.y + _ratio(s * 0.12)),
           (inner.right - _ratio(s * 0.08), inner.y + _ratio(s * 0.4)),
           (inner.x + _ratio(s * 0.32), inner.bottom - _ratio(s * 0.06)),
           (inner.x + _ratio(s * 0.08), inner.y + _ratio(s * 0.5))]
    pygame.draw.polygon(surface, color, pts)
    pygame.draw.polygon(surface, ink, pts, max(1, s // 20))
    pygame.draw.circle(surface, ink, (inner.x + int(s * 0.28), inner.y + int(s * 0.32)),
                       max(2, s // 16))


def _icon_umbrella(surface, rect, color, ink):
    inner, s = _box(rect)
    dome = pygame.Rect(inner.x, inner.y + _ratio(s * 0.12), inner.width,
                       _ratio(s * 0.54))
    pygame.draw.arc(surface, color, dome, 0, math.pi, max(3, s // 8))
    pygame.draw.line(surface, color, (inner.centerx, inner.y + _ratio(s * 0.4)),
                     (inner.centerx, inner.bottom - _ratio(s * 0.12)), max(2, s // 12))
    pygame.draw.arc(surface, color, pygame.Rect(inner.centerx, inner.bottom - _ratio(s * 0.3),
                                                _ratio(s * 0.24), _ratio(s * 0.3)),
                    math.pi, math.tau, max(2, s // 14))
    for dx in (-0.3, 0.0, 0.3):
        x = inner.centerx + int(inner.width * dx)
        pygame.draw.line(surface, ink, (x, inner.y + _ratio(s * 0.24)),
                         (x, inner.y + _ratio(s * 0.36)), max(1, s // 24))


def _icon_replay(surface, rect, color, ink):
    inner, s = _box(rect)
    r = pygame.Rect(inner.x, inner.y, inner.width, inner.height)
    pygame.draw.arc(surface, color, r, -0.4, math.pi * 1.4, max(3, s // 9))
    tip = (inner.right - _ratio(s * 0.04), inner.y + _ratio(s * 0.06))
    pygame.draw.polygon(surface, color, [(tip[0] - _ratio(s * 0.24), tip[1]),
                                         (tip[0], tip[1] + _ratio(s * 0.3)),
                                         (tip[0], tip[1] - _ratio(s * 0.06))])


def _icon_key(surface, rect, color, ink):
    inner, s = _box(rect)
    pygame.draw.circle(surface, color, (inner.x + _ratio(s * 0.28), inner.centery),
                       _ratio(s * 0.24))
    pygame.draw.circle(surface, ink, (inner.x + _ratio(s * 0.28), inner.centery),
                       _ratio(s * 0.24), max(1, s // 20))
    pygame.draw.circle(surface, ink, (inner.x + _ratio(s * 0.28), inner.centery),
                       _ratio(s * 0.08))
    pygame.draw.line(surface, color, (inner.x + _ratio(s * 0.48), inner.centery),
                     (inner.right, inner.centery), max(3, s // 11))
    pygame.draw.line(surface, color, (inner.right - _ratio(s * 0.22), inner.centery),
                     (inner.right - _ratio(s * 0.22), inner.centery + _ratio(s * 0.22)),
                     max(2, s // 14))


def _icon_broom(surface, rect, color, ink):
    inner, s = _box(rect)
    pygame.draw.line(surface, color, (inner.right - _ratio(s * 0.12), inner.y),
                     (inner.centerx, inner.centery + _ratio(s * 0.1)), max(3, s // 11))
    head = [(inner.x, inner.bottom), (inner.centerx + _ratio(s * 0.2), inner.centery),
            (inner.centerx - _ratio(s * 0.08), inner.bottom)]
    pygame.draw.polygon(surface, color, head)
    pygame.draw.polygon(surface, ink, head, max(1, s // 22))


def _icon_scales(surface, rect, color, ink):
    inner, s = _box(rect)
    pygame.draw.line(surface, color, (inner.centerx, inner.y + _ratio(s * 0.08)),
                     (inner.centerx, inner.bottom - _ratio(s * 0.14)), max(2, s // 13))
    pygame.draw.line(surface, color, (inner.x + _ratio(s * 0.12), inner.y + _ratio(s * 0.24)),
                     (inner.right - _ratio(s * 0.12), inner.y + _ratio(s * 0.24)),
                     max(2, s // 14))
    for x in (inner.x + _ratio(s * 0.12), inner.right - _ratio(s * 0.12)):
        pygame.draw.arc(surface, color, pygame.Rect(int(x) - _ratio(s * 0.16),
                                                    inner.y + _ratio(s * 0.24),
                                                    _ratio(s * 0.32), _ratio(s * 0.3)),
                        math.pi, math.tau, max(2, s // 14))
    pygame.draw.line(surface, color, (inner.x + _ratio(s * 0.24),
                                      inner.bottom - _ratio(s * 0.14)),
                     (inner.right - _ratio(s * 0.24), inner.bottom - _ratio(s * 0.14)),
                     max(2, s // 16))


def _icon_clover(surface, rect, color, ink):
    inner, s = _box(rect)
    r = s * 0.19
    for k in range(4):
        ang = math.pi / 2 * k
        px = inner.centerx + math.cos(ang + math.pi / 4) * r
        py = inner.centery + math.sin(ang + math.pi / 4) * r
        pygame.draw.circle(surface, color, (int(px), int(py)), int(r))
    for k in range(4):
        ang = math.pi / 2 * k
        px = inner.centerx + math.cos(ang + math.pi / 4) * r
        py = inner.centery + math.sin(ang + math.pi / 4) * r
        pygame.draw.circle(surface, ink, (int(px), int(py)), int(r), max(1, s // 26))


def _icon_lock(surface, rect, color, ink):
    inner, s = _box(rect)
    body = pygame.Rect(inner.x + _ratio(s * 0.14), inner.y + _ratio(s * 0.42),
                       _ratio(s * 0.72), _ratio(s * 0.52))
    pygame.draw.rect(surface, color, body, border_radius=max(2, s // 16))
    pygame.draw.rect(surface, ink, body, max(1, s // 20), border_radius=max(2, s // 16))
    pygame.draw.arc(surface, color, pygame.Rect(inner.x + _ratio(s * 0.26),
                                                inner.y + _ratio(s * 0.06),
                                                _ratio(s * 0.48), _ratio(s * 0.56)),
                    math.pi, math.tau, max(3, s // 10))
    pygame.draw.circle(surface, ink, body.center, max(2, s // 18))


def _icon_hammerdown(surface, rect, color, ink):
    _icon_hammer(surface, rect, color, ink, down=True)


def _icon_swap2(surface, rect, color, ink):
    _icon_swap(surface, rect, color, ink)
    inner, s = _box(rect)
    pygame.draw.rect(surface, ink, pygame.Rect(inner.x + int(s * 0.12),
                                               inner.y + int(s * 0.12),
                                               int(s * 0.26), int(s * 0.22)),
                     max(1, s // 22))
    pygame.draw.rect(surface, ink, pygame.Rect(inner.right - int(s * 0.38),
                                               inner.bottom - int(s * 0.34),
                                               int(s * 0.26), int(s * 0.22)),
                     max(1, s // 22))


def _icon_card(surface, rect, color, ink):
    inner, s = _box(rect)
    r = pygame.Rect(inner.x + _ratio(s * 0.14), inner.y + _ratio(s * 0.06),
                    _ratio(s * 0.72), _ratio(s * 0.88))
    pygame.draw.rect(surface, color, r, border_radius=max(2, s // 14))
    pygame.draw.rect(surface, ink, r, max(1, s // 18), border_radius=max(2, s // 14))
    for i in range(3):
        y = r.y + int(r.height * (0.28 + i * 0.22))
        pygame.draw.line(surface, ink, (r.x + int(r.width * 0.2), y),
                         (r.right - int(r.width * 0.2), y), max(1, s // 26))


def _icon_star(surface, rect, color, ink):
    inner, s = _box(rect)
    pts = []
    for k in range(10):
        ang = -math.pi / 2 + k * math.pi / 5
        radius = s * 0.46 if k % 2 == 0 else s * 0.19
        pts.append((inner.centerx + math.cos(ang) * radius,
                    inner.centery + math.sin(ang) * radius))
    pygame.draw.polygon(surface, color, pts)
    pygame.draw.polygon(surface, ink, pts, max(1, s // 22))


def _icon_bell(surface, rect, color, ink):
    inner, s = _box(rect)
    body = [(inner.x + _ratio(s * 0.14), inner.bottom - _ratio(s * 0.26)),
            (inner.x + _ratio(s * 0.26), inner.y + _ratio(s * 0.3)),
            (inner.right - _ratio(s * 0.26), inner.y + _ratio(s * 0.3)),
            (inner.right - _ratio(s * 0.14), inner.bottom - _ratio(s * 0.26))]
    pygame.draw.polygon(surface, color, body)
    pygame.draw.polygon(surface, ink, body, max(1, s // 22))
    pygame.draw.rect(surface, color, pygame.Rect(inner.x + _ratio(s * 0.12),
                                                 inner.bottom - _ratio(s * 0.3),
                                                 _ratio(s * 0.76), _ratio(s * 0.1)))
    pygame.draw.circle(surface, ink, (inner.centerx, inner.bottom - _ratio(s * 0.12)),
                       max(2, _ratio(s * 0.1)))


# ==================================================================== 界面

def _icon_play(surface, rect, color, ink):
    inner, s = _box(rect)
    tri = [(inner.x + _ratio(s * 0.2), inner.y + _ratio(s * 0.08)),
           (inner.x + _ratio(s * 0.2), inner.bottom - _ratio(s * 0.08)),
           (inner.right - _ratio(s * 0.1), inner.centery)]
    pygame.draw.polygon(surface, color, tri)
    pygame.draw.polygon(surface, ink, tri, max(1, s // 20))


def _icon_network(surface, rect, color, ink):
    inner, s = _box(rect)
    pygame.draw.circle(surface, color, (inner.centerx, inner.bottom - _ratio(s * 0.08)),
                       max(3, _ratio(s * 0.1)))
    for i, yy in enumerate((0.56, 0.32, 0.1)):
        r = pygame.Rect(0, 0, int(s * (0.4 + i * 0.28)), int(s * (0.4 + i * 0.28)))
        r.center = (inner.centerx, inner.bottom - _ratio(s * 0.08))
        pygame.draw.arc(surface, color, r, math.pi * 0.75, math.pi * 1.25,
                        max(2, s // 18))


def _icon_copy(surface, rect, color, ink):
    inner, s = _box(rect)
    back = pygame.Rect(inner.x, inner.y, _ratio(s * 0.58), _ratio(s * 0.58))
    front = pygame.Rect(inner.x + _ratio(s * 0.42), inner.y + _ratio(s * 0.42),
                        _ratio(s * 0.58), _ratio(s * 0.58))
    pygame.draw.rect(surface, ink, back, max(2, s // 20), border_radius=max(2, s // 20))
    pygame.draw.rect(surface, color, front, border_radius=max(2, s // 20))
    pygame.draw.rect(surface, ink, front, max(2, s // 20), border_radius=max(2, s // 20))


def _icon_refresh(surface, rect, color, ink):
    inner, s = _box(rect)
    r = pygame.Rect(inner.x, inner.y, inner.width, inner.height)
    pygame.draw.arc(surface, color, r, 0.5, math.pi * 1.7, max(3, s // 10))
    pygame.draw.polygon(surface, color, [
        (inner.right - _ratio(s * 0.16), inner.y + _ratio(s * 0.06)),
        (inner.right, inner.y + _ratio(s * 0.2)),
        (inner.right - _ratio(s * 0.3), inner.y + _ratio(s * 0.34)),
    ])


def _icon_book(surface, rect, color, ink):
    inner, s = _box(rect)
    body = pygame.Rect(inner.x + _ratio(s * 0.08), inner.y + _ratio(s * 0.14),
                       _ratio(s * 0.84), _ratio(s * 0.72))
    pygame.draw.rect(surface, color, body, border_radius=max(2, s // 20))
    pygame.draw.rect(surface, ink, body, max(1, s // 20), border_radius=max(2, s // 20))
    pygame.draw.line(surface, ink, (body.centerx, body.y + 2),
                     (body.centerx, body.bottom - 2), max(1, s // 26))


def _icon_gear(surface, rect, color, ink):
    inner, s = _box(rect)
    cx, cy = inner.centerx, inner.centery
    for k in range(8):
        ang = k * math.pi / 4
        x1 = cx + math.cos(ang) * s * 0.3
        y1 = cy + math.sin(ang) * s * 0.3
        x2 = cx + math.cos(ang) * s * 0.46
        y2 = cy + math.sin(ang) * s * 0.46
        pygame.draw.line(surface, color, (int(x1), int(y1)), (int(x2), int(y2)),
                         max(3, s // 9))
    pygame.draw.circle(surface, color, (cx, cy), _ratio(s * 0.3))
    pygame.draw.circle(surface, ink, (cx, cy), max(3, _ratio(s * 0.13)))


def _icon_save(surface, rect, color, ink):
    inner, s = _box(rect)
    body = pygame.Rect(inner.x + _ratio(s * 0.06), inner.y + _ratio(s * 0.06),
                       _ratio(s * 0.88), _ratio(s * 0.88))
    pygame.draw.rect(surface, color, body, border_radius=max(2, s // 22))
    pygame.draw.rect(surface, ink, body, max(1, s // 20), border_radius=max(2, s // 22))
    pygame.draw.rect(surface, ink, pygame.Rect(body.x + int(body.width * 0.28),
                                               body.y + 2, int(body.width * 0.44),
                                               int(body.height * 0.3)))
    pygame.draw.rect(surface, ink, pygame.Rect(body.x + int(body.width * 0.2),
                                               body.bottom - int(body.height * 0.34),
                                               int(body.width * 0.6),
                                               int(body.height * 0.3)))


def _icon_exit(surface, rect, color, ink):
    inner, s = _box(rect)
    frame = pygame.Rect(inner.x + _ratio(s * 0.06), inner.y, _ratio(s * 0.56),
                        inner.height)
    pygame.draw.arc(surface, color, frame, 0, math.tau, max(2, s // 14))
    pygame.draw.line(surface, color, (frame.centerx, inner.centery),
                     (inner.right, inner.centery), max(3, s // 11))
    pygame.draw.polygon(surface, color, [(inner.right - _ratio(s * 0.2), inner.centery - _ratio(s * 0.16)),
                                        (inner.right - _ratio(s * 0.2), inner.centery + _ratio(s * 0.16)),
                                        (inner.right, inner.centery)])


def _icon_help(surface, rect, color, ink):
    inner, s = _box(rect)
    pygame.draw.circle(surface, color, inner.center, _ratio(s * 0.46))
    pygame.draw.circle(surface, ink, inner.center, _ratio(s * 0.46), max(1, s // 22))
    pygame.draw.arc(surface, ink, pygame.Rect(inner.centerx - int(s * 0.16),
                                              inner.y + int(s * 0.2),
                                              int(s * 0.32), int(s * 0.3)),
                    math.pi * 0.9, math.pi * 2.1, max(2, s // 16))
    pygame.draw.circle(surface, ink, (inner.centerx, inner.bottom - int(s * 0.2)),
                       max(2, s // 18))


def _icon_settings_audio(surface, rect, color, ink):
    inner, s = _box(rect)
    body = pygame.Rect(inner.x, inner.y + _ratio(s * 0.3), _ratio(s * 0.36),
                       _ratio(s * 0.4))
    pygame.draw.rect(surface, color, body)
    pygame.draw.polygon(surface, color, [
        (body.right, body.y), (inner.x + _ratio(s * 0.6), inner.y + _ratio(s * 0.1)),
        (inner.x + _ratio(s * 0.6), inner.bottom - _ratio(s * 0.1)), (body.right, body.bottom)])
    for i, w in enumerate((0.18, 0.3)):
        pygame.draw.arc(surface, ink,
                        pygame.Rect(inner.x + _ratio(s * (0.6 + i * 0.0)),
                                    inner.y + _ratio(s * (0.3 - i * 0.14)),
                                    _ratio(s * (0.24 + i * 0.16)),
                                    _ratio(s * (0.4 + i * 0.28))),
                        -math.pi / 2.4, math.pi / 2.4, max(2, s // 20))


def _icon_check(surface, rect, color, ink):
    inner, s = _box(rect)
    pygame.draw.lines(surface, color, False, [
        (inner.x, inner.centery),
        (inner.centerx - _ratio(s * 0.05), inner.bottom - _ratio(s * 0.12)),
        (inner.right, inner.y + _ratio(s * 0.12)),
    ], max(3, s // 8))


def _icon_cross(surface, rect, color, ink):
    inner, s = _box(rect)
    w = max(3, s // 8)
    pygame.draw.line(surface, color, inner.topleft, inner.bottomright, w)
    pygame.draw.line(surface, color, (inner.right, inner.y), (inner.x, inner.bottom), w)


def _icon_arrow_right(surface, rect, color, ink):
    inner, s = _box(rect)
    pygame.draw.polygon(surface, color, [
        (inner.x + _ratio(s * 0.2), inner.y + _ratio(s * 0.1)),
        (inner.x + _ratio(s * 0.2), inner.bottom - _ratio(s * 0.1)),
        (inner.right - _ratio(s * 0.1), inner.centery)])


def _icon_arrow_left(surface, rect, color, ink):
    flip = pygame.Surface(rect.size, pygame.SRCALPHA)
    local = pygame.Rect(0, 0, rect.width, rect.height)
    _icon_arrow_right(flip, local, color, ink)
    surface.blit(pygame.transform.flip(flip, True, False), rect.topleft)


def _icon_plus(surface, rect, color, ink):
    inner, s = _box(rect)
    w = max(2, s // 9)
    pygame.draw.line(surface, color, (inner.centerx, inner.y + _ratio(s * 0.12)),
                     (inner.centerx, inner.bottom - _ratio(s * 0.12)), w)
    pygame.draw.line(surface, color, (inner.x + _ratio(s * 0.12), inner.centery),
                     (inner.right - _ratio(s * 0.12), inner.centery), w)


def _icon_minus(surface, rect, color, ink):
    inner, s = _box(rect)
    w = max(2, s // 9)
    pygame.draw.line(surface, color, (inner.x + _ratio(s * 0.12), inner.centery),
                     (inner.right - _ratio(s * 0.12), inner.centery), w)


def _icon_crown(surface, rect, color, ink):
    inner, s = _box(rect)
    pts = [(inner.x, inner.bottom - _ratio(s * 0.16)),
           (inner.x + _ratio(s * 0.1), inner.y + _ratio(s * 0.34)),
           (inner.centerx, inner.y + _ratio(s * 0.58)),
           (inner.right - _ratio(s * 0.1), inner.y + _ratio(s * 0.34)),
           (inner.right, inner.bottom - _ratio(s * 0.16))]
    pygame.draw.polygon(surface, color, pts)
    pygame.draw.polygon(surface, ink, pts, max(1, s // 20))
    base = pygame.Rect(inner.x, inner.bottom - _ratio(s * 0.14), inner.width,
                       _ratio(s * 0.14))
    pygame.draw.rect(surface, color, base)
    pygame.draw.rect(surface, ink, base, max(1, s // 24))


def _icon_person(surface, rect, color, ink):
    inner, s = _box(rect)
    pygame.draw.circle(surface, color, (inner.centerx, inner.y + _ratio(s * 0.26)),
                       _ratio(s * 0.22))
    pygame.draw.circle(surface, ink, (inner.centerx, inner.y + _ratio(s * 0.26)),
                       _ratio(s * 0.22), max(1, s // 22))
    pygame.draw.arc(surface, color, pygame.Rect(inner.x + _ratio(s * 0.1),
                                                inner.y + _ratio(s * 0.5),
                                                _ratio(s * 0.8), _ratio(s * 0.6)),
                    math.pi, math.tau, max(3, s // 9))


def _icon_info(surface, rect, color, ink):
    inner, s = _box(rect)
    pygame.draw.circle(surface, color, inner.center, _ratio(s * 0.46))
    pygame.draw.circle(surface, ink, (inner.centerx, inner.y + int(s * 0.22)),
                       max(2, s // 18))
    pygame.draw.line(surface, ink, (inner.centerx, inner.y + int(s * 0.36)),
                     (inner.centerx, inner.bottom - int(s * 0.16)), max(2, s // 10))


def _icon_alert(surface, rect, color, ink):
    _icon_disaster(surface, rect, color, ink)


def _icon_map(surface, rect, color, ink):
    inner, s = _box(rect)
    r = pygame.Rect(inner.x, inner.y + _ratio(s * 0.1), inner.width, _ratio(s * 0.76))
    pygame.draw.rect(surface, color, r, border_radius=max(2, s // 22))
    pygame.draw.rect(surface, ink, r, max(1, s // 22), border_radius=max(2, s // 22))
    pygame.draw.line(surface, ink, (r.x, r.y + int(r.height * 0.4)),
                     (r.right, r.y + int(r.height * 0.4)), max(1, s // 26))
    pygame.draw.circle(surface, ink, (r.x + int(r.width * 0.34),
                                      r.y + int(r.height * 0.24)),
                       max(2, s // 20))


def _icon_rules(surface, rect, color, ink):
    _icon_scales(surface, rect, color, ink)


def _icon_clock(surface, rect, color, ink):
    inner, s = _box(rect)
    pygame.draw.circle(surface, color, inner.center, _ratio(s * 0.46))
    pygame.draw.circle(surface, ink, inner.center, _ratio(s * 0.46), max(1, s // 22))
    pygame.draw.line(surface, ink, inner.center, (inner.centerx, inner.y + int(s * 0.18)),
                     max(2, s // 16))
    pygame.draw.line(surface, ink, inner.center, (inner.right - int(s * 0.2), inner.centery),
                     max(2, s // 16))


def _icon_trophy_small(surface, rect, color, ink):
    _icon_trophy(surface, rect, color, ink)


def _icon_gift(surface, rect, color, ink):
    inner, s = _box(rect)
    box = pygame.Rect(inner.x + _ratio(s * 0.08), inner.y + _ratio(s * 0.38),
                      _ratio(s * 0.84), _ratio(s * 0.56))
    pygame.draw.rect(surface, color, box)
    pygame.draw.rect(surface, ink, box, max(1, s // 22))
    pygame.draw.line(surface, ink, (box.centerx, box.y), (box.centerx, box.bottom),
                     max(2, s // 14))
    pygame.draw.rect(surface, color, pygame.Rect(box.x, box.y - _ratio(s * 0.16),
                                                 box.width, _ratio(s * 0.16)))
    pygame.draw.rect(surface, ink, pygame.Rect(box.x, box.y - _ratio(s * 0.16),
                                               box.width, _ratio(s * 0.16)),
                     max(1, s // 24))


def _icon_bolt(surface, rect, color, ink):
    inner, s = _box(rect)
    pts = [(inner.centerx + _ratio(s * 0.2), inner.y),
           (inner.x + _ratio(s * 0.18), inner.centery + _ratio(s * 0.04)),
           (inner.centerx, inner.centery + _ratio(s * 0.04)),
           (inner.centerx - _ratio(s * 0.18), inner.bottom),
           (inner.right - _ratio(s * 0.16), inner.centery - _ratio(s * 0.06)),
           (inner.centerx, inner.centery - _ratio(s * 0.06))]
    pygame.draw.polygon(surface, color, pts)


ICONS: dict[str, Callable] = {
    # 格子
    "start": _icon_start,
    "property": _icon_property,
    "station": _icon_station,
    "fortune": _icon_fortune,
    "disaster": _icon_disaster,
    "shop": _icon_shop,
    "tax": _icon_tax,
    "jail": _icon_jail,
    "police": _icon_police,
    "park": _icon_park,
    "trophy": _icon_trophy,
    "dice": _icon_dice,
    # 道具（与 data/cards.json 的 icon 字段一一对应）
    "shield": _icon_shield,
    "coin": _icon_coin,
    "portal": _icon_portal,
    "swap": _icon_swap,
    "hand": _icon_hand,
    "guard": _icon_guard,
    "anchor": _icon_anchor,
    "cone": _icon_cone,
    "hammer": _icon_hammer,
    "hammerdown": _icon_hammerdown,
    "tag": _icon_tag,
    "cash": _icon_cash,
    "umbrella": _icon_umbrella,
    "swap2": _icon_swap2,
    "replay": _icon_replay,
    "key": _icon_key,
    "broom": _icon_broom,
    "scales": _icon_scales,
    "clover": _icon_clover,
    "lock": _icon_lock,
    "card": _icon_card,
    "star": _icon_star,
    "bell": _icon_bell,
    # 界面
    "play": _icon_play,
    "network": _icon_network,
    "copy": _icon_copy,
    "refresh": _icon_refresh,
    "book": _icon_book,
    "gear": _icon_gear,
    "save": _icon_save,
    "exit": _icon_exit,
    "help": _icon_help,
    "audio": _icon_settings_audio,
    "check": _icon_check,
    "cross": _icon_cross,
    "arrow_right": _icon_arrow_right,
    "arrow_left": _icon_arrow_left,
    "plus": _icon_plus,
    "minus": _icon_minus,
    "crown": _icon_crown,
    "person": _icon_person,
    "info": _icon_info,
    "alert": _icon_alert,
    "map": _icon_map,
    "rules": _icon_rules,
    "clock": _icon_clock,
    "gift": _icon_gift,
    "bolt": _icon_bolt,
}

#: 建筑等级图标（0～3 级）
BUILDING_LEVEL_ICONS = {0: None, 1: "building1", 2: "building2", 3: "building3"}


def _icon_building_1(surface, rect, color, ink):
    _icon_building(surface, rect, color, ink, floors=1)


def _icon_building_2(surface, rect, color, ink):
    _icon_building(surface, rect, color, ink, floors=2)


def _icon_building_3(surface, rect, color, ink):
    _icon_building(surface, rect, color, ink, floors=3, high=True)


ICONS.update({
    "building1": _icon_building_1,
    "building2": _icon_building_2,
    "building3": _icon_building_3,
})


def has_icon(name: str) -> bool:
    return name in ICONS


def draw_icon(
    surface: pygame.Surface,
    name: str,
    rect: pygame.Rect | tuple[int, int, int, int],
    color: tuple[int, int, int] | tuple[int, int, int, int],
    ink: tuple[int, int, int] = (30, 38, 52),
) -> None:
    """在 rect 内绘制指定图标。color 是主色，ink 是描边色。"""
    r = pygame.Rect(rect)
    if r.width <= 2 or r.height <= 2:
        return
    fn = ICONS.get(name)
    if fn is None:
        pygame.draw.circle(surface, color[:3], r.center, max(2, min(r.width, r.height) // 4))
        return
    if len(color) == 4:
        # 半透明图标：先画到临时层再整体设置 alpha
        layer = pygame.Surface(r.size, pygame.SRCALPHA)
        local = pygame.Rect(0, 0, r.width, r.height)
        try:
            fn(layer, local, color[:3], ink)
        except Exception:
            return
        layer.set_alpha(color[3])
        surface.blit(layer, r.topleft)
        return
    try:
        fn(surface, r, color, ink)
    except Exception:  # pragma: no cover - 图标永不致命
        return
