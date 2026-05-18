#!/usr/bin/env python3
"""
memstats.py — interactive per-NUMA-node memory statistics (like top)

Keys:
  q / Ctrl-C  quit
  space       force refresh
  h           toggle help
  s           change sort column (Total/Used/Dirty/Anon/Slab/Hit)
  n           toggle number format (KB / MB / GB / auto)
  d           set refresh delay
  H           toggle highlight thresholds

Sources (all per NUMA node):
  /sys/devices/system/node/nodeN/meminfo  — Total, Free, Used, Slab
  /sys/devices/system/node/nodeN/vmstat   — Dirty, Anon (pages × 4KB)
  /sys/devices/system/node/nodeN/numastat — hit, miss, foreign
"""

import curses
import os
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path

PAGE_KB  = os.sysconf("SC_PAGE_SIZE") // 1024
HOSTNAME = os.uname().nodename

SORT_KEYS   = ["node", "total", "free", "used", "dirty", "anon", "slab", "hit", "miss"]
SORT_LABELS = ["Node", "Total", "Free", "Used", "Dirty", "Anon", "Slab", "Hit", "Miss"]
UNITS       = ["KB", "MB", "GB", "auto"]

DEFAULT_DELAY  = 2.0
WARN_DIRTY_PCT = 5      # % of total → amber
CRIT_DIRTY_PCT = 15     # % of total → red
WARN_USED_PCT  = 75
CRIT_USED_PCT  = 90
WARN_MISS      = 100    # numa_miss per sample → amber
CRIT_MISS      = 1000   # numa_miss per sample → red


# ── readers ────────────────────────────────────────────────────────────────

def read_kv(path: str, sep: str = None) -> dict:
    d = {}
    try:
        with open(path) as f:
            for line in f:
                parts = line.split(sep)
                if len(parts) >= 2:
                    key   = parts[-2].split(":")[-1].strip().rstrip(":")
                    try:
                        d[key] = int(parts[-1].split()[0])
                    except (ValueError, IndexError):
                        pass
    except (FileNotFoundError, PermissionError):
        pass
    return d


def read_node_meminfo(node: int) -> dict:
    path = f"/sys/devices/system/node/node{node}/meminfo"
    d = {}
    try:
        with open(path) as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4:
                    key = parts[2].rstrip(":")
                    try:
                        d[key] = int(parts[3])
                    except ValueError:
                        pass
    except (FileNotFoundError, PermissionError):
        pass
    return d


def read_node_vmstat(node: int) -> dict:
    path = f"/sys/devices/system/node/node{node}/vmstat"
    d = {}
    try:
        with open(path) as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2:
                    try:
                        d[parts[0]] = int(parts[1])
                    except ValueError:
                        pass
    except (FileNotFoundError, PermissionError):
        pass
    return d


def read_node_numastat(node: int) -> dict:
    path = f"/sys/devices/system/node/node{node}/numastat"
    d = {}
    try:
        with open(path) as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2:
                    try:
                        d[parts[0]] = int(parts[1])
                    except ValueError:
                        pass
    except (FileNotFoundError, PermissionError):
        pass
    return d


def discover_nodes() -> list:
    base = Path("/sys/devices/system/node")
    nodes = []
    if base.exists():
        for p in sorted(base.iterdir()):
            if p.name.startswith("node") and p.name[4:].isdigit():
                nodes.append(int(p.name[4:]))
    return sorted(nodes)


# ── stat collection ────────────────────────────────────────────────────────

def node_stats(node: int) -> dict:
    mi   = read_node_meminfo(node)
    vm   = read_node_vmstat(node)
    numa = read_node_numastat(node)

    total = mi.get("MemTotal", 0)
    free  = mi.get("MemFree",  0)
    used  = mi.get("MemUsed",  total - free)
    slab  = mi.get("Slab", mi.get("SReclaimable", 0) + mi.get("SUnreclaim", 0))
    dirty = vm.get("nr_dirty",      0) * PAGE_KB
    anon  = vm.get("nr_anon_pages", vm.get("nr_mapped", 0)) * PAGE_KB
    hit   = numa.get("numa_hit",     numa.get("local_node",  0))
    miss  = numa.get("numa_miss",    numa.get("other_node",  0))
    fore  = numa.get("numa_foreign", 0)

    return dict(
        node=node,
        total=total, free=free, used=used,
        dirty=dirty, anon=anon, slab=slab,
        hit=hit, miss=miss, fore=fore,
    )


