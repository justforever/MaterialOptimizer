#!/usr/bin/env python3
"""一維裁切最佳化 (1D cutting stock) CLI。

與 index.html 中的 JS solver 使用同一套演算法，方便批次驗證：

  1. 所有長度換算成整數「公釐」(x1000) 再運算，避免浮點誤差。
  2. 件數 <= EXACT_THRESHOLD：列舉所有「極大裁切樣式」做記憶化搜尋，
     得到真正的最小成本解。
  3. 件數較多：貪婪法（每支料以 0/1 背包取最佳填充）求初始解，
     再以成對重組的區域搜尋改善。

目標函數為總成本；未指定單價時所有單價視為 1，等同最小化總用料長度，
也就等同最小化廢料。成本相同時取支數較少者。

範例：
    python execute.py --demands "1.1 1.1 2.3 4.22 5.95" --stocks 10,12
    python execute.py --demands demands.txt --stocks 10,12 --var-stocks 9,11,13,14 \
                      --price 100 --var-price 150 --kerf 0.003
    python execute.py --self-test
"""

from __future__ import annotations

import argparse
import itertools
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Iterable, Sequence

SCALE = 1000            # 1 單位 = 1/1000 m
EXACT_THRESHOLD = 16    # 件數 <= 此值走精確解
EXACT_NODE_BUDGET = 200_000
PATTERN_LIMIT = 20_000
REPACK_MAX_ITEMS = 20
LOCAL_SEARCH_SECONDS = 5.0


class BudgetExceeded(Exception):
    """精確搜尋超出節點/樣式預算，需退回啟發式。"""


@dataclass(frozen=True)
class Spec:
    """一種原材規格。size/cost 皆為整數（公釐、公釐*單價）。"""

    size: int
    price: float
    is_fixed: bool

    @property
    def cost(self) -> float:
        return self.size * self.price


@dataclass
class Bin:
    """一支已排定裁切內容的原材。"""

    spec: Spec
    items: list[int] = field(default_factory=list)

    @property
    def used(self) -> int:
        return sum(self.items)

    @property
    def kerf_total_for(self) -> int:
        return max(0, len(self.items) - 1)

    def waste(self, kerf: int) -> int:
        return self.spec.size - self.used - self.kerf_total_for * kerf


@dataclass
class Solution:
    bins: list[Bin]
    unfittable: list[float]
    exact: bool

    @property
    def cost(self) -> float:
        return sum(b.spec.cost for b in self.bins)


# --------------------------------------------------------------------------
# 核心演算法
# --------------------------------------------------------------------------

def best_fill(items: Sequence[int], capacity: int, kerf: int) -> list[int] | None:
    """0/1 背包：在 capacity 內取最大填充長度，且必定包含 items[0]。

    items 需由大到小排序，回傳被選中的 index 清單。
    """
    if not items or items[0] > capacity:
        return None
    cap_left = capacity - items[0]
    rest = items[1:]
    width = cap_left + 1

    dp = [0] * width
    take = [bytearray(width) for _ in rest]

    for i, value in enumerate(rest):
        w = value + kerf          # 每多放一件就多一道鋸路
        if w > cap_left:
            continue
        row = take[i]
        for c in range(cap_left, w - 1, -1):
            cand = dp[c - w] + w
            if cand > dp[c]:
                dp[c] = cand
                row[c] = 1

    used = [0]
    pos = cap_left
    for j in range(len(rest) - 1, -1, -1):
        if take[j][pos]:
            used.append(j + 1)
            pos -= rest[j] + kerf
    return used


def maximal_patterns(rem: Sequence[int], capacity: int, kerf: int) -> list[tuple[int, ...]]:
    """列舉必含最大件 rem[0] 的所有「極大」裁切樣式。

    只保留再也塞不下任何一件的樣式：若 P 放得下且 P∪{x} 也放得下，
    改用 P∪{x} 絕不會比較差，因此非極大的樣式可直接捨棄。

    Raises:
        BudgetExceeded: 樣式數超過 PATTERN_LIMIT。
    """
    n = len(rem)
    out: list[tuple[int, ...]] = []
    chosen: list[int] = [0]

    def dfs(i: int, used: int, smallest_excluded: int) -> None:
        if len(out) >= PATTERN_LIMIT:
            raise BudgetExceeded
        if i == n:
            # rem 由大到小，最小的未選件也塞不下才算極大
            if smallest_excluded < 0 or used + rem[smallest_excluded] + kerf > capacity:
                out.append(tuple(chosen))
            return
        w = rem[i] + kerf
        # 等值重複件只允許「由前往後連續選取」，避免列舉出內容相同的重複樣式
        prev_same_skipped = i > 0 and rem[i] == rem[i - 1] and chosen[-1] != i - 1
        if not prev_same_skipped and used + w <= capacity:
            chosen.append(i)
            dfs(i + 1, used + w, smallest_excluded)
            chosen.pop()
        dfs(i + 1, used, i)

    dfs(1, rem[0], -1)
    return out


