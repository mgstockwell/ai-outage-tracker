// Cause → color. dash.colors are CSS var strings.
window.oaCauseColor = (c) => {
  const order = ['Change / config error', 'Software bug', 'Hardware / power', 'Third-party / vendor', 'Change / migration'];
  const i = order.indexOf(c);
  return i >= 0 ? dash.colors[i % dash.colors.length] : 'var(--cds-chart-muted)';
};
window.oaState = { size: 'reports', cat: '' };
window.oaFilter = (rows) => rows.filter((r) => !oaState.cat || r.category === oaState.cat);
window.oaFmtN = (n) => n == null ? '—' : n >= 1e6 ? d3.format('.1f')(n / 1e6).replace(/\.0$/, '') + 'M' : n >= 1e3 ? d3.format('.0f')(n / 1e3) + 'k' : d3.format(',')(n);
window.oaFmtH = (h) => h == null ? '—' : h < 1 ? Math.round(h * 60) + ' min' : d3.format('.1f')(h).replace(/\.0$/, '') + ' h';

// Legend swatches: set css vars from dash.colors on the root (style only).
document.querySelectorAll('.oa').forEach((el) => { dash.colors.slice(0, 5).forEach((c, i) => el.style.setProperty('--dash-c' + i, c)); });

dash.calc('total', {
  title: 'Outages in the set',
  description: 'Number of outages in the curated list.',
  inputs: ['outages'],
  fn: (outages) => outages.length,
});

dash.calc('summary', {
  title: 'Cause shares by era',
  description: "For each era, the share of outages with a disclosed cause that were the company's own change or bug, or a third-party vendor.",
  inputs: ['outages'],
  fn: (outages) => ['Pre-AI', 'AI era'].map((era) => {
    const rows = outages.filter((r) => r.era === era);
    const known = rows.filter((r) => r.cause_type !== 'Not disclosed');
    const self = known.filter((r) => ['Change / config error', 'Software bug', 'Change / migration'].includes(r.cause_type)).length;
    const vendor = known.filter((r) => r.cause_type === 'Third-party / vendor').length;
    return { era, incidents: rows.length, disclosed: known.length, undisclosed: rows.length - known.length,
      self_share: known.length ? Math.round(100 * self / known.length) : null,
      vendor_share: known.length ? Math.round(100 * vendor / known.length) : null };
  }),
});

dash.calc('causeMix', {
  title: 'Cause mix by era',
  description: 'Count and share of each disclosed root cause, before and during the AI era.',
  inputs: ['outages'],
  fn: (outages) => {
    const out = [];
    ['Pre-AI', 'AI era'].forEach((era) => {
      const known = outages.filter((r) => r.era === era && r.cause_type !== 'Not disclosed');
      d3.rollups(known, (v) => v.length, (r) => r.cause_type).forEach(([cause, n]) => out.push({ era, cause, count: n, share: Math.round(100 * n / known.length) }));
    });
    return out;
  },
});


// Controls: listen only; set aria-pressed / value.
document.querySelectorAll('#sizeby button').forEach((b) => b.addEventListener('click', () => {
  oaState.size = b.dataset.v;
  document.querySelectorAll('#sizeby button').forEach((x) => x.setAttribute('aria-pressed', x === b ? 'true' : 'false'));
  dash.draw('timeline');
}));
document.querySelectorAll('#catsel').forEach((s) => s.addEventListener('change', () => {
  oaState.cat = s.value;
  dash.draw('timeline'); dash.draw('table');
}));

const oaSimple = (name, src, fn) => dash.draw(name, (el) => {
  const d = dash.data(src);
  if (d.status !== 'ok' || d.data == null) { el.textContent = d.status === 'error' ? 'Failed to load' : '—'; return; }
  el.textContent = fn(d.data);
});
oaSimple('total', 'total', (n) => String(n));
oaSimple('largest-n', 'yearly', (rows) => { const r = rows.find((x) => +x.year === 2025); return r ? String(r.reports_millions) : '—'; });
oaSimple('largest-who', 'yearly', (rows) => { const r = rows.find((x) => +x.year === 2025); return r ? r.service : '—'; });
const oaEra = (era, f, suffix) => (rows) => { const r = rows.find((x) => x.era === era); return r && r[f] != null ? r[f] + (suffix || '') : '—'; };
oaSimple('self-pre', 'summary', oaEra('Pre-AI', 'self_share', '%'));
oaSimple('self-post', 'summary', oaEra('AI era', 'self_share', '%'));
oaSimple('vendor-pre', 'summary', oaEra('Pre-AI', 'vendor_share', '%'));
oaSimple('vendor-post', 'summary', oaEra('AI era', 'vendor_share', '%'));
oaSimple('undisc-pre', 'summary', oaEra('Pre-AI', 'undisclosed'));
oaSimple('undisc-post', 'summary', oaEra('AI era', 'undisclosed'));


