"""
The Textual TUI.

Keys: f / 1-4 framework · m impact×effort map · d duel the item against the next one down
      [ ] pick a weight · + - change it (custom) · o open in browser · r re-fetch · R re-ask all · q quit
"""
import asyncio
import os
import webbrowser

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import DataTable, Footer, Header, ProgressBar, Static

from jev_board import cli, github, goals, retrieval, scoring

BLOCKS = " ▁▂▃▄▅▆▇█"
FW_KEYS = list(scoring.FRAMEWORKS)
DIM_COLOR = {"reach": "cyan", "impact": "magenta", "urgency": "red", "effort": "yellow",
             "clarity": "green", "unblocks": "blue", "alignment": "bright_white"}
QUAD_STYLE = {"quick win": "bold green", "big bet": "bold magenta", "fill-in": "dim", "money pit": "red"}


def spark(v):
    return BLOCKS[min(8, round(v * 8))]


def bar(v, width=20, style="white"):
    full = int(v * width)
    return Text("█" * full + ("▌" if v * width - full >= 0.5 else ""), style=style).append(
        " " * (width - full), style="on grey15")


def take(it):
    """A one-line reading of the scores, in code: what kind of item this is."""
    n = it["norm"]
    bits = [it["quadrant"]]
    if it["goals"]:
        bits.append("named by a goal")
    if scoring.sinks(it):
        bits.append("looks shipped")
    elif it["shipped"] != "open":
        bits.append(f"Jev says {it['shipped']}; check before starting")
    if n["unblocks"] >= 0.6 or it["referenced_by"] >= 3:
        bits.append(f"long pole (referenced by {it['referenced_by']})")
    if n["urgency"] >= 0.7:
        bits.append("on fire")
    if n["clarity"] <= 0.35:
        bits.append("needs shaping before anyone starts")
    if it["blocked_by"] and not it["blocked_by"].lower().startswith("none"):
        bits.append("says it is blocked")
    return " · ".join(bits)


def short_refs(items):
    """Drop the prefix every repo on the board shares, so refs fit the table."""
    repos = {it["repo"] for it in items if it.get("repo")}
    prefix = os.path.commonprefix(sorted(repos)) if len(repos) > 1 else ""
    cut = prefix.rfind("-") + 1 if "-" in prefix else 0
    return lambda ref: ref[cut:] if cut and ref.startswith(prefix[:cut]) else ref