def exact_solve(items: Sequence[int], specs: Sequence[Spec], kerf: int) -> tuple[float, list[Bin]]:
    """記憶化搜尋所有極大樣式的組合，回傳 (最小成本, bins)。

    Raises:
        BudgetExceeded: 搜尋節點超過 EXACT_NODE_BUDGET。
    """
    memo: dict[tuple[int, ...], tuple[float, int, list[Bin]]] = {}
    nodes = 0

    def solve_rec(rem: tuple[int, ...]) -> tuple[float, int, list[Bin]]:
        nonlocal nodes
        if not rem:
            return 0.0, 0, []
        cached = memo.get(rem)
        if cached is not None:
            return cached
        nodes += 1
        if nodes > EXACT_NODE_BUDGET:
            raise BudgetExceeded

        best: tuple[float, int, list[Bin]] | None = None
        for spec in specs:
            if spec.size < rem[0]:
                continue
            # 剪枝：這支料本身已比目前最佳的「整體」成本貴，不可能更好
            if best is not None and spec.cost > best[0]:
                continue
            for idxs in maximal_patterns(rem, spec.size, kerf):
                used = set(idxs)
                nxt = tuple(v for i, v in enumerate(rem) if i not in used)
                sub_cost, sub_count, sub_bins = solve_rec(nxt)
                if sub_cost == float("inf"):
                    continue
                cost = spec.cost + sub_cost
                count = 1 + sub_count
                if best is None or cost < best[0] or (cost == best[0] and count < best[1]):
                    best = (cost, count, [Bin(spec, [rem[i] for i in idxs])] + sub_bins)

        result = best if best is not None else (float("inf"), 0, [])
        memo[rem] = result
        return result

    cost, _count, bins = solve_rec(tuple(sorted(items, reverse=True)))
    return cost, bins


def greedy_solve(items: Sequence[int], specs: Sequence[Spec], kerf: int, cost_mode: bool) -> list[Bin]:
    """每次挑一支最划算的料，用背包取最佳填充。"""
    rem = sorted(items, reverse=True)
    bins: list[Bin] = []
    while rem:
        best_option = None
        min_score = float("inf")
        for spec in specs:
            if spec.size < rem[0]:
                continue
            idxs = best_fill(rem, spec.size, kerf)
            if idxs is None:
                continue
            filled = sum(rem[i] for i in idxs)
            # 有成本：每公尺實裝的花費；無成本：純廢料長度
            score = spec.cost / filled if cost_mode else spec.size - filled
            if score < min_score:
                min_score = score
                best_option = (spec, idxs)
        if best_option is None:
            break       # 連最大規格都放不下 rem[0]
        spec, idxs = best_option
        bins.append(Bin(spec, [rem[i] for i in idxs]))
        for i in sorted(idxs, reverse=True):
            rem.pop(i)
    return bins


def _fits(values: Sequence[int], spec: Spec, kerf: int) -> bool:
    return sum(values) + max(0, len(values) - 1) * kerf <= spec.size


def cheapest_spec_for(values: Sequence[int], specs: Sequence[Spec], kerf: int) -> Spec | None:
    candidates = [s for s in specs if _fits(values, s, kerf)]
    return min(candidates, key=lambda s: s.cost) if candidates else None


