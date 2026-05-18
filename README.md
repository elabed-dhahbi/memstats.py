# memstats.py

Interactive per-NUMA-node memory statistics monitor, inspired by the SGI
`memory_stats` tool and built to look and feel like `top`.

Reads directly from the Linux sysfs NUMA interfaces — no external dependencies
beyond the Python standard library.

---

## Requirements

- Python 3.6+
- Linux kernel with NUMA sysfs support (`/sys/devices/system/node/`)
- A terminal with colour support (xterm-256color recommended)
- No pip packages required — uses only `curses`, `os`, `sys`, `time`,
  `argparse`, `datetime`, `pathlib`

---

## Installation

```bash
chmod +x memstats.py
# optionally copy to your PATH
sudo cp memstats.py /usr/local/bin/memstats
```

---

## Usage

```bash
python3 memstats.py [options]
```

| Option | Default | Description |
|---|---|---|
| `-d SECS` / `--delay SECS` | `2.0` | Refresh interval in seconds (minimum 0.5) |
| `-s COL` / `--sort COL` | `node` | Initial sort column — see column list below |
| `-u UNIT` / `--unit UNIT` | `auto` | Display unit: `KB`, `MB`, `GB`, or `auto` |

### Examples

```bash
# Basic — 2 second refresh, auto units
python3 memstats.py

# Faster refresh, sort by most used
python3 memstats.py -d 1 -s used

# Show values in megabytes
python3 memstats.py -u MB

# Sort by NUMA miss rate at startup
python3 memstats.py -s miss
```

---

## Interactive keys

| Key | Action |
|---|---|
| `q` / `Esc` / `Ctrl-C` | Quit |
| `Space` | Force immediate refresh |
| `s` | Cycle sort column (Node → Total → Free → Used → Dirty → Anon → Slab → Hit → Miss) |
| `n` | Cycle display units (auto → KB → MB → GB) |
| `d` | Prompt for a new refresh delay — type a number and press Enter |
| `h` | Toggle the key bar on/off |

---

## Columns

All memory values are displayed in the unit selected by `n` or `--unit`.

| Column | Source | Description |
|---|---|---|
| `NODE` | — | NUMA node number |
| `Total` | `nodeN/meminfo` → `MemTotal` | Total physical memory on this node |
| `Free` | `nodeN/meminfo` → `MemFree` | Completely free pages |
| `Used` | `nodeN/meminfo` → `MemUsed` | Total − Free (or `MemUsed` if present) |
| `Dirty` | `nodeN/vmstat` → `nr_dirty × page_size` | Pages modified in page cache, not yet written to disk |
| `Anon` | `nodeN/vmstat` → `nr_anon_pages × page_size` | Anonymous (non-file-backed) pages: heap, stack, mmap private |
| `Slab` | `nodeN/meminfo` → `Slab` | Kernel slab allocator: inodes, dentries, network buffers |
| `bar` | — | Inline usage bar — proportional to Used/Total |
| `Δhit` | `nodeN/numastat` → `numa_hit` delta | Local allocations that succeeded this interval (green = good) |
| `Δmiss` | `nodeN/numastat` → `numa_miss` delta | Allocations that fell back to a remote NUMA node (amber/red = pressure) |
| `Δfore` | `nodeN/numastat` → `numa_foreign` delta | Allocations intended for this node but served by another |

The `Δ` prefix means hit/miss/foreign are shown as **deltas since the previous
sample**, not cumulative lifetime totals. This makes NUMA locality pressure
visible as it happens rather than as a monotonically growing number.

---

## Colour coding

| Colour | Meaning |
|---|---|
| Green | Healthy — Used < 75%, Free > 50%, Δhit > 0 |
| Amber | Warning — Used ≥ 75%, Dirty > 5% of total, Δmiss > 100/interval |
| Red | Critical — Used ≥ 90%, Dirty > 15% of total, Δmiss > 1000/interval |
| Blue highlight | The column currently used for sorting |

Thresholds are defined as constants at the top of the script and can be
edited directly:

```python
WARN_DIRTY_PCT = 5      # % of total → amber
CRIT_DIRTY_PCT = 15     # % of total → red
WARN_USED_PCT  = 75
CRIT_USED_PCT  = 90
WARN_MISS      = 100    # numa_miss per interval → amber
CRIT_MISS      = 1000   # numa_miss per interval → red
```

