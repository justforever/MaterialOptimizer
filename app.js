/* ============================================================
   UI 綁定層：輸入解析、驗證、結果渲染。
   演算法在 solver.js（全域 CuttingStock）。
   ============================================================ */
function toggleCostInputs() {
    var isEnabled = document.getElementById('enableCost').checked;
    document.querySelectorAll('.price-input').forEach(function (input) {
        input.disabled = !isEnabled;
    });
}

/** 解析數字列表，同時回報無法辨識的字詞 */
function parseNumberList(raw) {
    var values = [], invalid = [];
    raw.split(/[,，\s]+/).forEach(function (token) {
        if (!token) return;
        var n = Number(token);
        if (!isFinite(n) || n <= 0) invalid.push(token); else values.push(n);
    });
    return { values: values, invalid: invalid };
}

function showMessages(list) {
    var box = document.getElementById('messages');
    box.innerHTML = '';
    list.forEach(function (m) {
        var div = document.createElement('div');
        div.className = 'message ' + m.type;
        div.textContent = m.text;   // textContent：不讓使用者輸入變成 HTML
        box.appendChild(div);
    });
}

function calculate() {
    var btn = document.getElementById('runBtn');
    var messages = [];

    var demandsIn = parseNumberList(document.getElementById('demandsInput').value);
    var fixedIn = parseNumberList(document.getElementById('fixedStocks').value);
    var varIn = parseNumberList(document.getElementById('varStocks').value);

    [['需求長度', demandsIn], ['定尺規格', fixedIn], ['不定尺規格', varIn]].forEach(function (pair) {
        if (pair[1].invalid.length) {
            messages.push({ type: 'warn', text: pair[0] + '中有無法辨識或非正數的項目已被忽略：' + pair[1].invalid.join(', ') });
        }
    });

    if (demandsIn.values.length === 0) {
        messages.push({ type: 'error', text: '請至少輸入一個有效的需求長度。' });
    }
    if (fixedIn.values.length === 0 && varIn.values.length === 0) {
        messages.push({ type: 'error', text: '請至少輸入一種原材規格。' });
    }
    if (messages.some(function (m) { return m.type === 'error'; })) {
        showMessages(messages);
        document.getElementById('resultsArea').style.display = 'none';
        return;
    }

    var isCostEnabled = document.getElementById('enableCost').checked;
    var pFixed = CuttingStock.safeNum(document.getElementById('fixedPrice').value, 1, 0);
    var pVar = CuttingStock.safeNum(document.getElementById('varPrice').value, 1, 0);
    var kerfRaw = document.getElementById('kerfInput').value;
    var kerf = CuttingStock.safeNum(kerfRaw, 0, 0);
    if (String(kerfRaw).trim() !== '' && kerf !== Number(kerfRaw)) {
        messages.push({ type: 'warn', text: '鋸路輸入無效，已以 0 計算。' });
    }

    var allSpecs = fixedIn.values.map(function (s) { return { size: s, price: pFixed, isFixed: true }; })
        .concat(varIn.values.map(function (s) { return { size: s, price: pVar, isFixed: false }; }));

    btn.disabled = true;
    btn.textContent = '運算中…';
    // 讓瀏覽器先把 disabled 狀態畫出來，再進行同步運算
    setTimeout(function () {
        try {
            var result = CuttingStock.solve(demandsIn.values, allSpecs, kerf, isCostEnabled);

            if (result.unfittable.length) {
                messages.push({
                    type: 'error',
                    text: '以下長度超過最大規格，無法裁切（其餘需求仍已排程）：' + result.unfittable.join(', ')
                });
            }
            messages.push({
                type: 'info',
                text: result.exact
                    ? '已取得最佳解（窮舉所有裁切組合）。'
                    : '需求件數較多，採用啟發式演算法求近似最佳解。'
            });
            showMessages(messages);

            if (result.bins.length === 0) {
                document.getElementById('resultsArea').style.display = 'none';
                return;
            }
            render(result, isCostEnabled, kerf);
        } catch (err) {
            showMessages(messages.concat([{ type: 'error', text: '運算失敗：' + err.message }]));
            document.getElementById('resultsArea').style.display = 'none';
        } finally {
            btn.disabled = false;
            btn.textContent = '執行最佳化運算';
        }
    }, 0);
}