def best_repack(values: Sequence[int], specs: Sequence[Spec], kerf: int) -> tuple[float, list[Bin]] | None:
    """把一組件重新拆成 1~2 支料的最便宜方式。"""
    n = len(values)
    if n > REPACK_MAX_ITEMS:
        return None
    best: tuple[float, list[Bin]] | None = None
    # 固定第 0 件在 A 組，避開 A/B 互換的鏡像組合
    for mask in range(1, 1 << n, 2):
        a = [values[i] for i in range(n) if mask >> i & 1]
        b = [values[i] for i in range(n) if not mask >> i & 1]
        spec_a = cheapest_spec_for(a, specs, kerf)
        if spec_a is None:
            continue
        cost = spec_a.cost
        if best is not None and cost >= best[0]:
            continue
        bins = [Bin(spec_a, a)]
        if b:
            spec_b = cheapest_spec_for(b, specs, kerf)
            if spec_b is None:
                continue
            cost += spec_b.cost
            bins.append(Bin(spec_b, b))
        if best is None or cost < best[0] or (cost == best[0] and len(bins) < len(best[1])):
            best = (cost, bins)
    return best


def local_search_improve(bins: list[Bin], specs: Sequence[Spec], kerf: int, deadline: float) -> list[Bin]:
    """成對重組：試著把任兩支料重新拆分成更便宜的組合。"""
    improved = True
    while improved:
        improved = False
        for i in range(len(bins)):
            if improved:
                break
            for j in range(i + 1, len(bins)):
                if time.monotonic() > deadline:
                    return bins
                combined = bins[i].items + bins[j].items
                if len(combined) > REPACK_MAX_ITEMS:
                    continue
                current = bins[i].spec.cost + bins[j].spec.cost
                cand = best_repack(combined, specs, kerf)
                if cand is None:
                    continue
                cost, new_bins = cand
                if cost < current or (cost == current and len(new_bins) < 2):
                    bins = bins[:i] + new_bins + bins[i + 1:j] + bins[j + 1:]
                    improved = True
                    break
    return bins


def solve(
    demands: Iterable[float],
    specs: Sequence[Spec],
    kerf: float = 0.0,
    cost_mode: bool = False,
) -> Solution:
    """主入口。demands/kerf 以公尺為單位；不會就地修改傳入的容器。"""
    kerf_int = round(max(0.0, kerf) * SCALE)
    specs = [s for s in specs if s.size > 0]
    demands = list(demands)
    if not specs or not demands:
        return Solution([], list(demands), True)

    max_size = max(s.size for s in specs)
    items: list[int] = []
    unfittable: list[float] = []
    for d in demands:
        v = round(d * SCALE)
        if v <= 0:
            continue
        (unfittable.append(d) if v > max_size else items.append(v))

    if not items:
        return Solution([], unfittable, True)

    bins: list[Bin] | None = None
    exact = False
    if len(items) <= EXACT_THRESHOLD:
        try:
            cost, candidate = exact_solve(items, specs, kerf_int)
            if cost != float("inf"):
                bins, exact = candidate, True
        except (BudgetExceeded, RecursionError):
            bins = None
    if bins is None:
        bins = greedy_solve(items, specs, kerf_int, cost_mode)
        bins = local_search_improve(bins, specs, kerf_int, time.monotonic() + LOCAL_SEARCH_SECONDS)

    packed = sum(len(b.items) for b in bins)
    if packed < len(items):
        raise RuntimeError(f"內部錯誤：有 {len(items) - packed} 件需求未被排入任何原材")

    return Solution(bins, unfittable, exact)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_number_list(raw: str) -> list[float]:
    """接受以逗號、空白或換行分隔的數字；忽略無法辨識的字詞。"""
    values = []
    for token in re.split(r"[,，\s]+", raw.strip()):
        if not token:
            continue
        try:
            n = float(token)
        except ValueError:
            print(f"警告：忽略無法辨識的數值 {token!r}", file=sys.stderr)
            continue
        if n > 0:
            values.append(n)
        else:
            print(f"警告：忽略非正數 {token!r}", file=sys.stderr)
    return values


def read_list_arg(value: str) -> list[float]:
    """引數可以是數字字串，也可以是含有數字的檔案路徑。"""
    if os.path.isfile(value):
        with open(value, encoding="utf-8") as fh:
            return parse_number_list(fh.read())
    return parse_number_list(value)