---

## Data sources

All data is read from the Linux kernel's per-node sysfs interface.
No root privileges are required.

```
/sys/devices/system/node/nodeN/meminfo   — Total, Free, Used, Slab
/sys/devices/system/node/nodeN/vmstat    — Dirty, Anon (page counts)
/sys/devices/system/node/nodeN/numastat  — hit, miss, foreign
```

### Single-node systems

On a single-node or non-NUMA system, `node0` is still present and the script
runs normally — you see one data row. The Δhit/Δmiss/Δfore columns are still
meaningful: a high miss rate on a single-node system indicates failed local
allocations, typically due to memory pressure or fragmentation.

### Kernel version notes

| Field | Kernel availability |
|---|---|
| `MemUsed` in node meminfo | Available on most kernels; falls back to `MemTotal − MemFree` |
| `nr_anon_pages` in node vmstat | Available since kernel 2.6.18 |
| `numa_foreign` in numastat | Available since kernel 2.6.18 |

---

## What to look for

**High `Δmiss` on any node** — allocations are crossing NUMA boundaries.
Causes: process spread across nodes without memory binding, insufficient
local memory, `vm.zone_reclaim_mode=0` on a loaded system. Fix: check
`numactl --hardware`, consider `numactl --membind` or `--cpunodebind` for
the offending process, or raise `vm.zone_reclaim_mode` to 1.

**High `Dirty` (amber/red)** — a large backlog of writes is building up in
the page cache and has not been flushed to disk. Causes: heavy write workload
exceeding disk throughput, `vm.dirty_ratio` / `vm.dirty_background_ratio` set
too high, storage latency spike holding up writeback. Check `iostat -xz 1`
and `xfsstats.py` for journal pressure.

**High `Slab`** — kernel metadata caches are consuming a large fraction of
node memory. Common drivers: very large inode/dentry working sets
(`xfs_inode`, `dentry` in `slabtop`), high network connection counts
(`sock_inode_cache`, `TCPv6`). On a metadata-heavy XFS workload this is
expected — see `slabtop -o` to confirm which caches dominate.

**Imbalanced `Total` across nodes** — NUMA nodes have different amounts of
physical memory. This is a hardware configuration issue and cannot be tuned
at runtime.

**Free falling on one node while others are idle** — a process is pinned to
one node without memory binding. Use `numactl --hardware` to check node
topology and `cat /proc/PID/numa_maps` to see where a process's pages live.

---

## Screen layout

```
 memstats — hostname   Mon May 18 15:55:32 2026   delay:2.0s   unit:auto
 q quit   space refresh now   s cycle sort   n cycle units   d set delay   h hide help

 NODE         Total         Free         Used        Dirty         Anon         Slab   [  bar   ]      Δhit  Δmiss  Δfore
──────────────────────────────────────────────────────────────────────────────────────────────────────
    0       32.0G        18.2G        13.8G        512.0M         2.1G         1.4G   [████░░░░]    35,347      0      0
    1       32.0G        20.1G        11.9G        128.0M         1.8G         1.2G   [████░░░░]    29,812      4      0
    2       32.0G        22.4G         9.6G         64.0M         1.1G         0.9G   [███░░░░░]    31,024      0      0
    3       32.0G        19.8G        12.2G        256.0M         1.6G         1.1G   [████░░░░]    28,491      0      0
──────────────────────────────────────────────────────────────────────────────────────────────────────
 TOT       128.0G        80.5G        47.5G         960.0M        6.6G         4.6G   [████░░░░]   124,674      4      0
 next refresh in 1.4s  |  sort: Node  |  4 NUMA node(s)
```

---

## Relationship to the original SGI tool

The original `memory_stats` command on SGI IRIX and early SGI Linux systems
(as seen on `floyd2`, Jun 2011) reported the same columns — Total, Free, Used,
Dirty, Anon, Slab, and NUMA preferred-allocation hit/miss/foreign — one row
per NUMA node with a totals line. This script reproduces that layout using the
modern Linux sysfs NUMA interfaces, adds live colour thresholds, an inline
usage bar, delta numastat counters, interactive sort and unit switching, and
`top`-style in-place refresh via Python `curses`.

---

## License

MIT — do whatever you like with it.