def collect(nodes: list) -> list:
    return [node_stats(n) for n in nodes]


def totals(stats: list) -> dict:
    t = dict(node=-1, total=0, free=0, used=0,
             dirty=0, anon=0, slab=0, hit=0, miss=0, fore=0)
    for s in stats:
        for k in t:
            if k != "node":
                t[k] += s[k]
    return t


# ── formatting ─────────────────────────────────────────────────────────────

def fmt(kb: int, unit: str) -> str:
    if unit == "auto":
        if   kb >= 1024*1024: return f"{kb/1024/1024:>7.1f}G"
        elif kb >= 1024:      return f"{kb/1024:>7.1f}M"
        else:                  return f"{kb:>8,}"
    elif unit == "GB":
        return f"{kb/1024/1024:>7.2f}G"
    elif unit == "MB":
        return f"{kb/1024:>7.1f}M"
    else:
        return f"{kb:>10,}"


# ── curses UI ──────────────────────────────────────────────────────────────

C_DEFAULT = 0
C_HEADER  = 1
C_TITLE   = 2
C_WARN    = 3
C_CRIT    = 4
C_GOOD    = 5
C_DIM     = 6
C_SORT    = 7
C_TOTROW  = 8
C_KEY     = 9


def init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(C_HEADER, curses.COLOR_BLACK,  curses.COLOR_CYAN)
    curses.init_pair(C_TITLE,  curses.COLOR_CYAN,   -1)
    curses.init_pair(C_WARN,   curses.COLOR_YELLOW, -1)
    curses.init_pair(C_CRIT,   curses.COLOR_RED,    -1)
    curses.init_pair(C_GOOD,   curses.COLOR_GREEN,  -1)
    curses.init_pair(C_DIM,    curses.COLOR_WHITE,  -1)
    curses.init_pair(C_SORT,   curses.COLOR_WHITE,  curses.COLOR_BLUE)
    curses.init_pair(C_TOTROW, curses.COLOR_WHITE,  curses.COLOR_BLACK)
    curses.init_pair(C_KEY,    curses.COLOR_YELLOW, -1)


def row_color(s: dict, col: str) -> int:
    total = s.get("total", 1) or 1
    if col == "used":
        pct = s["used"] * 100 // total
        return C_CRIT if pct >= CRIT_USED_PCT else (C_WARN if pct >= WARN_USED_PCT else C_DEFAULT)
    if col == "dirty":
        pct = s["dirty"] * 100 // total
        return C_CRIT if pct >= CRIT_DIRTY_PCT else (C_WARN if pct >= WARN_DIRTY_PCT else C_DEFAULT)
    if col == "miss":
        return C_CRIT if s["miss"] >= CRIT_MISS else (C_WARN if s["miss"] >= WARN_MISS else C_DEFAULT)
    if col == "free":
        pct = s["free"] * 100 // total
        return C_GOOD if pct > 50 else (C_WARN if pct < 20 else C_DEFAULT)
    return C_DEFAULT


def draw_bar(stdscr, y: int, x: int, width: int, used: int, total: int):
    """Draw a small inline bar: [████░░░░] used/total."""
    if total == 0:
        return
    filled = min(width, int(width * used / total))
    stdscr.addstr(y, x, "[", curses.color_pair(C_DIM))
    pct = used * 100 // total
    bar_col = (curses.color_pair(C_CRIT) if pct >= CRIT_USED_PCT
               else curses.color_pair(C_WARN) if pct >= WARN_USED_PCT
               else curses.color_pair(C_GOOD))
    stdscr.addstr("█" * filled, bar_col)
    stdscr.addstr("░" * (width - filled), curses.color_pair(C_DIM))
    stdscr.addstr("]", curses.color_pair(C_DIM))


def draw_help(stdscr, y: int, x: int):
    items = [
        ("q",     "quit"),
        ("space", "refresh now"),
        ("s",     "cycle sort"),
        ("n",     "cycle units"),
        ("d",     "set delay"),
        ("h",     "hide help"),
    ]
    for k, v in items:
        stdscr.addstr(y, x, k, curses.color_pair(C_KEY) | curses.A_BOLD)
        stdscr.addstr(f" {v}  ", curses.color_pair(C_DIM))
        x += len(k) + len(v) + 4


