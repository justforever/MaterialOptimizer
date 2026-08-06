/* ============================================================
   Cutting-stock solver
   ------------------------------------------------------------
   所有長度在內部一律換算成整數「公釐」(×1000) 再運算，
   避免 3.802 + 0.003 這類浮點誤差造成「明明放得下卻判定放不下」。

   三段式策略：
     1. 件數 <= EXACT_THRESHOLD：列舉所有「極大裁切樣式」(maximal
        pattern) 做記憶化搜尋，回傳真正的最小成本解。
     2. 件數較多：貪婪法（每支料用 0/1 背包取最佳填充）先求初始解。
     3. 再跑成對重組的區域搜尋，把貪婪法短視造成的錯誤修掉。

   目標函數：總成本（未啟用單價時 price 一律為 1，等同最小化總用料
   長度，也就等同最小化廢料）；成本相同時取支數較少者。
   ============================================================ */
var CuttingStock = (function () {
  'use strict';

  var SCALE = 1000;                // 1 單位 = 1/1000 m
  var EXACT_THRESHOLD = 16;        // 件數 <= 此值走精確解
  var EXACT_NODE_BUDGET = 200000;  // 精確解的搜尋節點上限，超過則退回啟發式
  var PATTERN_LIMIT = 20000;       // 單一狀態下列舉的裁切樣式上限
  var REPACK_MAX_ITEMS = 20;       // 成對重組時的件數上限（2^n 列舉）
  var LOCAL_SEARCH_MS = 2000;      // 區域搜尋時間預算

  function toInt(v) { return Math.round(v * SCALE); }
  function toFloat(v) { return v / SCALE; }

  /** 把可能是空字串 / NaN / 負數的輸入收斂成合理數值 */
  function safeNum(raw, fallback, min) {
    var n = typeof raw === 'number' ? raw : parseFloat(raw);
    if (!isFinite(n)) return fallback;
    if (typeof min === 'number' && n < min) return fallback;
    return n;
  }

  // ---- 0/1 背包：在 capacity 內取得最大填充長度，且必定包含 items[0] ----
  // items 需為 {value, idx} 且已由大到小排序，回傳 idx 陣列。
  function bestFill(items, capacity, kerf) {
    var base = items[0].value;
    if (base > capacity) return null;
    var capLeft = capacity - base;
    var rest = items.slice(1);
    var n = rest.length;
    var width = capLeft + 1;

    var dp = new Int32Array(width);
    var take = new Uint8Array(n * width);   // take[i*width + c]=1 → 容量 c 時選了第 i 件

    for (var i = 0; i < n; i++) {
      var w = rest[i].value + kerf;         // 每多放一件就多一道鋸路
      if (w > capLeft) continue;
      var row = i * width;
      for (var c = capLeft; c >= w; c--) {
        var cand = dp[c - w] + w;
        if (cand > dp[c]) { dp[c] = cand; take[row + c] = 1; }
      }
    }

    var used = [items[0].idx];
    var pos = capLeft;
    for (var j = n - 1; j >= 0; j--) {
      if (take[j * width + pos]) {
        used.push(rest[j].idx);
        pos -= rest[j].value + kerf;
      }
    }
    return used;
  }

  // ---- 列舉「極大裁切樣式」----
  // 必含最大件 rem[0]（對稱性剪枝：最大件一定落在某支料，先處理它）。
  // 只保留「再也塞不下任何一件」的樣式：若 P 放得下且 P∪{x} 也放得下，
  // 改用 P∪{x} 絕不會比較差，所以非極大的樣式可以直接捨棄。
  function maximalPatterns(rem, capacity, kerf) {
    var n = rem.length;
    var out = [];
    var chosen = [0];
    var overflow = false;

    function dfs(i, used, smallestExcluded) {
      if (out.length >= PATTERN_LIMIT) { overflow = true; return; }
      if (i === n) {
        // 極大性檢查：rem 由大到小，最小的未選件也塞不下才算數
        if (smallestExcluded === -1 || used + rem[smallestExcluded] + kerf > capacity) {
          out.push(chosen.slice());
        }
        return;
      }
      var w = rem[i] + kerf;
      // 等值重複件只允許「由前往後連續選取」：前一件同值卻沒選，就不准選這件。
      // 否則 [3,3,3] 會列舉出 {第1件} 與 {第2件} 這種內容完全相同的重複樣式。
      var prevSameValueSkipped = i > 0 && rem[i] === rem[i - 1] &&
        !(chosen.length > 0 && chosen[chosen.length - 1] === i - 1);
      if (!prevSameValueSkipped && used + w <= capacity) {
        chosen.push(i);
        dfs(i + 1, used + w, smallestExcluded);
        chosen.pop();
      }
      dfs(i + 1, used, i);
    }

    dfs(1, rem[0], -1);
    if (out.length === 0 && overflow) return null;
    return out;
  }

  function BudgetExceeded() {}

  function makeBin(spec, itemValues) {
    return { size: spec.size, isFixed: spec.isFixed, cost: spec.cost, items: itemValues };
  }

  // ---- 精確解：記憶化搜尋所有極大樣式的組合 ----
  function exactSolve(items, specs, kerf) {
    var memo = new Map();
    var nodes = 0;

    function solveRec(rem) {
      if (rem.length === 0) return { cost: 0, count: 0, bins: [] };
      var k = rem.join(',');
      var hit = memo.get(k);
      if (hit) return hit;
      if (++nodes > EXACT_NODE_BUDGET) throw new BudgetExceeded();

      var best = null;
      for (var s = 0; s < specs.length; s++) {
        var spec = specs[s];
        if (spec.size < rem[0]) continue;
        // 剪枝：這支料本身就已經比目前最佳的「整體」成本貴，不可能更好
        if (best && spec.cost > best.cost) continue;

        var patterns = maximalPatterns(rem, spec.size, kerf);
        if (!patterns) throw new BudgetExceeded();

        for (var p = 0; p < patterns.length; p++) {
          var idxs = patterns[p];
          var usedSet = new Set(idxs);
          var next = [];
          for (var i = 0; i < rem.length; i++) if (!usedSet.has(i)) next.push(rem[i]);

          var sub = solveRec(next);
          if (sub.cost === Infinity) continue;
          var cost = spec.cost + sub.cost;
          var count = 1 + sub.count;
          if (!best || cost < best.cost || (cost === best.cost && count < best.count)) {
            best = {
              cost: cost,
              count: count,
              bins: [makeBin(spec, idxs.map(function (i) { return rem[i]; }))].concat(sub.bins)
            };
          }
        }
      }
      if (!best) best = { cost: Infinity, count: 0, bins: [] };
      memo.set(k, best);
      return best;
    }

    return solveRec(items.slice().sort(function (a, b) { return b - a; }));
  }

  // ---- 貪婪法：每次挑一支「最划算」的料，用背包取最佳填充 ----
  function greedySolve(items, specs, kerf, isCostEnabled) {
    var rem = items.slice().sort(function (a, b) { return b - a; });
    var bins = [];
    while (rem.length > 0) {
      var objs = rem.map(function (v, idx) { return { value: v, idx: idx }; });
      var bestOption = null, minScore = Infinity;
      for (var s = 0; s < specs.length; s++) {
        var spec = specs[s];
        if (spec.size < rem[0]) continue;
        var idxs = bestFill(objs, spec.size, kerf);
        if (!idxs) continue;
        var filled = 0;
        for (var i = 0; i < idxs.length; i++) filled += rem[idxs[i]];
        // 有成本：每公尺實裝的花費；無成本：純廢料長度
        var score = isCostEnabled ? spec.cost / filled : spec.size - filled;
        if (score < minScore) { minScore = score; bestOption = { spec: spec, idxs: idxs }; }
      }
      if (!bestOption) break;   // 連最大規格都放不下 rem[0]
      bins.push(makeBin(bestOption.spec, bestOption.idxs.map(function (i) { return rem[i]; })));
      bestOption.idxs.slice().sort(function (a, b) { return b - a; })
        .forEach(function (i) { rem.splice(i, 1); });
    }
    return bins;
  }

  // ---- 成對重組的區域搜尋 ----
  function fitsInSpec(values, spec, kerf) {
    var sum = 0;
    for (var i = 0; i < values.length; i++) sum += values[i];
    return sum + Math.max(0, values.length - 1) * kerf <= spec.size;
  }

  function cheapestSpecFor(values, specs, kerf) {
    if (values.length === 0) return null;
    var best = null;
    for (var i = 0; i < specs.length; i++) {
      if (fitsInSpec(values, specs[i], kerf) && (!best || specs[i].cost < best.cost)) best = specs[i];
    }
    return best;
  }

  function bestRepack(values, specs, kerf) {
    var n = values.length;
    if (n > REPACK_MAX_ITEMS) return null;
    var best = null;
    var total = 1 << n;
    // 固定第 0 件在 A 組，避開 A/B 互換的鏡像組合（列舉量直接減半）
    for (var mask = 1; mask < total; mask += 2) {
      var A = [], B = [];
      for (var i = 0; i < n; i++) { if ((mask >> i) & 1) A.push(values[i]); else B.push(values[i]); }
      var specA = cheapestSpecFor(A, specs, kerf);
      if (!specA) continue;
      var cost = specA.cost;
      if (best && cost >= best.cost) continue;
      var bins = [makeBin(specA, A)];
      if (B.length > 0) {
        var specB = cheapestSpecFor(B, specs, kerf);
        if (!specB) continue;
        cost += specB.cost;
        bins.push(makeBin(specB, B));
      }
      if (!best || cost < best.cost || (cost === best.cost && bins.length < best.bins.length)) {
        best = { cost: cost, bins: bins };
      }
    }
    return best;
  }

  function localSearchImprove(bins, specs, kerf, deadline) {
    var improved = true;
    while (improved) {
      improved = false;
      for (var i = 0; i < bins.length && !improved; i++) {
        for (var j = i + 1; j < bins.length && !improved; j++) {
          if (Date.now() > deadline) return bins;   // 時間預算，避免凍結瀏覽器
          var combined = bins[i].items.concat(bins[j].items);
          if (combined.length > REPACK_MAX_ITEMS) continue;
          var currentCost = bins[i].cost + bins[j].cost;
          var cand = bestRepack(combined, specs, kerf);
          if (!cand) continue;
          if (cand.cost < currentCost || (cand.cost === currentCost && cand.bins.length < 2)) {
            var next = bins.slice();
            next.splice(j, 1);
            next.splice.apply(next, [i, 1].concat(cand.bins));
            bins = next;
            improved = true;
          }
        }
      }
    }
    return bins;
  }

  /**
   * @param {number[]} demands 需求長度 (m)
   * @param {{size:number, price:number, isFixed:boolean}[]} specs 原材規格
   * @param {number} kerf 鋸路 (m)
   * @param {boolean} isCostEnabled 是否以成本為目標（否則以最省料為目標）
   * @returns {{bins:Array, unfittable:number[], exact:boolean}}
   */
  function solve(demands, specs, kerf, isCostEnabled) {
    var kerfInt = toInt(safeNum(kerf, 0, 0));
    var specsInt = specs
      .filter(function (s) { return safeNum(s.size, 0, 0) > 0; })
      .map(function (s) {
        var size = toInt(s.size);
        var price = isCostEnabled ? safeNum(s.price, 1, 0) : 1;
        return { size: size, price: price, isFixed: !!s.isFixed, cost: size * price };
      });

    if (specsInt.length === 0 || demands.length === 0) {
      return { bins: [], unfittable: demands.slice(), exact: true };
    }

    var maxSize = specsInt.reduce(function (m, s) { return Math.max(m, s.size); }, 0);
    var items = [], unfittable = [];
    demands.forEach(function (d) {
      var v = toInt(d);
      if (v <= 0) return;
      if (v > maxSize) unfittable.push(d); else items.push(v);
    });

    if (items.length === 0) return { bins: [], unfittable: unfittable, exact: true };

    var bins = null, exact = false;
    if (items.length <= EXACT_THRESHOLD) {
      try {
        var r = exactSolve(items, specsInt, kerfInt);
        // Infinity 代表搜不到可行解（理論上不該發生，超長件已先濾掉）
        if (r.cost !== Infinity) { bins = r.bins; exact = true; }
      } catch (e) {
        if (!(e instanceof BudgetExceeded)) throw e;
        bins = null;   // 預算用盡，退回啟發式
      }
    }
    if (!bins) {
      bins = greedySolve(items, specsInt, kerfInt, isCostEnabled);
      bins = localSearchImprove(bins, specsInt, kerfInt, Date.now() + LOCAL_SEARCH_MS);
    }

    var packed = bins.reduce(function (n, b) { return n + b.items.length; }, 0);
    if (packed < items.length) {
      // 理論上不會發生；真的發生就明確回報而不是默默吞掉需求
      throw new Error('內部錯誤：有 ' + (items.length - packed) + ' 件需求未被排入任何原材');
    }

    var out = bins.map(function (b) {
      var used = b.items.reduce(function (a, c) { return a + c; }, 0);
      var kerfTotal = Math.max(0, b.items.length - 1) * kerfInt;
      return {
        size: toFloat(b.size),
        isFixed: b.isFixed,
        cost: b.cost / SCALE,
        items: b.items.map(toFloat),
        kerfTotal: toFloat(kerfTotal),
        waste: toFloat(b.size - used - kerfTotal)
      };
    });
    return { bins: out, unfittable: unfittable, exact: exact };
  }

  return {
    solve: solve,
    safeNum: safeNum,
    _internal: { exactSolve: exactSolve, greedySolve: greedySolve, localSearchImprove: localSearchImprove, bestFill: bestFill, toInt: toInt }
  };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = CuttingStock;

// Node.js（測試用）；瀏覽器直接使用全域的 CuttingStock
if (typeof module !== 'undefined' && module.exports) module.exports = CuttingStock;
