// Minimal stand-in for the Claude dashboard runtime, so the page runs on any static host
// (GitHub Pages, a local web server). It provides the small API that assets/oa.js uses:
// dash.data, dash.calc, dash.draw, dash.onData and dash.colors.
(function () {
  const SOURCES = {
    outages: 'data/outages.json',
    yearly: 'data/yearly_largest.json',
  };

  const store = {};   // id -> {status, data, message}
  const calcs = {};   // id -> {inputs, fn}
  const draws = {};   // name -> fn | {mount, update, unmount}
  const listeners = [];
  let scheduled = false;

  function load(id) {
    if (store[id]) return;
    store[id] = { status: 'loading', data: [] };
    fetch(SOURCES[id], { cache: 'no-cache' })
      .then((r) => { if (!r.ok) throw new Error(r.status + ' ' + r.statusText); return r.json(); })
      .then((json) => {
        const rows = Array.isArray(json) ? json : (json.rows || json.data || []);
        store[id] = { status: 'ok', data: rows };
      })
      .catch((e) => { store[id] = { status: 'error', data: [], message: String(e) }; console.error('Failed to load', id, e); })
      .finally(schedule);
  }

  function data(id) {
    if (calcs[id]) {
      const c = calcs[id];
      const ins = c.inputs.map(data);
      const bad = ins.find((d) => d.status !== 'ok');
      if (bad) return { status: bad.status, data: null, message: bad.message };
      try { return { status: 'ok', data: c.fn(...ins.map((d) => d.data)) }; }
      catch (e) { console.error('calc', id, e); return { status: 'error', data: null, message: String(e) }; }
    }
    if (!SOURCES[id]) return { status: 'missing', data: [] };
    load(id);
    return store[id];
  }

  function runDraw(name) {
    const spec = draws[name];
    if (!spec) return;
    document.querySelectorAll('[data-draw="' + name + '"]').forEach((el) => {
      try {
        if (typeof spec === 'function') { spec(el); return; }
        if (!el.__dashMade) { el.replaceChildren(); el.__dashMade = { v: spec.mount ? spec.mount(el) : undefined }; }
        if (spec.update) spec.update(el, el.__dashMade.v);
      } catch (e) { console.error('draw', name, e); }
    });
  }

  function renderAll() {
    scheduled = false;
    listeners.forEach((fn) => { try { fn(); } catch (e) { console.error(e); } });
    Object.keys(draws).forEach(runDraw);
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(renderAll);
  }

  window.dash = {
    colors: ['var(--c0)', 'var(--c1)', 'var(--c2)', 'var(--c3)', 'var(--c4)', 'var(--c5)'],
    data,
    calc(id, spec) { calcs[id] = spec; spec.inputs.forEach(load); schedule(); },
    loader() { /* not needed for static JSON files */ },
    draw(name, spec) { if (spec === undefined) { runDraw(name); return; } draws[name] = spec; schedule(); },
    onData(fn) { listeners.push(fn); schedule(); },
    params() { return {}; },
    setParams() {}, resetParams() {}, setLink() {}, refresh() {},
  };

  Object.keys(SOURCES).forEach(load);
  let lastW = 0;
  window.addEventListener('resize', () => {
    const w = document.documentElement.clientWidth;
    if (w !== lastW) { lastW = w; schedule(); }
  });
})();