// Tooltip helper inside a chart element.
window.oaTip = (el) => {
  const tip = document.createElement('div'); tip.className = 'tip'; tip.hidden = true; el.appendChild(tip);
  return {
    show(ev, lines) {
      tip.replaceChildren(...lines.map(([t, cls]) => { const d = document.createElement('div'); if (cls) d.className = cls; if (cls === 'b') { const b = document.createElement('b'); b.textContent = t; d.appendChild(b); } else d.textContent = t; return d; }));
      tip.hidden = false;
      const r = el.getBoundingClientRect(); let x = ev.clientX - r.left + 12, y = ev.clientY - r.top + 12;
      if (x + tip.offsetWidth > r.width) x = Math.max(0, ev.clientX - r.left - tip.offsetWidth - 12);
      if (y + tip.offsetHeight > r.height) y = Math.max(0, ev.clientY - r.top - tip.offsetHeight - 12);
      tip.style.left = x + 'px'; tip.style.top = y + 'px';
    },
    hide() { tip.hidden = true; },
  };
};

dash.draw('timeline', {
  mount: (el) => { el.replaceChildren(); const svg = d3.select(el).append('svg').attr('role', 'img').attr('aria-label', 'Timeline of outages from 2018 to 2026 by industry, sized by impact and colored by root cause'); return { svg, tip: oaTip(el) }; },
  update: (el, m) => {
    const d = dash.data('outages');
    const svg = m.svg; svg.selectAll('*').remove(); m.tip.hide();
    if (d.status !== 'ok') { svg.attr('height', 40).attr('width', '100%').append('text').attr('x', 0).attr('y', 20).text(d.status === 'error' ? 'Failed to load' : '—'); return; }
    const lanes = ['Banking', 'Payments & commerce', 'Cell & ISP', 'Social & streaming', 'Cloud & infrastructure', 'AI services'].filter((c) => !oaState.cat || c === oaState.cat);
    const rows = oaFilter(d.data).map((r) => ({ ...r, dt: new Date(r.date + 'T12:00:00Z') }));
    const W = Math.max(300, el.clientWidth), laneH = 52, top = 10, bottom = 28;
    const H = top + lanes.length * laneH + bottom;
    svg.attr('width', W).attr('height', H).attr('viewBox', `0 0 ${W} ${H}`);
    // measure lane label width
    const meas = svg.append('text').attr('class', 'lane-lbl');
    let lw = 0; lanes.forEach((l) => { meas.text(l); lw = Math.max(lw, meas.node().getComputedTextLength()); }); meas.remove();
    const narrow = W < 520; const left = narrow ? 8 : lw + 16, right = 16;
    const x = d3.scaleUtc().domain([new Date('2018-01-01T00:00:00Z'), new Date('2026-12-31T00:00:00Z')]).range([left + 14, W - right - 14]);
    const y = d3.scaleBand().domain(lanes).range([top, top + lanes.length * laneH]);
    const key = oaState.size === 'reports' ? 'reports' : 'duration_hours';
    const maxV = oaState.size === 'reports' ? 17000000 : 168;
    const rS = d3.scaleSqrt().domain([0, maxV]).range([3, narrow ? 18 : 24]);
    // lanes
    lanes.forEach((l) => {
      svg.append('line').attr('x1', left).attr('x2', W - right).attr('y1', y(l) + laneH).attr('y2', y(l) + laneH).style('stroke', 'var(--cds-chart-grid)');
      svg.append('text').attr('class', 'lane-lbl').attr('x', narrow ? left : left - 10).attr('y', narrow ? y(l) + 11 : y(l) + laneH / 2 + 4).attr('text-anchor', narrow ? 'start' : 'end').text(l);
    });
    // axis
    const ticks = x.ticks(d3.utcYear.every(narrow ? 2 : 1));
    svg.append('g').attr('transform', `translate(0,${top + lanes.length * laneH})`).call(d3.axisBottom(x).tickValues(ticks).tickFormat(d3.utcFormat('%Y')).tickSizeOuter(0))
      .call((g) => g.select('.domain').style('stroke', 'var(--cds-chart-axis)')).call((g) => g.selectAll('line').style('stroke', 'var(--cds-chart-axis)'));
    // AI line
    const ax = x(new Date('2022-11-30T00:00:00Z'));
    svg.append('line').attr('x1', ax).attr('x2', ax).attr('y1', top - 6).attr('y2', top + lanes.length * laneH).style('stroke', 'var(--cds-chart-reference)').style('stroke-dasharray', '4 3');
    svg.append('text').attr('x', ax + 4).attr('y', top + (narrow ? 22 : 4)).text('ChatGPT launch');
    // bubbles (large first so small sit on top)
    const pts = rows.map((r) => ({ r, v: r[key] == null ? null : +r[key] })).sort((a, b) => (b.v || 0) - (a.v || 0));
    const yOff = narrow ? 8 : 0;
    svg.append('g').selectAll('circle').data(pts).join('circle')
      .attr('cx', (p) => x(p.r.dt)).attr('cy', (p) => y(p.r.category) + laneH / 2 + yOff)
      .attr('r', (p) => p.v == null ? 4 : rS(p.v))
      .style('fill', (p) => p.v == null ? 'none' : oaCauseColor(p.r.cause_type)).style('fill-opacity', 0.72)
      .style('stroke', (p) => oaCauseColor(p.r.cause_type)).style('stroke-width', 1.5)
      .style('cursor', 'default')
      .on('pointerenter pointermove', (ev, p) => {
        const r = p.r;
        m.tip.show(ev, [
          [r.service, 'b'],
          [d3.utcFormat('%b %-d, %Y')(r.dt) + ' · ' + r.region, 'm'],
          ['Duration: ' + (r.duration_hours == null ? (r.duration_note || 'not stated') : oaFmtH(+r.duration_hours) + (r.duration_note ? ' (' + r.duration_note + ')' : ''))],
          ['Reports: ' + (r.reports == null ? 'not published' : oaFmtN(+r.reports) + ' (' + r.reports_type + ')')],
          ...(r.impact_note ? [['Impact: ' + r.impact_note]] : []),
          ['Cause: ' + r.cause_type + (r.cause && r.cause !== 'Not disclosed' ? ' — ' + r.cause : '')],
        ]);
      })
      .on('pointerleave', () => m.tip.hide());
  },
});

