/* AI 답변 렌더링 — ```chart JSON``` 블록은 Chart.js 그래프, 마크다운 표·굵게·제목은 HTML로. */
(function (global) {
  'use strict';

  var PALETTE = [
    '#1f6fd1', '#f28c28', '#2ca25f', '#d6404e', '#8e6bc4',
    '#17a2b8', '#c9a227', '#e377c2', '#6c757d', '#7cb342',
  ];
  var charts = [];

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function inline(s) {
    return escapeHtml(s)
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>');
  }

  function splitRow(line) {
    var t = line.trim();
    if (t.charAt(0) === '|') t = t.slice(1);
    if (t.charAt(t.length - 1) === '|') t = t.slice(0, -1);
    return t.split('|').map(function (c) { return c.trim(); });
  }

  function isSepRow(line) {
    return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
  }

  function renderTable(lines) {
    var head = splitRow(lines[0]);
    var body = lines.slice(isSepRow(lines[1] || '') ? 2 : 1);
    var html = '<div class="ai-md-table-wrap"><table class="ai-md-table"><thead><tr>';
    head.forEach(function (h) { html += '<th>' + inline(h) + '</th>'; });
    html += '</tr></thead><tbody>';
    body.forEach(function (ln) {
      html += '<tr>';
      splitRow(ln).forEach(function (c) { html += '<td>' + inline(c) + '</td>'; });
      html += '</tr>';
    });
    html += '</tbody></table></div>';
    var div = document.createElement('div');
    div.innerHTML = html;
    return div.firstChild;
  }

  function renderText(text) {
    var frag = document.createDocumentFragment();
    var lines = text.split('\n');
    var buf = [];
    function flush() {
      var s = buf.join('\n').replace(/^\n+|\n+$/g, '');
      buf = [];
      if (!s) return;
      var div = document.createElement('div');
      div.className = 'ai-md-text';
      div.innerHTML = s
        .split('\n')
        .map(function (ln) {
          var h = ln.match(/^\s*#{1,6}\s+(.*)$/);
          return h ? '<span class="ai-md-h">' + inline(h[1]) + '</span>' : inline(ln);
        })
        .join('\n');
      frag.appendChild(div);
    }
    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i];
      if (/^\s*\|.*\|\s*$/.test(ln) && i + 1 < lines.length && isSepRow(lines[i + 1])) {
        flush();
        var tbl = [ln];
        i++;
        while (i < lines.length && /^\s*\|/.test(lines[i])) { tbl.push(lines[i]); i++; }
        i--;
        frag.appendChild(renderTable(tbl));
      } else {
        buf.push(ln);
      }
    }
    flush();
    return frag;
  }

  function parseSpec(raw) {
    var s = String(raw || '').trim();
    try { return JSON.parse(s); } catch (_e) { /* 아래에서 재시도 */ }
    try { return JSON.parse(s.replace(/,\s*([\]}])/g, '$1')); } catch (_e2) { return null; }
  }

  function toNum(v) {
    if (v === null || v === undefined || v === '') return null;
    if (typeof v === 'number') return v;
    var n = Number(String(v).replace(/,/g, ''));
    return isNaN(n) ? null : n;
  }

  function buildConfig(spec) {
    var type = String(spec.type || 'bar').toLowerCase();
    var horizontal = !!spec.horizontal || type === 'horizontalbar';
    if (type === 'horizontalbar') type = 'bar';
    if (type === 'area') { type = 'line'; spec.fill = true; }
    var round = type === 'pie' || type === 'doughnut' || type === 'polararea';
    var labels = (spec.labels || []).map(String);
    var hasRight = false;
    var datasets = (spec.datasets || []).map(function (ds, i) {
      var color = ds.color || PALETTE[i % PALETTE.length];
      var data = (ds.data || []).map(function (v) {
        if (type === 'scatter' && v && typeof v === 'object') return { x: toNum(v.x), y: toNum(v.y) };
        return toNum(v);
      });
      var out = {
        label: ds.label || ('계열 ' + (i + 1)),
        data: data,
        borderWidth: round ? 1 : 2,
      };
      if (round) {
        out.backgroundColor = labels.map(function (_l, j) { return PALETTE[j % PALETTE.length]; });
        out.borderColor = '#fff';
      } else {
        var dsType = ds.type ? String(ds.type).toLowerCase() : type;
        if (ds.type) out.type = dsType;
        out.borderColor = color;
        var hex = /^#[0-9a-f]{6}$/i.test(color);
        out.backgroundColor = hex ? color + (dsType === 'line' ? '33' : 'cc') : color;
        if (dsType === 'line') {
          out.tension = 0.25;
          out.pointRadius = data.length > 60 ? 0 : 3;
          out.fill = !!(ds.fill || spec.fill);
        }
        if (ds.y_axis === 'right') { out.yAxisID = 'y1'; hasRight = true; }
      }
      return out;
    });

    var options = {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        title: { display: !!spec.title, text: spec.title || '', font: { size: 14 } },
        legend: { display: round || datasets.length > 1, position: round ? 'right' : 'top' },
        tooltip: { mode: round ? 'nearest' : 'index', intersect: false },
      },
    };
    if (!round && type !== 'radar') {
      var stacked = !!spec.stacked;
      var catAxis = { stacked: stacked, title: { display: !!spec.x_label, text: spec.x_label || '' } };
      var valAxis = {
        stacked: stacked,
        beginAtZero: spec.begin_at_zero !== false,
        title: { display: !!spec.y_label, text: spec.y_label || '' },
      };
      if (horizontal) {
        options.indexAxis = 'y';
        options.scales = { y: catAxis, x: valAxis };
      } else {
        options.scales = { x: catAxis, y: valAxis };
      }
      if (hasRight) {
        options.scales.y1 = {
          position: 'right',
          beginAtZero: true,
          grid: { drawOnChartArea: false },
          title: { display: !!spec.y2_label, text: spec.y2_label || '' },
        };
      }
    }
    return { type: type === 'polararea' ? 'polarArea' : type, data: { labels: labels, datasets: datasets }, options: options };
  }

  function renderChart(raw) {
    var box = document.createElement('div');
    box.className = 'ai-chart-box';
    var spec = parseSpec(raw);
    if (!spec || typeof global.Chart !== 'function') {
      box.innerHTML =
        '<div class="ai-chart-error">' +
        (spec ? '그래프 라이브러리를 불러오지 못했습니다.' : '그래프 데이터 형식이 올바르지 않습니다.') +
        '</div><pre class="ai-md-code">' + escapeHtml(raw) + '</pre>';
      return box;
    }
    var area = document.createElement('div');
    area.className = 'ai-chart-area';
    var canvas = document.createElement('canvas');
    area.appendChild(canvas);
    box.appendChild(area);
    var tools = document.createElement('div');
    tools.className = 'ai-chart-tools';
    var png = document.createElement('button');
    png.type = 'button';
    png.className = 'ai-chart-btn';
    png.textContent = '이미지 저장';
    tools.appendChild(png);
    box.appendChild(tools);

    setTimeout(function () {
      try {
        var chart = new global.Chart(canvas, buildConfig(spec));
        charts.push(chart);
        png.addEventListener('click', function () {
          var a = document.createElement('a');
          a.href = chart.toBase64Image('image/png', 1);
          a.download = (spec.title || 'AI_그래프').replace(/[\\/:*?"<>|]/g, '_') + '.png';
          document.body.appendChild(a);
          a.click();
          a.remove();
        });
      } catch (e) {
        area.innerHTML = '<div class="ai-chart-error">그래프를 그리지 못했습니다: ' + escapeHtml(e.message) + '</div>';
      }
    }, 0);
    return box;
  }

  function render(container, text) {
    container.innerHTML = '';
    var src = String(text || '');
    var re = /```([\w-]*)[^\n]*\n([\s\S]*?)```/g;
    var last = 0;
    var m;
    var hasChart = false;
    while ((m = re.exec(src)) !== null) {
      if (m.index > last) container.appendChild(renderText(src.slice(last, m.index)));
      var lang = (m[1] || '').toLowerCase();
      if (lang === 'chart' || lang === 'chartjs') {
        container.appendChild(renderChart(m[2]));
        hasChart = true;
      } else {
        var pre = document.createElement('pre');
        pre.className = 'ai-md-code';
        pre.textContent = m[2];
        container.appendChild(pre);
      }
      last = re.lastIndex;
    }
    if (last < src.length) container.appendChild(renderText(src.slice(last)));
    return hasChart;
  }

  function destroyCharts() {
    charts.forEach(function (c) { try { c.destroy(); } catch (_e) { /* noop */ } });
    charts = [];
  }

  global.AiChatRender = { render: render, destroyCharts: destroyCharts, buildConfig: buildConfig };
})(window);