def main_loop(stdscr, nodes: list, delay: float, sort_idx: int, unit_idx: int):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(200)
    init_colors()

    show_help  = True
    last_fetch = 0.0
    stats      = []
    prev_numa  = {}   # node → numastat snapshot for delta hit/miss
    deltas     = {}

    while True:
        # ── input ──────────────────────────────────────────────────────
        ch = stdscr.getch()
        if ch in (ord("q"), ord("Q"), 27):
            break
        elif ch == ord(" "):
            last_fetch = 0
        elif ch in (ord("s"), ord("S")):
            sort_idx = (sort_idx + 1) % len(SORT_KEYS)
            last_fetch = 0
        elif ch in (ord("n"), ord("N")):
            unit_idx = (unit_idx + 1) % len(UNITS)
        elif ch in (ord("h"), ord("H")):
            show_help = not show_help
        elif ch in (ord("d"), ord("D")):
            # prompt for new delay
            curses.curs_set(1)
            stdscr.nodelay(False)
            h, w = stdscr.getmaxyx()
            prompt = "Delay (seconds): "
            stdscr.addstr(h - 1, 0, prompt, curses.color_pair(C_KEY))
            curses.echo()
            try:
                raw = stdscr.getstr(h - 1, len(prompt), 6).decode()
                delay = max(0.5, float(raw))
            except (ValueError, curses.error):
                pass
            curses.noecho()
            stdscr.nodelay(True)
            curses.curs_set(0)

        # ── fetch ──────────────────────────────────────────────────────
        now = time.monotonic()
        if now - last_fetch >= delay:
            new_stats = collect(nodes)
            # compute hit/miss deltas
            for s in new_stats:
                n = s["node"]
                if n in prev_numa:
                    deltas[n] = {
                        "hit":  max(0, s["hit"]  - prev_numa[n]["hit"]),
                        "miss": max(0, s["miss"] - prev_numa[n]["miss"]),
                        "fore": max(0, s["fore"] - prev_numa[n]["fore"]),
                    }
                else:
                    deltas[n] = {"hit": s["hit"], "miss": s["miss"], "fore": s["fore"]}
                prev_numa[n] = {"hit": s["hit"], "miss": s["miss"], "fore": s["fore"]}
                # replace absolute hit/miss/fore with deltas for display
                s["hit_d"]  = deltas[n]["hit"]
                s["miss_d"] = deltas[n]["miss"]
                s["fore_d"] = deltas[n]["fore"]

            # sort
            sk = SORT_KEYS[sort_idx]
            if sk == "node":
                new_stats.sort(key=lambda x: x["node"])
            else:
                new_stats.sort(key=lambda x: x.get(sk, 0), reverse=True)

            stats     = new_stats
            last_fetch = now

        # ── draw ───────────────────────────────────────────────────────
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        unit = UNITS[unit_idx]
        ts   = datetime.now().strftime("%a %b %d %H:%M:%S %Y")

        row = 0

        # title bar
        title = f" memstats — {HOSTNAME}   {ts}   delay:{delay:.1f}s   unit:{unit}"
        stdscr.addstr(row, 0, title.ljust(w)[:w], curses.color_pair(C_HEADER) | curses.A_BOLD)
        row += 1

        # help bar
        if show_help:
            draw_help(stdscr, row, 1)
            row += 1

        row += 1  # blank

        # column headers
        sort_label = SORT_KEYS[sort_idx]
        COL_W = 11 if unit != "KB" else 13

        def col_hdr(label: str, key: str) -> str:
            s = label.rjust(COL_W)
            return s

        hdr = f"{'NODE':>4}  "
        cols = [
            ("Total",  "total"),
            ("Free",   "free"),
            ("Used",   "used"),
            ("Dirty",  "dirty"),
            ("Anon",   "anon"),
            ("Slab",   "slab"),
        ]
        stdscr.addstr(row, 0, f"{'NODE':>4}  ", curses.color_pair(C_DIM))
        x = 6
        for label, key in cols:
            attr = curses.color_pair(C_SORT) | curses.A_BOLD if key == sort_label else curses.color_pair(C_DIM) | curses.A_BOLD
            s = label.rjust(COL_W)
            stdscr.addstr(row, x, s, attr)
            x += COL_W + 1
        # bar column header
        bar_w = min(20, max(8, w - x - 40))
        stdscr.addstr(row, x, f"{'bar':^{bar_w+2}}", curses.color_pair(C_DIM) | curses.A_BOLD)
        x += bar_w + 3
        for label, key in [("Δhit", "hit"), ("Δmiss", "miss"), ("Δfore", "fore")]:
            attr = curses.color_pair(C_SORT) | curses.A_BOLD if key == sort_label else curses.color_pair(C_DIM) | curses.A_BOLD
            stdscr.addstr(row, x, label.rjust(9), attr)
            x += 10
        row += 1

        # separator
        stdscr.addstr(row, 0, "─" * min(w - 1, 100), curses.color_pair(C_DIM))
        row += 1

        # data rows
        tot = totals(stats) if stats else {}
        for s in stats:
            if row >= h - 3:
                break
            nd = s["node"]
            stdscr.addstr(row, 0, f"{nd:>4}  ", curses.color_pair(C_TITLE) | curses.A_BOLD)
            x = 6
            for label, key in cols:
                val  = s.get(key, 0)
                col  = row_color(s, key)
                text = fmt(val, unit).rjust(COL_W)
                stdscr.addstr(row, x, text, curses.color_pair(col))
                x += COL_W + 1
            # usage bar
            draw_bar(stdscr, row, x, bar_w, s["used"], s["total"])
            x += bar_w + 3
            # delta numastat
            for dkey, dattr in [("hit_d", C_GOOD), ("miss_d", C_WARN), ("fore_d", C_WARN)]:
                dv  = s.get(dkey, 0)
                col = C_DEFAULT if dv == 0 else dattr
                if dkey == "miss_d" and dv >= CRIT_MISS:
                    col = C_CRIT
                stdscr.addstr(row, x, f"{dv:>9,}", curses.color_pair(col))
                x += 10
            row += 1

        # totals row
        if stats and row < h - 2:
            stdscr.addstr(row, 0, "─" * min(w - 1, 100), curses.color_pair(C_DIM))
            row += 1
            stdscr.addstr(row, 0, " TOT  ", curses.color_pair(C_TOTROW) | curses.A_BOLD)
            x = 6
            for label, key in cols:
                val  = tot.get(key, 0)
                text = fmt(val, unit).rjust(COL_W)
                # colour used% relative to total
                fake = {**tot, "node": -1}
                col  = row_color(fake, key)
                stdscr.addstr(row, x, text, curses.color_pair(col) | curses.A_BOLD)
                x += COL_W + 1
            draw_bar(stdscr, row, x, bar_w, tot["used"], tot["total"])
            x += bar_w + 3
            th = sum(s.get("hit_d",  0) for s in stats)
            tm = sum(s.get("miss_d", 0) for s in stats)
            tf = sum(s.get("fore_d", 0) for s in stats)
            for v, col in [(th, C_GOOD), (tm, C_WARN if tm else C_DEFAULT), (tf, C_WARN if tf else C_DEFAULT)]:
                stdscr.addstr(row, x, f"{v:>9,}", curses.color_pair(col) | curses.A_BOLD)
                x += 10
            row += 1

        # status line
        if row < h - 1:
            elapsed = time.monotonic() - last_fetch
            next_in = max(0.0, delay - elapsed)
            status  = f" next refresh in {next_in:.1f}s  |  sort: {SORT_LABELS[sort_idx]}  |  {len(nodes)} NUMA node(s)"
            stdscr.addstr(row, 0, status[:w - 1], curses.color_pair(C_DIM))

        stdscr.refresh()


def main():
    parser = argparse.ArgumentParser(
        description="Interactive per-NUMA-node memory stats (like top)"
    )
    parser.add_argument("-d", "--delay",   type=float, default=DEFAULT_DELAY,
                        metavar="SECS",    help=f"refresh interval (default {DEFAULT_DELAY}s)")
    parser.add_argument("-s", "--sort",    type=str,   default="node",
                        choices=SORT_KEYS, help="initial sort column")
    parser.add_argument("-u", "--unit",    type=str,   default="auto",
                        choices=UNITS,     help="display unit (default auto)")
    args = parser.parse_args()

    nodes = discover_nodes()
    if not nodes:
        sys.exit("No NUMA nodes found. Single-node or missing sysfs NUMA support.")

    sort_idx = SORT_KEYS.index(args.sort) if args.sort in SORT_KEYS else 0
    unit_idx = UNITS.index(args.unit)     if args.unit  in UNITS     else 3

    try:
        curses.wrapper(main_loop, nodes, args.delay, sort_idx, unit_idx)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