class JevBoard(App):
    TITLE = "Jev board"
    CSS = """
    #main { height: 1fr; }
    #table { width: 3fr; }
    #side { width: 2fr; border-left: tall $primary; padding: 0 1; }
    #progress { height: 1; margin: 0 1; }
    #weights { height: auto; padding: 0 1; background: $boost; }
    """
    BINDINGS = [
        ("f", "next_framework", "Framework"), ("m", "toggle_map", "Map"), ("d", "duel", "Duel"),
        ("o", "open", "Open"), ("r", "refetch", "Re-fetch"), ("R", "reask", "Re-ask all"),
        ("left_square_bracket", "weight_dim(-1)", "◀ weight"), ("right_square_bracket", "weight_dim(1)", "weight ▶"),
        ("plus,equals_sign", "weight(1)", "+"), ("minus", "weight(-1)", "−"),
        ("1", "framework(0)", ""), ("2", "framework(1)", ""), ("3", "framework(2)", ""), ("4", "framework(3)", ""),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, settings):
        super().__init__()
        self.s = settings
        self.meta, self.items, self.ranked, self.ctx = {}, [], [], None
        self.fw, self.weights, self.wdim = settings.framework, dict(scoring.DEFAULT_WEIGHTS), 0
        self.show_map, self.duels, self.spent = False, {}, 0
        self.short = lambda ref: ref

    def compose(self) -> ComposeResult:
        yield Header()
        yield ProgressBar(id="progress", show_eta=False)
        yield Static(id="weights")
        with Horizontal(id="main"):
            yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="side"):
                yield Static(id="detail")
        yield Footer()

    def on_mount(self):
        self.query_one(DataTable).add_columns("#", "score", "R I U E C B G", "shape", "ref", "title", "status")
        self.load()

    # ── data ──

    @work(exclusive=True)
    async def load(self, refresh=False):
        s = self.s
        self.sub_title = f"board {s.board} · fetching from GitHub…"
        self.meta, self.items, total = await asyncio.to_thread(github.fetch, s.board, s.owner, s.status)
        self.title = f"Jev board · {s.board} · {self.meta.get('title', '')}"
        self.short = short_refs(self.items)
        self.sub_title = "reading codebases…"
        repos = await asyncio.to_thread(retrieval.load_repos, s.repos)
        g = goals.load(s.goals) if s.goals else None
        self.ctx = cli.context(s, self.meta)
        bar_ = self.query_one(ProgressBar)
        bar_.update(total=len(self.items), progress=0)
        done = [0]

        def tick(_it):
            done[0] += 1
            bar_.update(progress=done[0])
        stats = await scoring.assess(self.items, self.ctx, repos, g, refresh or s.refresh, on_progress=tick)
        bar_.update(progress=len(self.items))
        self.spent += stats["tokens"]
        self.notify(f"{len(self.items)} open of {total} · Jev asked about {stats['asked']}, aligned "
                    f"{stats['aligned']}, {len(self.items) - stats['asked']} from cache · "
                    f"{stats['tokens']:,} input tokens")
        self.refresh_view()

    def refresh_view(self, keep=None):
        t = self.query_one(DataTable)
        keep = keep or self.current()
        self.ranked = scoring.rank(self.items, self.fw, self.weights)
        t.clear()
        for it in self.ranked:
            dims = Text()
            for k, v in it["norm"].items():
                dims.append(spark(v) + " ", style=DIM_COLOR[k])
            q = it["quadrant"]
            t.add_row(str(it["rank"]), bar(it["rel"], 10, "bold cyan"), dims, Text(q, style=QUAD_STYLE[q]),
                      cli.flags(it) + " " + self.short(it["ref"]), it["title"][:46], it["status"], key=it["id"])
        if keep:
            for i, it in enumerate(self.ranked):
                if it["id"] == keep["id"]:
                    t.move_cursor(row=i)
        name, desc, _ = scoring.FRAMEWORKS[self.fw]
        self.sub_title = f"{name} · {desc}"
        self.render_weights()
        self.render_detail()

    def current(self):
        t = self.query_one(DataTable)
        if not self.ranked or t.cursor_row is None or t.cursor_row >= len(self.ranked):
            return None
        return self.ranked[t.cursor_row]

    # ── panes ──

    def weight_keys(self):
        has_goals = any("alignment" in it.get("norm", {}) for it in self.items)
        return [k for k in self.weights if k != "alignment" or has_goals]

    def render_weights(self):
        w = self.query_one("#weights", Static)
        if self.fw != "custom":
            w.update(Text(f"framework {FW_KEYS.index(self.fw) + 1}/4 · press f or 1-4 to switch · "
                          "4 = custom weights you can tune", style="dim"))
            return
        line = Text("weights  ")
        for i, k in enumerate(self.weight_keys()):
            style = f"reverse {DIM_COLOR[k]}" if i == self.wdim else DIM_COLOR[k]
            line.append(f" {k} {'●' * self.weights[k]}{'○' * (5 - self.weights[k])} ", style=style)
        line.append("   [ ] select · + − adjust · effort counts as ease", style="dim")
        w.update(line)

    def render_detail(self):
        d = self.query_one("#detail", Static)
        it = self.current()
        if self.show_map:
            d.update(self.matrix(it))
            return
        if not it:
            d.update("")
            return
        head = Text()
        head.append(f"{it['ref']}  ", style="bold")
        head.append(f"#{it['rank']} under {scoring.FRAMEWORKS[self.fw][0]}\n", style="cyan")
        head.append(it["title"] + "\n\n", style="bold white")
        head.append(take(it) + "\n", style="italic yellow")
        facts = [f"status {it['status']}"] + [f"{k} {v if not isinstance(v, dict) else v.get('title', '')}"
                                               for k, v in it["fields"].items()]
        if it["labels"]:
            facts.append("labels " + ", ".join(it["labels"]))
        facts.append(f"referenced by {it['referenced_by']} other items")
        if it["blocked_by"]:
            facts.append(f"blocked by: {it['blocked_by']}")
        for gt in it["goals"]:
            facts.append(f"goal: {gt}")
        ev = it["evidence"]
        if ev:
            if ev["files"]:
                facts.append("touches " + ", ".join(ev["files"][:6]) + (" …" if len(ev["files"]) > 6 else ""))
            if ev["hits"]:
                facts.append("symbols found in " + ", ".join(f"{r} ({n} files)" for r, n in ev["hits"].items()))
            for m in ev["mentions"][:5]:
                facts.append(f"commit {m['repo']}@{m['sha'][:7]} {m['subject'][:60]}")
        head.append("\n".join(facts) + "\n", style="dim")

        qs = scoring.base_questions(self.s.audience)
        tbl = Table.grid(padding=(0, 1))
        tbl.add_column(width=9)
        tbl.add_column()
        answers = {**it["jev"]["answers"], **(it.get("align") or {}).get("answers", {})}
        for k, v in it["norm"].items():
            a = answers.get(k)
            if not a:  # clarity is computed; a goal-named item is aligned in code
                tbl.add_row(Text(k, style=f"bold {DIM_COLOR[k]}"),
                            Text.assemble(bar(v, 16, DIM_COLOR[k]), (f" {v:.2f}  from facts", "dim")))
                continue
            q = qs.get(k) or scoring.ALIGNMENT
            tbl.add_row(Text(k, style=f"bold {DIM_COLOR[k]}"),
                        Text.assemble(bar(v, 16, DIM_COLOR[k]),
                                      (f" {a['score']:.1f}/{scoring.LEVELS}  conf {a['confidence']:.2f}", "dim")))
            dist = Text("          ")
            for lv in range(scoring.LEVELS + 1):
                dist.append(spark(a["probabilities"].get(str(lv), 0)), style=DIM_COLOR[k])
            dist.append(f"  “{q.criteria[round(a['score'])]}”", style="italic")
            tbl.add_row("", dist)

        parts = [head, Text(""), tbl]
        if it["id"] in self.duels:
            parts.append(Text("\nduels", style="bold"))
            parts.extend(self.duels[it["id"]])
        d.update(Group(*parts))

    def matrix(self, sel, w=44, h=16):
        """Impact (up) × effort (right) scatter. Items plot as their rank."""
        mid = scoring.medians(self.ranked)
        mx, my = min(w - 1, int(mid["effort"] * w)), min(h - 1, int((1 - mid["impact"]) * h))
        grid = [[" "] * w for _ in range(h)]
        for x in range(w):
            grid[my][x] = "┄"
        for y in range(h):
            grid[y][mx] = "┆"
        spots = {}
        for it in self.ranked:
            x = min(w - 1, int(it["norm"]["effort"] * w))
            y = min(h - 1, int((1 - it["norm"]["impact"]) * h))
            spots.setdefault((y, x), []).append(it)
        out = Text()
        out.append("impact ↑   effort →   lines at this board's medians\n", style="dim")
        out.append(Text.assemble(("quick wins", QUAD_STYLE["quick win"]), " ↖   ↗ ", ("big bets", QUAD_STYLE["big bet"]), "\n"))
        for y in range(h):
            x = 0
            row = Text()
            while x < w:
                hits = spots.get((y, x))
                if hits:
                    best = min(hits, key=lambda i: i["rank"])
                    label = str(best["rank"])
                    is_sel = sel and any(i["id"] == sel["id"] for i in hits)
                    row.append(label, style="reverse bold yellow" if is_sel else QUAD_STYLE[best["quadrant"]])
                    x += len(label)
                else:
                    row.append(grid[y][x], style="grey35")
                    x += 1
            out.append(row)
            out.append("\n")
        out.append(Text.assemble(("fill-ins", QUAD_STYLE["fill-in"]), "   ↙   ↘ ", ("money pits", QUAD_STYLE["money pit"]), "\n"))
        if sel:
            out.append(f"\nselected: #{sel['rank']} {sel['ref']} · {sel['title'][:60]}\n", style="yellow")
        out.append("press m for the detail pane", style="dim")
        return out

    # ── events and actions ──

    def on_data_table_row_highlighted(self, _event):
        self.render_detail()

    def action_framework(self, i):
        self.fw = FW_KEYS[i]
        self.refresh_view()

    def action_next_framework(self):
        self.action_framework((FW_KEYS.index(self.fw) + 1) % len(FW_KEYS))

    def action_toggle_map(self):
        self.show_map = not self.show_map
        self.render_detail()

    def action_weight_dim(self, step):
        if self.fw != "custom":
            self.action_framework(FW_KEYS.index("custom"))
        self.wdim = (self.wdim + step) % len(self.weight_keys())
        self.render_weights()

    def action_weight(self, step):
        if self.fw != "custom":
            self.action_framework(FW_KEYS.index("custom"))
        k = self.weight_keys()[self.wdim]
        self.weights[k] = max(0, min(5, self.weights[k] + step))
        self.refresh_view()

    def action_open(self):
        it = self.current()
        if it and it["url"]:
            webbrowser.open(it["url"])

    def action_refetch(self):
        self.load()

    def action_reask(self):
        self.load(refresh=True)

    @work(exclusive=True, group="duel")
    async def action_duel(self):
        i = self.query_one(DataTable).cursor_row
        if i is None or i + 1 >= len(self.ranked):
            self.notify("Duel needs an item below this one.", severity="warning")
            return
        a, b = self.ranked[i], self.ranked[i + 1]
        self.notify(f"⚔  {a['ref']} vs {b['ref']}: asking Jev both ways round…")
        r = await scoring.duel(a, b, self.ctx)
        self.spent += r["in_tokens"]
        win, lose, p = (a, b, r["p_a"]) if r["p_a"] >= 0.5 else (b, a, 1 - r["p_a"])
        verdict = "agrees with the ranking" if win is a else "upsets the ranking"
        shaky = "  (order-sensitive: answers moved {:.0%} when options swapped)".format(r["order_spread"]) \
            if r["order_spread"] > 0.2 else ""
        line = Text.assemble(("⚔ ", "bold"), (f"{win['ref']}", "bold green"), " before ",
                             (f"{lose['ref']}", "red"), f"  p={p:.2f} · {verdict}{shaky}")
        for it in (a, b):
            self.duels.setdefault(it["id"], []).append(line)
        self.notify(line.plain, severity="information" if win is a else "warning", timeout=8)
        self.render_detail()


def run(settings):
    JevBoard(settings).run()