dash.draw('yearly', {
  mount: (el) => { el.replaceChildren(); const svg = d3.select(el).append('svg').attr('role', 'img').attr('aria-label', 'Bar chart of the largest Downdetector outage each year, 2018 to 2026, in millions of reports'); return { svg, tip: oaTip(el) }; },
  update: (el, m) => {
    const d = dash.data('yearly'); const svg = m.svg; svg.selectAll('*').remove(); m.tip.hide();
    if (d.status !== 'ok') { svg.attr('height', 40).attr('width', '100%').append('text').attr('y', 20).text(d.status === 'error' ? 'Failed to load' : '—'); return; }
    const years = d3.range(2018, 2027); const by = new Map(d.data.map((r) => [+r.year, r]));
    const W = Math.max(280, el.clientWidth), H = 250, top = 22, bottom = 26, left = 4, right = 4;
    svg.attr('width', W).attr('height', H).attr('viewBox', `0 0 ${W} ${H}`);
    const x = d3.scaleBand().domain(years).range([left, W - right]).padding(0.22);
    const y = d3.scaleLinear().domain([0, 18]).range([H - bottom, top]);
    svg.append('line').attr('x1', left).attr('x2', W - right).attr('y1', H - bottom).attr('y2', H - bottom).style('stroke', 'var(--cds-chart-axis)');
    years.forEach((yr) => {
      svg.append('text').attr('x', x(yr) + x.bandwidth() / 2).attr('y', H - 8).attr('text-anchor', 'middle').text(W < 420 ? "'" + String(yr).slice(2) : yr);
      const r = by.get(yr);
      if (!r) { svg.append('text').attr('x', x(yr) + x.bandwidth() / 2).attr('y', H - bottom - 6).attr('text-anchor', 'middle').text('n/a'); return; }
      const v = +r.reports_millions;
      svg.append('rect').attr('x', x(yr)).attr('width', x.bandwidth()).attr('y', y(v)).attr('height', y(0) - y(v)).attr('rx', 2)
        .style('fill', yr >= 2023 ? dash.colors[0] : 'var(--cds-chart-muted)')
        .on('pointerenter pointermove', (ev) => m.tip.show(ev, [[String(yr) + ': ' + r.service, 'b'], [v + 'M Downdetector reports'], ...(r.note ? [[r.note, 'm']] : [])]))
        .on('pointerleave', () => m.tip.hide());
      svg.append('text').attr('x', x(yr) + x.bandwidth() / 2).attr('y', y(v) - 5).attr('text-anchor', 'middle').style('fill', 'var(--color-fg)').text(v + (r.note && /First half|Q1|Year to date/.test(r.note) ? '*' : ''));
    });
    const ax = x(2023) - (x.step() * x.padding()) / 2 - 1;
    svg.append('line').attr('x1', ax).attr('x2', ax).attr('y1', 4).attr('y2', H - bottom).style('stroke', 'var(--cds-chart-reference)').style('stroke-dasharray', '4 3');
    svg.append('text').attr('x', ax + 4).attr('y', 12).text('AI era →');
  },
});

