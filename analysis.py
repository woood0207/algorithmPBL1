"""비최적 알고리즘(greedy / beam / local / random) 정량 분석 (repbl/heuristic_analysis.py 기반).

핵심 질문
  1) 거리가 멀어질수록 최적해를 찾을 확률(optimal rate)이 떨어지는가?
  2) 최적이 아닐 때 최적 대비 비용이 얼마나 벗어나는가?   (cost_ratio = 비용 / Dijkstra 비용)
  3) 대신 얼마나 빠른가?                                    (expanded, 시간 비율)

benchmark()는 repbl/path_algorithms.py의 --repeats 비교(results.json)를 옮긴 것이다.
"""
import csv
import json
import math
import random
import statistics
import time
from pathlib import Path

from graph_data import distances, haversine
from search import ALGORITHMS, run_search

EPS = 1e-9
ANALYSIS_ALGOS = ["bfs", "greedy", "beam", "local", "random"]


# ───────────────────────── 통계 보조 ─────────────────────────
def wilson(k, n, z=1.96):
    """비율의 95% Wilson 신뢰구간."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    i = (len(xs) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def spearman(x, y):
    """scipy 없이 스피어만 상관계수 (동순위는 평균순위)."""
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    if len(x) < 3:
        return None
    rx, ry = rank(x), rank(y)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


# ───────────────────────── 표본 추출 ─────────────────────────
def sample_pairs(graph, n, seed):
    """랜덤 (start, goal) 쌍. 도달 불가능한 쌍은 제외. 같은 시드면 항상 같은 쌍."""
    rng = random.Random(seed)
    ids = sorted(graph.coords)
    pairs, seen, tries = [], set(), 0
    reach_cache = {}
    while len(pairs) < n and tries < n * 50:
        tries += 1
        s, t = rng.sample(ids, 2)
        if (s, t) in seen:
            continue
        if s not in reach_cache:
            reach_cache[s] = distances(graph, s)
        if t not in reach_cache[s]:
            continue
        seen.add((s, t))
        pairs.append((s, t))
    return pairs


def timed(graph, algo, s, t, repeats, **opt):
    """결과와 중앙값 시간(ms). 첫 호출은 워밍업으로 버린다."""
    res = run_search(graph, algo, s, t, **opt)
    ts = []
    for _ in range(repeats):
        b = time.perf_counter()
        run_search(graph, algo, s, t, **opt)
        ts.append((time.perf_counter() - b) * 1000)
    return res, statistics.median(ts)


# ───────────────────────── 실험 본체 ─────────────────────────
def experiment(graph, algos, pairs, repeats, budget, beam_width, walk_seeds, progress=None):
    rows = []
    for idx, (s, t) in enumerate(pairs):
        base, base_ms = timed(graph, "dijkstra", s, t, repeats, budget=budget)
        straight = haversine(*graph.coords[s], *graph.coords[t])
        for algo in algos:
            # 확률적 알고리즘은 시드를 여러 개 돌려 '확률'로 본다. local은 결정적이라 1회만.
            for sd in (list(range(walk_seeds)) if algo == "random" else [0]):
                opt = dict(budget=budget, seed=sd, heuristic="geo" if ALGORITHMS[algo].uses_heuristic else None)
                if algo == "beam":
                    opt["beam_width"] = beam_width
                res, ms = timed(graph, algo, s, t, 1 if algo == "random" else repeats, **opt)
                ratio = res.cost / base.cost if res.found and base.cost else (1.0 if res.found else None)
                rows.append(dict(
                    pair=idx, start=s, goal=t, algo=algo, seed=sd,
                    straight_m=straight, opt_cost=base.cost, opt_hops=len(base.path) - 1,
                    found=res.found, reason=res.reason,
                    cost=res.cost, cost_ratio=ratio,
                    optimal=bool(res.found and ratio is not None and ratio <= 1 + EPS),
                    expanded=res.expanded, base_expanded=base.expanded,
                    ms=ms, base_ms=base_ms,
                    hops=len(res.path) - 1 if res.found else None,
                ))
        if progress:
            progress(idx + 1, len(pairs))
    return rows


def summarize(rows, key="opt_cost", bins=5):
    """key(기본: 최적 경로 길이) 분위수로 구간을 나눠 알고리즘별 요약."""
    vals = sorted({r["pair"]: r[key] for r in rows}.values())
    edges = [pct(vals, i / bins) for i in range(bins + 1)]
    edges[-1] += 1e-6

    def bin_of(v):
        for b in range(bins):
            if edges[b] <= v < edges[b + 1]:
                return b
        return bins - 1

    out = []
    for algo in sorted({r["algo"] for r in rows}):
        for b in range(bins):
            rs = [r for r in rows if r["algo"] == algo and bin_of(r[key]) == b]
            if not rs:
                continue
            n = len(rs)
            nf = sum(r["found"] for r in rs)
            no = sum(r["optimal"] for r in rs)
            ratios = [r["cost_ratio"] for r in rs if r["cost_ratio"] is not None]
            sub = [r["cost_ratio"] for r in rs if r["found"] and not r["optimal"]]
            out.append(dict(
                algo=algo, bin=b, lo=edges[b], hi=edges[b + 1], n=n,
                success=nf / n, success_ci=wilson(nf, n),
                optimal=no / n, optimal_ci=wilson(no, n),
                ratio_mean=statistics.mean(ratios) if ratios else None,
                ratio_median=statistics.median(ratios) if ratios else None,
                ratio_p90=pct(ratios, 0.9),
                ratio_max=max(ratios) if ratios else None,
                ratio_when_suboptimal=statistics.mean(sub) if sub else None,
                expanded_ratio=statistics.mean(r["expanded"] / r["base_expanded"] for r in rs),
                speedup=statistics.mean(r["base_ms"] / r["ms"] for r in rs if r["ms"] > 0),
            ))
    return out


def overall(rows):
    out = []
    for algo in sorted({r["algo"] for r in rows}):
        rs = [r for r in rows if r["algo"] == algo]
        n = len(rs)
        ratios = [r["cost_ratio"] for r in rs if r["cost_ratio"] is not None]
        xs = [r["opt_cost"] for r in rs]
        out.append(dict(
            algo=algo, n=n,
            success=sum(r["found"] for r in rs) / n,
            optimal=sum(r["optimal"] for r in rs) / n,
            optimal_ci=wilson(sum(r["optimal"] for r in rs), n),
            ratio_mean=statistics.mean(ratios) if ratios else None,
            ratio_p90=pct(ratios, 0.9),
            expanded_ratio=statistics.mean(r["expanded"] / r["base_expanded"] for r in rs),
            speedup=statistics.mean(r["base_ms"] / r["ms"] for r in rs if r["ms"] > 0),
            # 거리 ↑ → 최적 확률 ↓ 인지 보는 상관 (음수일수록 '멀수록 나쁨')
            rho_dist_vs_optimal=spearman(xs, [float(r["optimal"]) for r in rs]),
            rho_dist_vs_ratio=spearman([r["opt_cost"] for r in rs if r["cost_ratio"] is not None], ratios),
        ))
    return out


# ───────────────────────── 출력 ─────────────────────────
def f(x, fmt="{:.3f}"):
    return "-" if x is None else fmt.format(x)


def print_report(ov, by_bin):
    print("\n=== 전체 요약 (n = 시도 횟수) ===")
    print(f"{'algo':8} {'n':>5} {'도달률':>7} {'최적률':>7} {'최적률95%CI':>14} "
          f"{'비용비 평균':>10} {'p90':>6} {'확장수비':>8} {'속도배':>7} {'ρ(거리,최적)':>12} {'ρ(거리,비용비)':>13}")
    for o in ov:
        ci = f"[{o['optimal_ci'][0]:.2f},{o['optimal_ci'][1]:.2f}]"
        print(f"{o['algo']:8} {o['n']:>5} {o['success']:>7.1%} {o['optimal']:>7.1%} {ci:>14} "
              f"{f(o['ratio_mean']):>10} {f(o['ratio_p90']):>6} {o['expanded_ratio']:>8.2f} "
              f"{o['speedup']:>7.2f} {f(o['rho_dist_vs_optimal']):>12} {f(o['rho_dist_vs_ratio']):>13}")
    print("\n=== 거리 구간별 (구간 = 최적 경로 길이 분위수, 단위 m) ===")
    for algo in sorted({b['algo'] for b in by_bin}):
        print(f"\n[{algo}]")
        print(f"{'구간(m)':>14} {'n':>5} {'도달률':>7} {'최적률':>7} {'비용비 평균':>10} {'중앙':>6} "
              f"{'p90':>6} {'max':>6} {'확장수비':>8} {'속도배':>7}")
        for b in (x for x in by_bin if x["algo"] == algo):
            rng_ = f"{b['lo']:.0f}-{b['hi']:.0f}"
            print(f"{rng_:>14} {b['n']:>5} {b['success']:>7.1%} {b['optimal']:>7.1%} "
                  f"{f(b['ratio_mean']):>10} {f(b['ratio_median']):>6} {f(b['ratio_p90']):>6} "
                  f"{f(b['ratio_max']):>6} {b['expanded_ratio']:>8.2f} {b['speedup']:>7.2f}")
    print("\n* 비용비 = 알고리즘 비용 / Dijkstra 최적 비용 (1.0 이 최적, 1.2 면 20% 더 김)")
    print("* 확장수비 = 확장 노드 수 / Dijkstra 확장 노드 수 (1 미만이면 탐색량 이득)")
    print("* 속도배  = Dijkstra 시간 / 알고리즘 시간 (1 초과면 더 빠름)")


def save_plots(rows, by_bin, outdir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
    except ImportError:
        print("matplotlib 없음 → 그래프 생략")
        return
    algos = sorted({r["algo"] for r in rows})
    plt.rcParams["axes.unicode_minus"] = False
    for name in ("Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR"):
        if any(name in x.name for x in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = name
            break

    fig, ax = plt.subplots(1, 3, figsize=(16, 4.5))
    for a in algos:
        bs = [b for b in by_bin if b["algo"] == a]
        x = [(b["lo"] + b["hi"]) / 2 for b in bs]
        ax[0].errorbar(x, [b["optimal"] for b in bs],
                       yerr=[[max(0.0, b["optimal"] - b["optimal_ci"][0]) for b in bs],
                             [max(0.0, b["optimal_ci"][1] - b["optimal"]) for b in bs]],
                       marker="o", capsize=3, label=a)
        ax[1].plot(x, [b["ratio_mean"] for b in bs], marker="o", label=a)
        ax[2].plot(x, [b["expanded_ratio"] for b in bs], marker="o", label=a)
    ax[0].set(title="거리별 최적해 발견 확률 (95% CI)", xlabel="최적 경로 길이 (m)", ylabel="P(최적)", ylim=(-0.02, 1.02))
    ax[1].set(title="거리별 평균 비용비 (1.0 = 최적)", xlabel="최적 경로 길이 (m)", ylabel="비용 / 최적")
    ax[1].axhline(1, color="gray", lw=0.8, ls="--")
    ax[2].set(title="Dijkstra 대비 확장 노드 비율 (낮을수록 이득)", xlabel="최적 경로 길이 (m)", ylabel="확장수 / Dijkstra")
    ax[2].axhline(1, color="gray", lw=0.8, ls="--")
    for x_ in ax:
        x_.grid(alpha=0.3)
        x_.legend()
    fig.tight_layout()
    fig.savefig(outdir / "distance_analysis.png", dpi=150)
    plt.close(fig)

    # 산점도: 거리 vs 비용비 (알고리즘별)
    fig, axs = plt.subplots(1, len(algos), figsize=(4.2 * len(algos), 4), squeeze=False, sharey=True)
    for a, axx in zip(algos, axs[0]):
        rs = [r for r in rows if r["algo"] == a and r["cost_ratio"] is not None]
        axx.scatter([r["opt_cost"] for r in rs], [r["cost_ratio"] for r in rs], s=8, alpha=0.4)
        axx.axhline(1, color="gray", lw=0.8, ls="--")
        axx.set(title=a, xlabel="최적 경로 길이 (m)")
    axs[0][0].set_ylabel("비용 / 최적")
    fig.tight_layout()
    fig.savefig(outdir / "scatter_ratio.png", dpi=150)
    plt.close(fig)
    print("그래프 저장:", outdir / "distance_analysis.png", ",", outdir / "scatter_ratio.png")


def run_analysis(graph, algos=None, pairs=300, bins=5, seed=42, repeats=3, budget=2000,
                 beam_width=5, walk_seeds=10, progress=None):
    algos = algos or ANALYSIS_ALGOS
    chosen = sample_pairs(graph, pairs, seed)
    rows = experiment(graph, algos, chosen, repeats, budget, beam_width, walk_seeds, progress)
    by_bin = summarize(rows, bins=bins)
    return rows, overall(rows), by_bin, chosen


def save_analysis(rows, ov, by_bin, config, outdir):
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "raw_runs.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    with open(out / "summary.json", "w", encoding="utf-8") as fh:
        json.dump(dict(config=config, overall=ov, by_distance_bin=by_bin), fh, ensure_ascii=False, indent=2)
    print("저장:", out / "raw_runs.csv", ",", out / "summary.json")
    save_plots(rows, by_bin, out)


def compare_all(graph, start, goal, alt, repeats=1, **options):
    """같은 출발·도착으로 모든 알고리즘과 A* 휴리스틱을 비교한다.
    첫 실행은 워밍업으로 버리고 repeats번 측정한 중앙값을 쓴다."""
    baseline = run_search(graph, "dijkstra", start, goal)
    results = []
    for algorithm, spec in ALGORITHMS.items():
        if not spec.comparable:
            continue
        variants = ["haversine", "geo", "alt", "max"] if algorithm == "astar" else [None]
        for heuristic in variants:
            kw = dict(heuristic=heuristic or spec.default_heuristic, alt=alt, **options)
            result = run_search(graph, algorithm, start, goal, **kw)
            timings = []
            for _ in range(repeats):
                begin = time.perf_counter()
                result = run_search(graph, algorithm, start, goal, **kw)
                timings.append((time.perf_counter() - begin) * 1000)
            ratio = None
            if result.found and baseline.found:
                ratio = result.cost / baseline.cost if baseline.cost else 1.0
            results.append(dict(
                algorithm=algorithm, heuristic=result.heuristic, label=result.label,
                found=result.found, reason=result.reason, cost=result.cost, cost_ratio=ratio,
                expanded=result.expanded, median_ms=statistics.median(timings),
                path=result.path, edge_ids=result.edge_ids,
                relaxed=sum(len(s.pushed) for s in result.steps),
                discovered=len({s.node for s in result.steps} | {v for s in result.steps for v in s.pushed}),
                pruned=result.extra.get("pruned"), revisits=result.extra.get("revisits"),
            ))
    return results