function fmt(n, digits) {
    return Number(n.toFixed(digits === undefined ? 3 : digits));
}

function render(result, isCostEnabled, kerf) {
    var bins = result.bins;
    document.getElementById('resultsArea').style.display = 'block';

    var totalCost = bins.reduce(function (a, b) { return a + b.cost; }, 0);
    var totalStockLen = bins.reduce(function (a, b) { return a + b.size; }, 0);
    var totalDemandLen = bins.reduce(function (a, b) {
        return a + b.items.reduce(function (x, y) { return x + y; }, 0);
    }, 0);
    var totalWaste = bins.reduce(function (a, b) { return a + b.waste; }, 0);

    document.getElementById('statCost').textContent = isCostEnabled
        ? '$' + totalCost.toLocaleString(undefined, { maximumFractionDigits: 2 })
        : 'N/A';
    document.getElementById('statUtil').textContent = totalStockLen > 0
        ? (totalDemandLen / totalStockLen * 100).toFixed(1) + '%'
        : '—';
    document.getElementById('statWaste').textContent = fmt(totalWaste) + ' m';
    document.getElementById('statCount').textContent = bins.length + ' 支';

    var list = document.getElementById('binsList');
    list.innerHTML = '';
    var colors = ['#0284c7', '#0d9488', '#4f46e5', '#7c3aed', '#2563eb'];

    bins.forEach(function (bin, idx) {
        var div = document.createElement('div');
        div.className = 'bin-item';

        var head = document.createElement('div');
        head.className = 'bin-head';
        var title = document.createElement('strong');
        title.textContent = '#' + (idx + 1) + ' 規格 ' + fmt(bin.size) + 'm';
        var tag = document.createElement('span');
        tag.className = 'tag';
        tag.style.background = bin.isFixed ? '#0369a1' : '#db2777';
        tag.textContent = bin.isFixed ? '定尺' : '不定尺';
        head.appendChild(title);
        head.appendChild(tag);
        div.appendChild(head);

        var bar = document.createElement('div');
        bar.className = 'progress-bar';
        bin.items.forEach(function (it, i) {
            if (i > 0) {
                var k = document.createElement('div');
                k.className = 'segment kerf';
                k.title = '鋸路 ' + fmt(kerf) + 'm';
                bar.appendChild(k);
            }
            var seg = document.createElement('div');
            seg.className = 'segment';
            seg.style.flex = '0 0 ' + (it / bin.size * 100) + '%';
            seg.style.background = colors[i % colors.length];
            seg.textContent = fmt(it);
            seg.title = fmt(it) + 'm';
            bar.appendChild(seg);
        });
        if (bin.waste > 0) {
            var w = document.createElement('div');
            w.className = 'segment waste';
            w.style.flex = '1 1 auto';
            w.textContent = fmt(bin.waste) + 'm';
            w.title = '剩餘 ' + fmt(bin.waste) + 'm';
            bar.appendChild(w);
        }
        div.appendChild(bar);

        var meta = document.createElement('div');
        meta.className = 'bin-meta';
        meta.textContent = bin.items.length + ' 件　鋸路合計 ' + fmt(bin.kerfTotal) +
            'm　剩餘 ' + fmt(bin.waste) + 'm' + (isCostEnabled ? '　成本 $' + fmt(bin.cost, 2) : '');
        div.appendChild(meta);

        list.appendChild(div);
    });

    var stats = {};
    bins.forEach(function (bin) {
        var key = fmt(bin.size) + 'm|' + (bin.isFixed ? '定尺' : '不定尺');
        stats[key] = (stats[key] || 0) + 1;
    });

    var summaryBody = document.getElementById('summaryBody');
    summaryBody.innerHTML = '';
    Object.keys(stats).sort(function (a, b) { return parseFloat(a) - parseFloat(b); }).forEach(function (key) {
        var row = summaryBody.insertRow();
        var parts = key.split('|');
        row.insertCell(0).textContent = parts[0];
        row.insertCell(1).textContent = parts[1];
        var cell = row.insertCell(2);
        var strong = document.createElement('strong');
        strong.textContent = stats[key];
        cell.appendChild(strong);
    });
}