dash.draw('causes', {
  mount: (el) => { el.replaceChildren(); const svg = d3.select(el).append('svg').attr('role', 'img').attr('aria-label', 'Stacked bars comparing the share of disclosed root causes before and during the AI era'); return { svg, tip: oaTip(el) }; },
  update: (el, m) => {
    const d = dash.data('causeMix'); const svg = m.svg; svg.selectAll('*').remove(); m.tip.hide();
    if (d.status !== 'ok' || !d.data) { svg.attr('height', 40).attr('width', '100%').append('text').attr('y', 20).text(d.status === 'error' ? 'Failed to load' : '—'); return; }
    const order = ['Change / config error', 'Change / migration', 'Software bug', 'Hardware / power', 'Third-party / vendor'];
    const eras = ['Pre-AI', 'AI era'];
    const W = Math.max(280, el.clientWidth), barH = 34, gap = 34, top = 18;
    const H = top + eras.length * (barH + gap);
    svg.attr('width', W).attr('height', H).attr('viewBox', `0 0 ${W} ${H}`);
    const x = d3.scaleLinear().domain([0, 100]).range([0, W]);
    eras.forEach((era, i) => {
      const segs = order.map((c) => d.data.find((r) => r.era === era && r.cause === c)).filter(Boolean);
      const n = d3.sum(segs, (s) => s.count);
      const y0 = top + i * (barH + gap);
      svg.append('text').attr('x', 0).attr('y', y0 - 6).style('fill', 'var(--color-fg)').text((era === 'Pre-AI' ? 'Before AI era (2018–Nov 2022)' : 'AI era (Dec 2022–2026)') + ' · ' + n + ' outages');
      let acc = 0;
      segs.forEach((s) => {
        const w = x(s.share);
        svg.append('rect').attr('x', x(acc)).attr('y', y0).attr('width', Math.max(0, w - 2)).attr('height', barH).attr('rx', 2).style('fill', oaCauseColor(s.cause))
          .on('pointerenter pointermove', (ev) => m.tip.show(ev, [[s.cause, 'b'], [s.share + '% (' + s.count + ' of ' + n + ')']]))
          .on('pointerleave', () => m.tip.hide());
        if (w > 34) svg.append('text').attr('x', x(acc) + 6).attr('y', y0 + barH / 2 + 4).style('fill', 'var(--color-bg)').style('pointer-events', 'none').text(s.share + '%');
        acc += s.share;
      });
    });
  },
});

dash.draw('table', (tb) => {
  const d = dash.data('outages');
  if (d.status !== 'ok') { tb.replaceChildren(); return; }
  const rows = oaFilter(d.data).slice().sort((a, b) => d3.descending(a.date, b.date));
  tb.replaceChildren(...rows.map((r) => {
    const tr = document.createElement('tr');
    tr.setAttribute('data-key', encodeURIComponent(r.date) + ',' + encodeURIComponent(r.service));
    const cell = (t, cls) => { const td = document.createElement('td'); if (cls) td.className = cls; td.textContent = t; tr.appendChild(td); };
    cell(r.date); cell(r.service); cell(r.category);
    cell(r.duration_hours == null ? (r.duration_note || '—') : String(r.duration_hours), 'r');
    cell(r.reports == null ? '—' : d3.format(',')(r.reports), 'r');
    cell(r.reports_type ? (r.reports_type.startsWith('Total') ? 'Total' : 'Peak') : '—');
    cell(r.cause_type); cell(r.cause);
    const td = document.createElement('td'); const a = document.createElement('a'); a.href = r.source; a.target = '_blank'; a.rel = 'noopener';
    try { a.textContent = new URL(r.source).hostname.replace(/^www\./, ''); } catch (e) { a.textContent = 'link'; }
    td.appendChild(a); tr.appendChild(td);
    return tr;
  }));
});