def report(solution: Solution, kerf: float, cost_mode: bool) -> None:
    kerf_int = round(max(0.0, kerf) * SCALE)
    if solution.unfittable:
        print(f"⚠ 超過最大規格、無法裁切：{', '.join(str(x) for x in solution.unfittable)}\n")

    print(f"{'#':>3}  {'規格(m)':>8}  {'類別':<6}  {'剩餘(m)':>8}  裁切組合")
    print("-" * 72)
    total_waste = 0
    total_stock = 0
    total_used = 0
    for idx, b in enumerate(solution.bins, 1):
        waste = b.waste(kerf_int)
        total_waste += waste
        total_stock += b.spec.size
        total_used += b.used
        kind = "定尺" if b.spec.is_fixed else "不定尺"
        combo = " + ".join(f"{v / SCALE:g}" for v in b.items)
        print(f"{idx:>3}  {b.spec.size / SCALE:>8g}  {kind:<6}  {waste / SCALE:>8.3f}  {combo}")

    print("-" * 72)
    util = (total_used / total_stock * 100) if total_stock else 0.0
    print(f"總使用支數 : {len(solution.bins)}")
    print(f"總用料長度 : {total_stock / SCALE:.3f} m")
    print(f"總廢料長度 : {total_waste / SCALE:.3f} m")
    print(f"材料利用率 : {util:.1f}%")
    if cost_mode:
        print(f"預估總成本 : ${solution.cost / SCALE:,.2f}")
    print(f"求解方式   : {'精確解（窮舉所有裁切組合）' if solution.exact else '啟發式近似解'}")

    counts: dict[tuple[float, str], int] = {}
    for b in solution.bins:
        counts[(b.spec.size / SCALE, "定尺" if b.spec.is_fixed else "不定尺")] = \
            counts.get((b.spec.size / SCALE, "定尺" if b.spec.is_fixed else "不定尺"), 0) + 1
    print("\n採購清單：")
    for (size, kind), n in sorted(counts.items()):
        print(f"  {size:g} m ({kind}) x {n}")


DEFAULT_DEMANDS = (
    "1.1 1.1 1.1 1.1 1.43 1.53 1.7 1.7 1.7 2.15 2.3 2.3 2.3 2.3 2.45 3.2 3.3 3.3 "
    "3.6 3.6 3.8 3.8 3.802 3.802 4.08 4.08 4.22 4.22 4.22 4.97 5.18 5.2 5.3 5.5 5.62 5.95 6.05"
)


