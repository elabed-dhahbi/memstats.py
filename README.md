472 lines, no dependencies beyond the standard library. Here is what it looks like and how it behaves:
Header bar (reversed cyan, full width):
memstats — lpcdb1   Mon May 18 15:55:32 2026   delay:2.0s   unit:auto
Key row:
q quit   space refresh now   s cycle sort   n cycle units   d set delay   h hide help
Column headers — the active sort column is highlighted in a blue bar:
NODE        Total        Free        Used       Dirty        Anon        Slab   [bar████░░]   Δhit   Δmiss   Δfore
Data rows — colour-coded live:

Used → green if < 75%, amber if ≥ 75%, red if ≥ 90%
Dirty → amber if > 5% of total, red if > 15%
Free → green if > 50%, amber if < 20%
Δmiss → amber if > 100/interval, red if > 1000/interval (NUMA locality problem)
Δhit → green (local allocations succeeding)

Bar column — inline [████████░░░░] proportional to used/total, colour follows used% threshold.
TOT row — bold, aggregates all nodes.
Status line — countdown to next refresh, current sort column, node count.
Interactive keys:
keyactionq / EscquitSpaceforce immediate refreshscycle sort: Node → Total → Free → Used → Dirty → Anon → Slab → Hit → Missncycle units: auto → KB → MB → GBdprompt for new delay in secondshhide/show the key bar
Δhit/Δmiss/Δfore show the delta since the previous sample, not the cumulative lifetime counter — so you see NUMA locality pressure as it happens rather than a monotonically growing number.