def self_test() -> int:
    """對照暴力解驗證精確解，並檢查物料守恆與不超長。"""
    import random

    failures = 0

    def check(ok: bool, msg: str) -> None:
        nonlocal failures
        if not ok:
            failures += 1
            print(f"  FAIL: {msg}")

    def brute_force_cost(demands, specs, kerf_int):
        memo: dict[tuple[int, ...], float] = {}

        def cheapest(vals):
            s = cheapest_spec_for(vals, specs, kerf_int)
            return s.cost if s else None

        def f(rem):
            if not rem:
                return 0.0
            if rem in memo:
                return memo[rem]
            best = float("inf")
            rest = rem[1:]
            for r in range(len(rest) + 1):
                for combo in itertools.combinations(range(len(rest)), r):
                    chosen = set(combo)
                    grp = [rem[0]] + [rest[i] for i in combo]
                    c = cheapest(grp)
                    if c is None:
                        continue
                    out = tuple(v for i, v in enumerate(rest) if i not in chosen)
                    total = c + f(out)
                    best = min(best, total)
            memo[rem] = best
            return best

        return f(tuple(sorted(round(d * SCALE) for d in demands))[::-1])

    print("== 1. 已知反例（舊版得 30，最佳為 28）==")
    dem = [2.1, 8.6, 6.4, 6.4, 1.6, 1.6]
    specs = [Spec(s * SCALE, 1, True) for s in (8, 10, 12)]
    sol = solve(dem, specs, 0.0, False)
    opt = brute_force_cost(dem, specs, 0)
    print(f"  得 {sol.cost / SCALE:g}，最佳 {opt / SCALE:g}")
    check(sol.cost == opt, "應達最佳解")

    print("== 2. 隨機對照暴力解（60 組）==")
    random.seed(20260807)
    spec_sets = [(9, 10), (8, 10, 12), (6, 9, 10), (7, 11, 12)]
    worse = 0
    for t in range(60):
        sizes = spec_sets[t % len(spec_sets)]
        specs = [Spec(s * SCALE, 1, True) for s in sizes]
        kerf = [0.0, 0.003, 0.005][t % 3]
        kerf_int = round(kerf * SCALE)
        n = 4 + t % 4
        dem = [round(random.uniform(1, max(sizes)), 1) for _ in range(n)]
        sol = solve(dem, specs, kerf, False)
        if sol.unfittable:
            continue
        flat = sorted(v for b in sol.bins for v in b.items)
        check(flat == sorted(round(d * SCALE) for d in dem), f"#{t} 物料守恆")
        for b in sol.bins:
            check(b.used + b.kerf_total_for * kerf_int <= b.spec.size, f"#{t} 未超長")
        if sol.cost > brute_force_cost(dem, specs, kerf_int):
            worse += 1
    print(f"  次佳組數：{worse}")
    check(worse == 0, "精確解應等於暴力解")

    print("== 3. 髒輸入 ==")
    specs = [Spec(10 * SCALE, 1, True), Spec(12 * SCALE, 1, True)]
    for bad_kerf in (0.0, -1.0):
        s = solve([3, 3, 3, 3, 3], specs, bad_kerf, False)
        check(len(s.bins) <= 2, f"kerf={bad_kerf} 不應退化")
    s = solve([3, 3, 99], specs, 0.003, False)
    check(s.unfittable == [99] and len(s.bins) == 1, "超長件應列入 unfittable 且其餘照排")
    check(solve([], specs, 0, False).bins == [], "空需求")
    check(len(solve([3], [], 0, False).unfittable) == 1, "空規格")

    print("== 4. 浮點邊界（3.802 x2 + 0.003 鋸路恰好 7.607）==")
    s = solve([3.802, 3.802], [Spec(round(7.607 * SCALE), 1, True)], 0.003, False)
    check(len(s.bins) == 1, "剛好塞滿應為 1 支")

    print("== 5. 效能 ==")
    specs = [Spec(10 * SCALE, 1, True), Spec(12 * SCALE, 1, True)] + \
            [Spec(s * SCALE, 1, False) for s in (9, 11, 13, 14)]
    dem = parse_number_list(DEFAULT_DEMANDS)
    t0 = time.monotonic()
    s = solve(dem, specs, 0.003, False)
    elapsed = time.monotonic() - t0
    total = sum(b.spec.size for b in s.bins) / SCALE
    print(f"  37 件：{elapsed:.2f}s，{len(s.bins)} 支，總長 {total:g} m（需求 {sum(dem):.3f} m）")
    check(elapsed < 30, "37 件應在 30 秒內完成")
    check(sorted(v for b in s.bins for v in b.items) == sorted(round(d * SCALE) for d in dem), "物料守恆")

    print("\n✅ 全部通過" if failures == 0 else f"\n❌ {failures} 項失敗")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="一維裁切最佳化 (1D cutting stock)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--demands", default=DEFAULT_DEMANDS,
                        help="需求長度，以逗號/空白分隔，或給一個檔案路徑")
    parser.add_argument("--stocks", default="10,12", help="定尺規格 (m)")
    parser.add_argument("--var-stocks", default="", help="不定尺規格 (m)")
    parser.add_argument("--price", type=float, default=1.0, help="定尺每米單價")
    parser.add_argument("--var-price", type=float, default=1.0, help="不定尺每米單價")
    parser.add_argument("--kerf", type=float, default=0.0, help="鋸路 / 切割損耗 (m)")
    parser.add_argument("--cost-mode", action="store_true",
                        help="以總成本為目標（預設以最省料為目標）")
    parser.add_argument("--self-test", action="store_true", help="執行內建驗證")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    demands = read_list_arg(args.demands)
    if not demands:
        parser.error("找不到任何有效的需求長度")

    specs = [Spec(round(s * SCALE), args.price if args.cost_mode else 1.0, True)
             for s in read_list_arg(args.stocks)]
    specs += [Spec(round(s * SCALE), args.var_price if args.cost_mode else 1.0, False)
              for s in read_list_arg(args.var_stocks)]
    if not specs:
        parser.error("請至少提供一種原材規格")
    if args.kerf < 0:
        parser.error("鋸路不可為負數")

    report(solve(demands, specs, args.kerf, args.cost_mode), args.kerf, args.cost_mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
