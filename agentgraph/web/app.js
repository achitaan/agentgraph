(function () {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const LIST_LIMIT = 200;
  const EDGE = 12, SLOT = 104, GROUP_GAP = 34, SEAT_DISTANCE = 68;
  let instance = 0;
  const STATES = { pending: 'Waiting', waiting: 'Waiting', running: 'Working', returned: 'Returned', passed: 'Passed', failed: 'Failed', blocked: 'Blocked' };
  const LOOP_STATES = { working: 'Revising', checking: 'In review', sent_back: 'Sent back', closed: 'Done', idle: 'Idle' };
  const WORK_LOOP_STATES = { working: 'Working', waiting: 'Waiting', stuck: 'Stuck', closed: 'Done', idle: 'Idle' };
  function el(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }
  function svg(tag, attrs, text) {
    const item = document.createElementNS(NS, tag);
    Object.entries(attrs || {}).forEach(([key, value]) => item.setAttribute(key, String(value)));
    if (text !== undefined) item.textContent = text;
    return item;
  }
  function text(value) { return value == null ? '' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value); }
  function cost(value) { return '$' + (Number(value) || 0).toFixed(2); }
  function amount(value) { const n = Number(value) || 0; return n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1000 ? (n / 1000).toFixed(n >= 1e5 ? 0 : 1) + 'k' : String(Math.round(n)); }
  function named(labels, value) { return Object.hasOwn(labels, value) ? labels[value] : text(value); }
  function state(value) { return named(STATES, value); }
  function ago(time) { const s = Math.max(0, Date.now() / 1000 - (Number(time) || 0)); return s < 90 ? 'now' : s < 5400 ? Math.round(s / 60) + 'm' : s < 129600 ? Math.round(s / 3600) + 'h' : Math.round(s / 86400) + 'd'; }
  function clamp(value, min, max) { return Math.min(max, Math.max(min, value)); }
  function shorten(value) {
    const words = text(value).trim().split(/\s+/);
    const result = words.slice(0, 3).join(' ');
    return result.length > 24 ? result.slice(0, 22).trim() + '…' : result + (words.length > 3 ? '…' : '');
  }

  function mount(root, options) {
    if (!root || !options || typeof options.fetchSnapshot !== 'function') throw new Error('A root and fetchSnapshot are required');
    let alive = true, snapshot = null, pending = null, selected = null, selectedOrigin = null;
    let initialSelection = root.dataset.initialSelection || '', detailSignature = '', highlightedStep = '', renderRevision = 0;
    const uid = 'ag-flow-' + (++instance);
    const cleanups = [], positions = new Map(), nodes = new Map(), lines = new Map(), loopViews = new Map(), workLoopViews = new Map(), openSteps = new Set();
    const openFolds = new Set();
    const lazySteps = new WeakMap();
    const camera = { x: 0, y: 0, scale: 1, initialized: false, touched: false, width: 0, height: 0 };
    const pointers = new Map();
    let drag = null, pinch = null, suppressClick = false;
    let layoutSignature = '', layoutFrame = null, resizeFrame = null, generation = 0, renderStamp = '';
    let arrangement = root.clientWidth > 0 && root.clientWidth <= 650 ? 'compact' : 'wide';
    let agentsByName = new Map(), stepsByAgent = new Map(), stepsById = new Map(), nodeOwners = new Map(), shownAgents = new Map();
    let visibleAgents = [], hubName = '', activeTeam = 'main';
    let teamChoice = null;
    const teamViews = new Map();
    let teamChoices = [];
    root.classList.add('agentgraph');
    root.dataset.state = 'loading';
    root.replaceChildren();
    function listen(target, name, handler, opts) {
      target.addEventListener(name, handler, opts);
      cleanups.push(() => target.removeEventListener(name, handler, opts));
    }
    root.dataset.agMotion = options.motion === 'none' ? 'none' : 'full';
    const schemePreference = ['light', 'dark'].includes(options.scheme) ? options.scheme : 'auto';
    const systemScheme = window.matchMedia('(prefers-color-scheme: dark)');
    function updateScheme() {
      if (!alive) return;
      let scheme = schemePreference;
      let source = 'override';
      if (scheme === 'auto') {
        scheme = '';
        source = 'system';
        for (let ancestor = root; ancestor; ancestor = ancestor.parentElement || ancestor.getRootNode?.().host || null) {
          const choice = ancestor.getAttribute('data-color-scheme');
          if (choice === 'light' || choice === 'dark') { scheme = choice; source = 'host'; break; }
        }
        if (!scheme) {
          const choice = document.documentElement.getAttribute('data-color-scheme');
          if (choice === 'light' || choice === 'dark') { scheme = choice; source = 'host'; }
          else scheme = systemScheme.matches ? 'dark' : 'light';
        }
      }
      root.dataset.agScheme = scheme;
      root.dataset.agSchemeSource = source;
    }
    const schemeObserver = new MutationObserver(updateScheme);
    schemeObserver.observe(document.documentElement, { attributes: true, subtree: true, attributeFilter: ['data-color-scheme', 'data-theme'] });
    if (systemScheme.addEventListener) listen(systemScheme, 'change', updateScheme);
    else { systemScheme.addListener(updateScheme); cleanups.push(() => systemScheme.removeListener(updateScheme)); }
    updateScheme();

    const notice = el('div', 'ag-notice');
    notice.hidden = true;
    notice.setAttribute('role', 'status');
    const network = el('section', 'ag-network');
    network.setAttribute('aria-label', 'Agent network');
    const activity = el('div', 'ag-activity');
    activity.hidden = true; activity.setAttribute('aria-live', 'polite');
    const viewport = el('div', 'ag-viewport');
    viewport.tabIndex = 0;
    viewport.setAttribute('role', 'group');
    viewport.setAttribute('aria-keyshortcuts', 'F + - ArrowUp ArrowDown ArrowLeft ArrowRight Shift+Enter');
    const drawing = svg('svg', { class: 'ag-drawing', width: '100%', height: '100%', 'aria-label': 'Agents' });
    const defs = svg('defs');
    ['data', 'feedback', 'loop', 'work', 'work-stuck', 'work-closed', 'data-live', 'feedback-live', 'loop-live', 'work-live'].forEach(kind => {
      const marker = svg('marker', { id: uid + '-' + kind, viewBox: '-7 -5 9 10', refX: 0, refY: 0, markerWidth: 9, markerHeight: 10, markerUnits: 'userSpaceOnUse', orient: 'auto' });
      marker.append(svg('polyline', { class: 'ag-arrow ag-arrow-' + kind, points: '-6,-3 0,0 -6,3' }));
      defs.append(marker);
    });
    const stage = svg('g');
    const edgeLayer = svg('g', { class: 'ag-edges', 'aria-hidden': 'true' });
    const spokeLayer = svg('g'), escalationLayer = svg('g'), flowLayer = svg('g');
    edgeLayer.append(spokeLayer, escalationLayer, flowLayer);
    const nodeLayer = svg('g', { class: 'ag-nodes' });
    const loopLayer = svg('g', { class: 'ag-loops' });
    stage.append(edgeLayer, loopLayer, nodeLayer); drawing.append(defs, stage);
    const empty = el('div', 'ag-empty', 'Loading');
    viewport.append(drawing, empty);
    const panCue = el('button', 'ag-pan-cue');
    panCue.type = 'button'; panCue.hidden = true;
    viewport.append(panCue);
    const controls = el('div', 'ag-controls');
    const fit = el('button', 'ag-control ag-fit', '⌖');
    const runDetails = el('button', 'ag-control ag-run-button', '≡');
    [[fit, 'Fit'], [runDetails, 'Run details']].forEach(([button, label]) => {
      button.type = 'button'; button.setAttribute('aria-label', label); button.title = label;
    });
    controls.append(fit, runDetails);
    const status = el('div', 'ag-status');
    const attention = el('button', 'ag-attention');
    attention.type = 'button'; attention.hidden = true; attention.setAttribute('aria-label', 'Needs attention');
    status.append(activity, attention);
    const usage = el('button', 'ag-usage');
    usage.type = 'button'; usage.hidden = true; usage.setAttribute('aria-label', 'Usage');
    const usageCost = el('span', 'ag-usage-cost'), usageTokens = el('span', 'ag-usage-tokens'), usageContext = el('span', 'ag-usage-context');
    usage.append(usageCost, usageTokens, usageContext);
    network.append(status, usage, viewport, controls);
    const detail = el('aside', 'ag-detail');
    detail.hidden = true; detail.setAttribute('aria-label', 'Details');
    const detailHeader = el('div', 'ag-detail-header');
    const eyebrow = el('div', 'ag-eyebrow');
    const close = el('button', 'ag-close', '×');
    close.type = 'button'; close.setAttribute('aria-label', 'Close');
    detailHeader.append(eyebrow, close);
    const detailScroll = el('div', 'ag-detail-scroll');
    detail.append(detailHeader, detailScroll); root.append(notice, network, detail);

    function renderActivity() {
      const working = snapshot ? visibleAgents.filter(agent => agent.busy > 0).length : 0;
      activity.hidden = !working;
      activity.textContent = working ? working + ' working' : '';
      activity.dataset.active = String(working > 0);
      const alerts = snapshot ? attentionAgents().size : 0;
      attention.hidden = !alerts; attention.textContent = alerts ? '! ' + alerts : '';
      usage.hidden = !snapshot;
      if (!snapshot) return;
      usageCost.textContent = cost(snapshot.totals?.cost);
      usageTokens.textContent = amount(snapshot.totals?.tokens_in) + '↑ ' + amount(snapshot.totals?.tokens_out) + '↓';
      usageContext.hidden = !snapshot.root?.context;
      usageContext.textContent = snapshot.root?.context ? 'ctx ' + amount(snapshot.root.context) : '';
    }
    // Agents the user should look at: a step that failed, is blocked or stalled,
    // a work loop that is stuck, or work a reviewer sent back.
    function attentionAgents() {
      const found = new Map();
      (snapshot?.graph?.nodes || []).forEach(node => {
        if (!['failed', 'blocked'].includes(node.status) && !node.stalled) return;
        const owner = nodeOwners.get(node.id);
        if (owner && shownAgents.has(owner) && !found.has(owner)) found.set(owner, node.stalled ? 'Stuck' : state(node.status));
      });
      canvasWorkLoops().forEach(loop => { if (loop.state === 'stuck' && !found.has(loop.agent)) found.set(loop.agent, 'Stuck'); });
      canvasLoops().forEach(loop => { if (loop.state === 'sent_back' && !found.has(loop.worker)) found.set(loop.worker, 'Sent back'); });
      return found;
    }
    function usageOf(sessions) {
      const known = sessions.filter(session => session.context), current = known.find(session => session.busy) || known[0];
      return { cost: sessions.reduce((sum, s) => sum + (Number(s.cost) || 0), 0), sent: sessions.reduce((sum, s) => sum + (Number(s.tokens_in) || 0), 0), received: sessions.reduce((sum, s) => sum + (Number(s.tokens_out) || 0), 0), context: current?.context || 0 };
    }
    function stats(parent, used) {
      const grid = el('div', 'ag-stats');
      [['Spent', cost(used.cost)], ['In', amount(used.sent)], ['Out', amount(used.received)], ['Context', used.context ? amount(used.context) : '']].forEach(([label, value]) => {
        if (!value) return;
        const cell = el('div', 'ag-stat'); cell.append(el('span', 'ag-stat-label', label), el('span', 'ag-stat-value', value)); grid.append(cell);
      });
      parent.append(grid);
    }
    function showEmpty() {
      empty.hidden = visibleAgents.length > 0;
      empty.textContent = root.dataset.state === 'loading' ? 'Loading' : root.dataset.state === 'error' ? 'Unavailable' : 'No agents';
    }

    function transform() {
      stage.setAttribute('transform', 'translate(' + camera.x + ' ' + camera.y + ') scale(' + camera.scale + ')');
      updatePanCue();
    }
    function graphBounds() {
      const points = visibleAgents.map(a => positions.get(a.name)).filter(Boolean);
      if (!points.length) return null;
      return {
        left: Math.min(...points.map(p => Math.min(p.x - p.r - 14, p.x + (p.labelMin ?? -40)))),
        right: Math.max(...points.map(p => Math.max(p.x + p.r + 14, p.x + (p.labelMax ?? 40)))),
        top: Math.min(...points.map(p => p.y - p.r - (p.labelUp ? 26 : 16))),
        bottom: Math.max(...points.map(p => p.y + p.r + 50))
      };
    }
    function updatePanCue() {
      const bounds = graphBounds(), height = viewport.clientHeight;
      const below = !!bounds && bounds.bottom * camera.scale + camera.y > height + 1;
      const above = !!bounds && bounds.top * camera.scale + camera.y < -1;
      panCue.hidden = arrangement !== 'compact' || (!below && !above);
      panCue.dataset.direction = below ? 'down' : 'up';
      panCue.textContent = below ? '↓' : '↑';
      panCue.setAttribute('aria-label', below ? 'More below' : 'More above');
    }
    // The sidebar keeps a readable scale: what does not fit is reached by
    // panning, and the picture stays centred on the orchestrator.
    function clampPan() {
      if (arrangement !== 'compact') return;
      const bounds = graphBounds();
      if (!bounds) return;
      const width = viewport.clientWidth, height = viewport.clientHeight;
      const top = 8 - bounds.top * camera.scale, bottom = height - 8 - bounds.bottom * camera.scale;
      camera.y = bottom >= top ? (top + bottom) / 2 : clamp(camera.y, bottom, top);
      const left = 2 - bounds.left * camera.scale, right = width - 2 - bounds.right * camera.scale;
      camera.x = right >= left ? clamp(width / 2, left, right) : clamp(camera.x, right, left);
    }
    function zoomAt(next, x, y) {
      const rect = viewport.getBoundingClientRect();
      x = x === undefined ? rect.width / 2 : x; y = y === undefined ? rect.height / 2 : y;
      next = clamp(next, .2, 3);
      camera.x = x - (x - camera.x) * next / camera.scale;
      camera.y = y - (y - camera.y) * next / camera.scale;
      camera.scale = next; camera.touched = true; transform();
    }
    // Bring the selected agent into the space the details leave uncovered.
    function reveal() {
      const point = selected?.type === 'agent' || selected?.type === 'work-loop' ? positions.get(selected.key) : null;
      if (!point || arrangement !== 'compact') return;
      const y = point.y * camera.scale + camera.y, height = viewport.clientHeight;
      if (y < point.r + 16 || y > height - point.r - 44) { camera.y += height / 2 - y; clampPan(); transform(); }
    }
    function fitView(userAction) {
      if (!positions.size) return;
      const rect = viewport.getBoundingClientRect(), bounds = graphBounds();
      if (!rect.width || !rect.height || !bounds) return;
      if (arrangement === 'compact' && !userAction) {
        camera.scale = 1; camera.x = rect.width / 2; camera.y = rect.height / 2;
        clampPan();
      } else {
        const pad = arrangement === 'compact' ? 6 : 26;
        camera.scale = clamp(Math.min((rect.width - pad * 2) / (bounds.right - bounds.left), (rect.height - pad * 2) / (bounds.bottom - bounds.top), arrangement === 'compact' ? 1 : 1.3), .05, 3);
        camera.x = rect.width / 2 - (bounds.left + bounds.right) / 2 * camera.scale;
        camera.y = rect.height / 2 - (bounds.top + bounds.bottom) / 2 * camera.scale;
      }
      camera.width = rect.width; camera.height = rect.height;
      camera.initialized = true;
      camera.touched = !!userAction;
      transform();
    }

    function flows() { return snapshot?.graph?.flows || []; }
    function loops() { return snapshot?.graph?.loops || []; }
    function workLoops() { return snapshot?.graph?.work_loops || []; }
    function canvasLoops() { return loops().filter(loop => allowedConnection(loop.worker, loop.evaluator)); }
    function canvasWorkLoops() {
      return workLoops().filter(loop => {
        const agent = shownAgents.get(loop.agent);
        return agent && agent.group !== 'hub' && agent.name !== hubName;
      });
    }
    // A review loop is a slim closed leaf between its two agents: out along
    // one edge, back along the other. It bows just far enough to pass clear of
    // every other agent and of the orchestrator's name.
    function reviewGeometry(a, b) {
      const dx = b.x - a.x, dy = b.y - a.y, distance = Math.hypot(dx, dy) || 1;
      const nx = -dy / distance, ny = dx / distance;
      const bulge = clamp(distance * .08, 8, 16);
      const obstacles = [], hub = positions.get(hubName);
      positions.forEach(p => { if (p !== a && p !== b) obstacles.push({ x: p.x, y: p.y, r: p.r + 9 }); });
      if (hub && hub !== a && hub !== b) obstacles.push({ x: hub.x, y: hub.y + hub.r + 18, r: 26 });
      const lanes = bow => [1, -1].map(side => {
        const from = side > 0 ? a : b, to = side > 0 ? b : a;
        const cx = (a.x + b.x) / 2 + nx * (bow + bulge * side) * 2, cy = (a.y + b.y) / 2 + ny * (bow + bulge * side) * 2;
        const out = Math.hypot(cx - from.x, cy - from.y) || 1, back = Math.hypot(cx - to.x, cy - to.y) || 1;
        return { cx, cy, sx: from.x + (cx - from.x) / out * (from.r + 6), sy: from.y + (cy - from.y) / out * (from.r + 6), ex: to.x + (cx - to.x) / back * (to.r + 8), ey: to.y + (cy - to.y) / back * (to.r + 8) };
      });
      const blocked = bow => {
        let penalty = 0;
        lanes(bow).forEach(lane => {
          for (let i = 0; i <= 14; i++) {
            const t = i / 14, u = 1 - t, x = u * u * lane.sx + 2 * u * t * lane.cx + t * t * lane.ex, y = u * u * lane.sy + 2 * u * t * lane.cy + t * t * lane.ey;
            obstacles.forEach(o => { penalty += Math.max(0, o.r - Math.hypot(x - o.x, y - o.y)) ** 2; });
          }
        });
        return penalty;
      };
      let bow = 0, best = blocked(0);
      for (let step = 8; best > 0 && step <= Math.max(48, distance * .7); step += 8) for (const candidate of [step, -step]) {
        const penalty = blocked(candidate);
        if (penalty < best) { best = penalty; bow = candidate; }
      }
      const [forward, back] = lanes(bow).map(lane => 'M ' + lane.sx + ' ' + lane.sy + ' Q ' + lane.cx + ' ' + lane.cy + ' ' + lane.ex + ' ' + lane.ey);
      return { forward, back, count: { x: (a.x + b.x) / 2 + nx * bow, y: (a.y + b.y) / 2 + ny * bow } };
    }
    function connectionKey(from, to) { return JSON.stringify([from, to].sort()); }
    function agentTeam(agent) { return agent.team !== undefined ? agent.team : 'main'; }
    function prepareRoster(data) {
      const rootTeam = data.team !== undefined ? data.team : 'main';
      const agents = data.agents.map(agent => {
        const team = agentTeam(agent), group = agent.group;
        const parent = agent.parent || '';
        return { ...agent, team, group, parent };
      });
      const teams = Array.from(new Set(agents.map(a => a.team))).sort((a, b) => Number(b === rootTeam) - Number(a === rootTeam) || a.localeCompare(b));
      if (teamChoice !== null && !teams.includes(teamChoice)) teamChoice = null;
      activeTeam = teamChoice !== null ? teamChoice : teams.includes(rootTeam) ? rootTeam : teams[0] || rootTeam;
      teamChoices = teams.map(team => {
        const roster = agents.filter(a => a.team === team);
        const hub = roster.find(a => a.group === 'hub' && a.name === data.root?.agent) || roster.find(a => a.group === 'hub');
        return { team, label: (hub?.name || team) + ' · ' + roster.length };
      });
      root.dataset.team = activeTeam;
      visibleAgents = agents.filter(a => a.team === activeTeam);
      shownAgents = new Map(visibleAgents.map(a => [a.name, a]));
      hubName = (visibleAgents.find(a => a.group === 'hub' && a.name === data.root?.agent) || visibleAgents.find(a => a.group === 'hub'))?.name || '';
      return agents;
    }
    function rememberTeam(team) {
      if (!camera.initialized) return;
      teamViews.set(team + ':' + arrangement, { positions: Array.from(positions, ([key, point]) => [key, { ...point }]), camera: { ...camera }, signature: layoutSignature });
    }
    function restoreTeam(team) {
      stopLayout(); positions.clear();
      const saved = teamViews.get(team + ':' + arrangement), rect = viewport.getBoundingClientRect();
      if (saved) {
        saved.positions.forEach(([key, point]) => positions.set(key, { ...point }));
        Object.assign(camera, saved.camera);
        camera.x += (rect.width - camera.width) / 2; camera.y += (rect.height - camera.height) / 2;
        layoutSignature = saved.signature;
      } else {
        Object.assign(camera, { x: 0, y: 0, scale: 1, initialized: false, touched: false });
        layoutSignature = '';
      }
      camera.width = rect.width; camera.height = rect.height;
      transform();
    }
    function switchTeam(team) {
      if (!snapshot || team === activeTeam) return;
      teamChoice = team;
      selected = null; detailSignature = ''; highlightedStep = '';
      detail.hidden = true; root.classList.remove('ag-details-open');
      render(snapshot); scheduleResize();
    }
    function allowedConnection(from, to) {
      const a = shownAgents.get(from), b = shownAgents.get(to);
      const isSeat = agent => agent.parent ? agent.parent !== hubName : agent.name.startsWith('councillor-');
      return a && b && !(a.group === 'hub' && isSeat(b)) && !(b.group === 'hub' && isSeat(a)) && (!isSeat(a) || a.parent === b.name) && (!isSeat(b) || b.parent === a.name);
    }
    function canvasFlows() {
      const loopPairs = new Set(canvasLoops().map(loop => connectionKey(loop.worker, loop.evaluator)));
      return flows().filter(f => f.from !== hubName && f.to !== hubName && allowedConnection(f.from, f.to) && !(['data', 'feedback'].includes(f.kind) && loopPairs.has(connectionKey(f.from, f.to))));
    }
    function displayName(agent) { return agent.parent ? agent.name.replace(/^councillor-/, '') : agent.name; }
    function stopLayout() {
      if (layoutFrame !== null) cancelAnimationFrame(layoutFrame);
      layoutFrame = null;
    }
    function cancelInteraction() {
      if (drag || pinch || pointers.size) suppressClick = true;
      drag = null; pinch = null;
      const captured = Array.from(pointers.keys()); pointers.clear();
      captured.forEach(id => { if (viewport.hasPointerCapture(id)) viewport.releasePointerCapture(id); });
      viewport.dataset.dragging = 'false';
    }
    function updateDetailSpace() {
      if (root.classList.contains('ag-compact') && !detail.hidden) root.style.setProperty('--ag-sheet-height', Math.max(0, Math.min(root.clientHeight * .6, root.clientHeight - 112)) + 'px');
      else root.style.removeProperty('--ag-sheet-height');
    }
    function perimeter(a, b) { return Math.PI * (3 * (a + b) - Math.sqrt((3 * a + b) * (a + 3 * b))); }
    // Points by distance travelled round an ellipse, clockwise from the top.
    function ellipse(rx, ry) {
      const steps = 360, points = [{ s: 0, x: 0, y: -ry }];
      let length = 0;
      for (let i = 1; i <= steps; i++) {
        const t = -Math.PI / 2 + i * Math.PI * 2 / steps, x = rx * Math.cos(t), y = ry * Math.sin(t), last = points[i - 1];
        length += Math.hypot(x - last.x, y - last.y);
        points.push({ s: length, x, y });
      }
      return { length, at(s) {
        s = ((s % length) + length) % length;
        let low = 0, high = steps;
        while (high - low > 1) { const middle = (low + high) >> 1; if (points[middle].s <= s) low = middle; else high = middle; }
        const a = points[low], b = points[high], t = (s - a.s) / ((b.s - a.s) || 1);
        return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
      } };
    }
    // Seats sit evenly on a circle round their lead; no seat lands on the
    // lead's own label, which is straight below it.
    function seatOffsets(count) {
      const distance = count > 1 ? Math.max(SEAT_DISTANCE, 31 / Math.sin(Math.PI / count)) : SEAT_DISTANCE;
      const start = -Math.PI / 2 + (count % 2 ? 0 : Math.PI / count);
      return Array.from({ length: count }, (_, i) => ({ dx: Math.cos(start + i * Math.PI * 2 / count) * distance, dy: Math.sin(start + i * Math.PI * 2 / count) * distance, distance }));
    }
    function reseat() {
      positions.forEach(point => {
        const lead = point.parent && positions.get(point.parent);
        if (lead) { point.x = lead.x + point.dx; point.y = lead.y + point.dy; point.fixed = false; }
      });
    }
    // Everything surrounds the orchestrator: one ring (a tall oval in a narrow
    // panel) with each group side by side on it. Manual pins still win.
    function addPositions(agents) {
      const rect = { width: root.clientWidth, height: Math.max(1, root.clientHeight - (notice.hidden ? 0 : notice.offsetHeight)) };
      if (rect.width <= 0 || viewport.clientWidth <= 0 || viewport.clientHeight <= 0) return;
      const compact = arrangement === 'compact';
      const aspect = Math.round(clamp(rect.width / rect.height, 1, 1.3) * 20) / 20;
      const signature = JSON.stringify([arrangement, compact ? [Math.round(rect.width), Math.round(rect.height)] : aspect, hubName, agents.map(a => [a.name, a.group, a.parent]).sort()]);
      if (signature === layoutSignature) return;
      layoutSignature = signature;
      stopLayout();
      const byAgent = new Map(agents.map(a => [a.name, a]));
      const isSeat = a => { const lead = a.parent && a.parent !== a.name && a.parent !== hubName && byAgent.get(a.parent); return !!lead && !(lead.parent && byAgent.has(lead.parent)); };
      const seatsOf = new Map();
      agents.filter(isSeat).sort((a, b) => a.name.localeCompare(b.name)).forEach(a => { if (!seatsOf.has(a.parent)) seatsOf.set(a.parent, []); seatsOf.get(a.parent).push(a); });
      const ring = agents.filter(a => a.name !== hubName && !isSeat(a));
      const keys = Array.from(new Set(ring.map(a => a.group)));
      const order = ['decide', 'build', 'verify', 'understand'];
      const lead = keys.filter(key => !order.includes(key)).sort((a, b) => Number(b === 'council') - Number(a === 'council') || String(a).localeCompare(String(b)));
      const slot = compact ? 100 : SLOT;
      const groups = [...lead, ...order.filter(key => keys.includes(key))].map(key => {
        const units = ring.filter(a => a.group === key).sort((a, b) => a.name.localeCompare(b.name)).map(agent => {
          const seats = seatsOf.get(agent.name) || [], offsets = seatOffsets(seats.length);
          return { agent, seats, offsets, width: seats.length ? Math.max(slot, 2 * (offsets[0].distance + 19) + 34) : slot };
        });
        return { key, units, width: units.reduce((sum, unit) => sum + unit.width, 0) };
      });
      // With nothing to sit between them at the top, keep a gap there so the
      // first and last groups do not close up.
      if (!lead.length && groups.length > 2) groups.unshift({ key: '', units: [], width: slot });
      const depth = Math.max(0, ...groups.flatMap(group => group.units.map(unit => unit.seats.length ? unit.offsets[0].distance + 19 : 0)));
      const filled = groups.reduce((sum, group) => sum + group.width, 0), needed = filled + groups.length * GROUP_GAP;
      const least = 38 + (depth ? depth + 64 : 104);
      const grow = fits => { let low = least, high = least + needed; for (let i = 0; i < 32; i++) { const middle = (low + high) / 2; if (fits(middle)) high = middle; else low = middle; } return high; };
      let rx, ry;
      if (compact) {
        rx = Math.max(60, rect.width / 2 - 23 - EDGE);
        ry = Math.max(least, rx / 1.3);
        if (perimeter(rx, ry) < needed) ry = grow(value => perimeter(rx, value) >= needed);
        // A tall panel is used, not left empty: the oval grows into spare height.
        ry = Math.max(ry, Math.min(rx * 2.2, rect.height / 2 - Math.max(depth + 34, 96)));
      } else {
        ry = perimeter(aspect * least, least) < needed ? grow(value => perimeter(aspect * value, value) >= needed) : least;
        rx = aspect * ry;
      }
      const path = ellipse(rx, ry), spread = clamp((path.length - groups.length * GROUP_GAP) / (filled || 1), 1, 1.25);
      groups.forEach(group => { group.units.forEach(unit => { unit.width *= spread; }); group.width *= spread; });
      const gap = groups.length ? (path.length - filled * spread) / groups.length : 0;
      const seeds = new Map();
      let cursor = groups.length ? -groups[0].width / 2 : 0;
      groups.forEach(group => {
        group.units.forEach(unit => {
          const point = path.at(cursor + unit.width / 2);
          cursor += unit.width;
          seeds.set(unit.agent.name, { x: point.x, y: point.y, r: unit.seats.length ? 29 : 23, reach: unit.seats.length ? unit.offsets[0].distance + 19 : 23, group: unit.agent.group, parent: '', dx: 0, dy: 0 });
          unit.seats.forEach((seat, i) => seeds.set(seat.name, { x: 0, y: 0, r: 19, reach: 19, group: seat.group, parent: unit.agent.name, dx: unit.offsets[i].dx, dy: unit.offsets[i].dy }));
        });
        cursor += gap;
      });
      const hub = byAgent.get(hubName);
      if (hub) seeds.set(hub.name, { x: 0, y: 0, r: 38, reach: 38, group: hub.group, parent: '', dx: 0, dy: 0 });
      const targets = Array.from(seeds, ([name, seed]) => {
        const old = positions.get(name), pinned = !!old?.fixed && !seed.parent;
        return { name, ...seed, ...(pinned ? { x: old.x, y: old.y } : {}), pinned, still: pinned || name === hubName };
      });
      const bodies = targets.filter(target => !target.parent), limit = body => Math.max(0, rect.width / 2 - body.reach - EDGE);
      for (let iteration = 0; iteration < 80; iteration++) {
        let moved = false;
        for (let i = 0; i < bodies.length; i++) for (let j = i + 1; j < bodies.length; j++) {
          const a = bodies[i], b = bodies[j], room = a.reach + b.reach + 18;
          if (a.still && b.still) continue;
          let dx = b.x - a.x, dy = b.y - a.y, distance = Math.hypot(dx, dy);
          if (distance >= room) continue;
          if (distance < .001) { dx = 0; dy = 1; distance = 1; }
          const push = (room - distance + .1) * (!a.still && !b.still ? .5 : 1);
          if (!a.still) { a.x -= dx / distance * push; a.y -= dy / distance * push; }
          if (!b.still) { b.x += dx / distance * push; b.y += dy / distance * push; }
          moved = true;
        }
        if (compact) bodies.forEach(body => { if (!body.still) body.x = clamp(body.x, -limit(body), limit(body)); });
        if (!moved) break;
      }
      const placed = new Map(bodies.map(body => [body.name, body]));
      targets.forEach(target => {
        if (target.parent) { const owner = placed.get(target.parent); target.x = owner.x + target.dx; target.y = owner.y + target.dy; }
        const point = positions.get(target.name) || {};
        positions.set(target.name, Object.assign(point, { x: target.x, y: target.y, r: target.r, group: target.group, parent: target.parent, dx: target.dx, dy: target.dy, fixed: target.pinned }));
      });
    }
    function indexSteps() {
      agentsByName = new Map(snapshot.agents.map(a => [a.name, a]));
      const sessionOwners = new Map();
      stepsByAgent = new Map(snapshot.agents.map(a => [a.name, []])); nodeOwners = new Map();
      stepsById = new Map();
      snapshot.agents.forEach(a => a.sessions.forEach(s => sessionOwners.set(s.id, a.name)));
      (snapshot.graph?.nodes || []).forEach(node => {
        stepsById.set(node.id, node);
        const owner = sessionOwners.get(node.session) || (agentsByName.has(node.agent) ? node.agent : null);
        if (owner) { stepsByAgent.get(owner).push(node); nodeOwners.set(node.id, owner); }
      });
    }
    function stepsFor(agent) {
      return stepsByAgent.get(agent.name) || [];
    }
    function busySteps(agent) { return stepsFor(agent).filter(n => n.busy); }
    function sessionToOpen(agent) {
      if (typeof options.onOpenSession !== 'function' || !agent?.busy) return '';
      const own = agent.name === hubName;
      const pool = own ? visibleAgents.filter(other => other.name !== hubName).flatMap(other => other.sessions) : agent.sessions;
      const newest = pool.filter(session => session.busy && session.id !== snapshot?.root?.id).reduce((latest, session) => !latest || Number(session.updated || session.created || 0) > Number(latest.updated || latest.created || 0) ? session : latest, null);
      return newest?.id || (own && snapshot?.root?.id) || '';
    }
    function activateAgent(name, origin) { select('agent', name, origin); }
    // Opening a session is the user's explicit choice, made from the details. The graph stays where it is.
    function openSession(name) {
      const sessionID = sessionToOpen(agentsByName.get(name));
      if (!sessionID) return;
      try { Promise.resolve(options.onOpenSession({ agent: name, sessionID })).catch(() => {}); } catch (_) { /* the details stay open either way */ }
    }
    function cells(parent, pairs) {
      const grid = el('div', 'ag-stats');
      pairs.forEach(([label, value]) => { const cell = el('div', 'ag-stat'); cell.append(el('span', 'ag-stat-label', label), el('span', 'ag-stat-value', value)); grid.append(cell); });
      parent.append(grid);
    }
    function notes(parent, list) { list.forEach(note => { const row = el('p', 'ag-note', text(note.text)); row.title = text(note.text); parent.append(row); }); }
    function positionNode(name) {
      const node = nodes.get(name), p = positions.get(name);
      if (node && p) node.setAttribute('transform', 'translate(' + p.x + ' ' + p.y + ')');
    }
    function moveNode(name, x, y) {
      let point = positions.get(name);
      if (!point) return;
      if (point.parent && positions.has(point.parent)) {
        const lead = positions.get(point.parent);
        x = lead.x + x - point.x; y = lead.y + y - point.y; name = point.parent; point = lead;
      }
      if (arrangement === 'compact') {
        const low = (point.r + EDGE - camera.x) / camera.scale, high = (viewport.clientWidth - point.r - EDGE - camera.x) / camera.scale;
        if (high > low) x = clamp(x, low, high);
      }
      let dx = x - point.x, dy = y - point.y;
      // A lead carries its seats, so the council stays a circle while it moves.
      const moving = new Set([name]);
      positions.forEach((child, childName) => { if (child.parent === name) moving.add(childName); });
      const valid = (dx, dy) => {
        for (const key of moving) {
          const member = positions.get(key);
          for (const [otherName, other] of positions) {
            if (!moving.has(otherName) && Math.hypot(member.x + dx - other.x, member.y + dy - other.y) < member.r + other.r + 14) return false;
          }
        }
        return true;
      };
      if (!valid(dx, dy)) {
        let low = 0, high = 1;
        for (let i = 0; i < 18; i++) {
          const middle = (low + high) / 2;
          if (valid(dx * middle, dy * middle)) low = middle; else high = middle;
        }
        if (low < .0001) return;
        dx *= low; dy *= low;
      }
      moving.forEach(key => {
        const member = positions.get(key);
        member.x += dx; member.y += dy; positionNode(key); measureLabels(nodes.get(key), member);
      });
      point.fixed = true;
      updateEdges();
    }
    function measureLabels(node, point) {
      if (!node || !point) return false;
      let low = -point.r - 8, high = point.r + 8;
      ['.ag-agent-name', '.ag-agent-work'].forEach(selector => {
        const label = node.querySelector(selector);
        let measured = label.getBBox().width, shift = 0;
        if (arrangement === 'compact' && viewport.clientWidth > 0) {
          const half = viewport.clientWidth / 2, limit = Math.max(24, viewport.clientWidth - 12), original = label.textContent;
          let count = original.length;
          while (measured > limit && count > 1) {
            count = Math.max(1, Math.floor(count * limit / measured) - 1);
            label.textContent = original.slice(0, count).trim() + '…';
            measured = label.getBBox().width;
          }
          const room = Math.max(0, half - measured / 2 - 6);
          shift = clamp(point.x, -room, room) - point.x;
        }
        label.setAttribute('x', shift);
        low = Math.min(low, shift - measured / 2 - 2); high = Math.max(high, shift + measured / 2 + 2);
      });
      const changed = Math.abs((point.labelMin ?? 0) - low) > .1 || Math.abs((point.labelMax ?? 0) - high) > .1;
      point.labelMin = low; point.labelMax = high; point.labelWidth = Math.max(-low, high);
      return changed;
    }
    function renderNodes(agents) {
      let labelsChanged = false;
      const names = new Set(agents.map(a => a.name));
      nodes.forEach((node, name) => { if (!names.has(name)) { node.remove(); nodes.delete(name); } });
      positions.forEach((_, name) => { if (!names.has(name)) positions.delete(name); });
      const alerts = attentionAgents();
      const done = snapshot?.graph?.progress, share = done && done.total ? clamp(done.passed / done.total, 0, 1) : 0;
      agents.forEach(agent => {
        const p = positions.get(agent.name), busy = Number(agent.busy) || 0;
        if (!p) return;
        let node = nodes.get(agent.name);
        if (!node) {
          node = svg('g', { class: 'ag-agent', 'data-agent': agent.name, role: 'button', tabindex: 0 });
          node.style.setProperty('--ag-order', String(nodes.size)); node.style.setProperty('--ag-phase', (-(nodes.size * 1.37) % 7).toFixed(2) + 's');
          node.append(svg('circle', { class: 'ag-ping' }), svg('circle', { class: 'ag-halo' }), svg('circle', { class: 'ag-hub-wake' }), svg('circle', { class: 'ag-hub-orbit' }), svg('circle', { class: 'ag-core' }), svg('circle', { class: 'ag-hub-progress', transform: 'rotate(-90)' }), svg('circle', { class: 'ag-spark' }));
          node.append(svg('text', { class: 'ag-agent-name', 'text-anchor': 'middle' }));
          node.append(svg('text', { class: 'ag-agent-work', 'text-anchor': 'middle' }));
          node.append(svg('circle', { class: 'ag-alert', r: 4 }), svg('title'));
          nodes.set(agent.name, node); nodeLayer.append(node);
        }
        const active = busySteps(agent), isHub = agent.name === hubName;
        const work = busy && active.length ? shorten(active[0].goal || active[0].id) + (active.length > 1 ? ' +' + (active.length - 1) : '') : '';
        node.dataset.busy = String(busy); node.dataset.group = agent.group;
        node.dataset.openChat = String(!!sessionToOpen(agent));
        node.dataset.attention = String(alerts.has(agent.name));
        const mark = node.querySelector('.ag-alert'); mark.setAttribute('cx', -p.r * .72); mark.setAttribute('cy', -p.r * .72);
        node.dataset.parent = agent.parent; node.dataset.team = agent.team;
        node.querySelector('.ag-core').setAttribute('r', p.r);
        node.querySelector('.ag-halo').setAttribute('r', p.r + 8);
        node.querySelector('.ag-spark').setAttribute('r', isHub ? 6 : 4);
        const orbit = node.querySelector('.ag-hub-orbit');
        orbit.setAttribute('r', p.r + 11); orbit.dataset.active = String(busy > 0);
        const wake = node.querySelector('.ag-hub-wake'); wake.setAttribute('r', p.r + 17); wake.dataset.active = String(busy > 0);
        node.querySelector('.ag-ping').setAttribute('r', p.r);
        node.dataset.ring = String(canvasWorkLoops().some(loop => loop.agent === agent.name));
        // The orchestrator's inner arc is the run's progress: steps passed.
        const arc = node.querySelector('.ag-hub-progress'), around = 2 * Math.PI * (p.r - 6);
        arc.setAttribute('r', p.r - 6); arc.setAttribute('stroke-dasharray', (around * share) + ' ' + around);
        arc.dataset.shown = String(isHub && share > 0);
        p.labelUp = !!p.parent && p.dy < -1;
        node.querySelector('.ag-agent-name').setAttribute('y', p.labelUp ? -p.r - 8 : p.r + 20);
        node.querySelector('.ag-agent-work').setAttribute('y', p.r + 35);
        node.classList.toggle('ag-hub', isHub);
        node.classList.toggle('ag-council-lead', visibleAgents.some(a => a.parent === agent.name));
        node.querySelector('.ag-agent-name').textContent = displayName(agent);
        node.querySelector('.ag-agent-work').textContent = work;
        node.querySelector('title').textContent = agent.name;
        node.setAttribute('aria-label', agent.name + (busy ? ', working' + (work ? ', ' + work : '') : ', idle') + (alerts.has(agent.name) ? ', ' + alerts.get(agent.name) : ''));
        positionNode(agent.name);
        labelsChanged = measureLabels(node, p) || labelsChanged;
      });
      empty.hidden = agents.length > 0;
      return labelsChanged;
    }
    function updateEdges() {
      if (!snapshot) return;
      const workFlows = canvasFlows();
      const explained = new Set([...workFlows.filter(f => f.active).flatMap(f => [f.from, f.to]), ...canvasLoops().filter(loop => loop.active).flatMap(loop => [loop.worker, loop.evaluator])]);
      // Work that passes through the orchestrator travels along its spoke.
      const viaHub = new Set(flows().filter(f => f.active && (f.from === hubName || f.to === hubName)).map(f => f.from === hubName ? f.to : f.from));
      const edges = visibleAgents.filter(a => a.name !== hubName && (a.parent || hubName)).map(a => ({ from: a.parent || hubName, to: a.name, kind: 'handoff', relation: a.parent ? 'membership' : 'supervision', active: (a.busy > 0 && !explained.has(a.name)) || (!a.parent && viaHub.has(a.name)) }));
      edges.push(...snapshot.escalation.filter(e => allowedConnection(e.from, e.to) && (e.used || (selected?.type === 'agent' && (selected.key === e.from || selected.key === e.to)))).map(e => ({ ...e, kind: 'escalation', active: false })), ...workFlows);
      // A checker is linked to every agent it can check; the link carries work only while it checks a looping agent.
      const looping = new Set(canvasWorkLoops().map(loop => loop.agent));
      visibleAgents.filter(agent => agent.checks && agent.name !== hubName).forEach(checker => visibleAgents.forEach(other => {
        if (other.name !== checker.name && other.name !== hubName && !other.checks) edges.push({ from: checker.name, to: other.name, kind: 'check', active: checker.busy > 0 && looping.has(other.name) });
      }));
      const directions = new Set(workFlows.map(f => JSON.stringify([f.from, f.to])));
      const keys = new Set();
      edges.forEach(edge => {
        if (!allowedConnection(edge.from, edge.to)) return;
        const a = positions.get(edge.from), b = positions.get(edge.to);
        if (!a || !b) return;
        const key = edge.kind + ':' + edge.from + '->' + edge.to; keys.add(key);
        let pair = lines.get(key);
        if (!pair) {
          const line = svg('line', { class: 'ag-edge', 'data-edge': edge.from + '->' + edge.to, 'data-kind': edge.kind });
          const flow = svg('line', { class: 'ag-flow', 'data-flow-kind': edge.kind });
          // Pulse overlays have no semantic edge hook; each flow is one edge.
          if (edge.kind === 'data' || edge.kind === 'feedback') line.setAttribute('marker-end', 'url(#' + uid + '-' + edge.kind + ')');
          const layer = edge.kind === 'handoff' || edge.kind === 'check' ? spokeLayer : edge.kind === 'escalation' ? escalationLayer : flowLayer;
          layer.append(line, flow); pair = { line, flow, edge }; lines.set(key, pair);
        }
        pair.edge = edge;
        pair.line.dataset.relation = edge.relation || '';
        pair.line.dataset.local = String(visibleAgents.some(a => a.name === edge.from && a.group === 'build') && visibleAgents.some(a => a.name === edge.to && a.group === 'build'));
        const distance = Math.hypot(b.x - a.x, b.y - a.y) || 1;
        const ux = (b.x - a.x) / distance, uy = (b.y - a.y) / distance;
        const work = edge.kind === 'data' || edge.kind === 'feedback';
        if (work) pair.line.setAttribute('marker-end', 'url(#' + uid + '-' + edge.kind + (edge.active ? '-live' : '') + ')');
        const reverse = directions.has(JSON.stringify([edge.to, edge.from]));
        const shift = work && (reverse || edge.from === hubName || edge.to === hubName) ? 5 : 0;
        const coordinates = { x1: a.x + ux * (a.r + 5) - uy * shift, y1: a.y + uy * (a.r + 5) + ux * shift, x2: b.x - ux * (b.r + 6) - uy * shift, y2: b.y - uy * (b.r + 6) + ux * shift };
        [pair.line, pair.flow].forEach(line => Object.entries(coordinates).forEach(([key, value]) => line.setAttribute(key, value)));
        pair.line.dataset.active = String(!!edge.active);
        // A line moves only while work is travelling along it to an agent that is working.
        const moving = edge.active && edge.kind !== 'escalation' && (edge.kind !== 'feedback' || shownAgents.get(edge.to)?.busy > 0);
        pair.flow.style.display = moving ? '' : 'none';
      });
      lines.forEach((pair, key) => { if (!keys.has(key)) { pair.line.remove(); pair.flow.remove(); lines.delete(key); } });
      updateLoops();
      highlight();
    }
    function updateLoops() {
      const shown = new Set(), counted = new Map(canvasWorkLoops().filter(loop => loop.rounds >= 2).map(loop => [loop.agent, loop.rounds]));
      canvasLoops().forEach(loop => {
        const a = positions.get(loop.worker), b = positions.get(loop.evaluator);
        if (!a || !b) return;
        shown.add(loop.id);
        let view = loopViews.get(loop.id);
        if (!view) {
          const group = svg('g', { class: 'ag-loop', 'data-loop-id': loop.id, role: 'button', tabindex: 0 });
          const forward = svg('path', { class: 'ag-loop-half', 'data-loop': loop.id, 'data-loop-half': 'forward', 'marker-end': 'url(#' + uid + '-loop)' });
          const back = svg('path', { class: 'ag-loop-half', 'data-loop': loop.id, 'data-loop-half': 'return', 'marker-end': 'url(#' + uid + '-loop)' });
          const title = svg('title'); group.append(forward, back, title); loopLayer.append(group);
          view = { group, forward, back, title, count: null, loop }; loopViews.set(loop.id, view);
        }
        view.loop = loop;
        view.group.dataset.state = loop.state; view.group.dataset.active = String(!!loop.active);
        view.group.dataset.moving = String(!!loop.active && [loop.worker, loop.evaluator].some(name => shownAgents.get(name)?.busy > 0));
        view.group.setAttribute('aria-label', loop.worker + ' and ' + loop.evaluator + ', review loop, ' + loop.rounds + (loop.rounds === 1 ? ' round, ' : ' rounds, ') + named(LOOP_STATES, loop.state));
        view.title.textContent = loop.worker + ' ↔ ' + loop.evaluator;
        const geometry = reviewGeometry(a, b);
        view.geometry = geometry;
        view.forward.setAttribute('d', geometry.forward); view.back.setAttribute('d', geometry.back);
        const liveHalf = loop.state === 'checking' ? 'forward' : ['working', 'sent_back'].includes(loop.state) ? 'return' : '';
        [view.forward, view.back].forEach(path => {
          path.dataset.live = String(path.dataset.loopHalf === liveHalf);
          path.dataset.loopState = loop.state;
          path.setAttribute('marker-end', 'url(#' + uid + (loop.active && path.dataset.loopHalf === liveHalf ? '-loop-live' : '-loop') + ')');
        });
        if (loop.rounds >= 2 && counted.get(loop.worker) !== loop.rounds) {
          if (!view.count) { view.count = svg('text', { class: 'ag-loop-count', 'data-loop-count': loop.id, 'text-anchor': 'middle', 'dominant-baseline': 'central' }); view.group.append(view.count); }
          view.count.textContent = '×' + loop.rounds;
          view.count.setAttribute('x', geometry.count.x); view.count.setAttribute('y', geometry.count.y);
        } else if (view.count) { view.count.remove(); view.count = null; }
      });
      loopViews.forEach((view, id) => { if (!shown.has(id)) { view.group.remove(); loopViews.delete(id); } });
      updateWorkLoops();
    }
    function updateWorkLoops() {
      const shown = new Set();
      canvasWorkLoops().forEach(loop => {
        const p = positions.get(loop.agent);
        if (!p) return;
        shown.add(loop.agent);
        let view = workLoopViews.get(loop.agent);
        if (!view) {
          const group = svg('g', { class: 'ag-work-loop', 'data-work-loop-id': loop.agent, role: 'button', tabindex: 0 });
          const ring = svg('path', { class: 'ag-work-loop-edge', 'data-work-loop': loop.agent });
          const title = svg('title'); group.append(ring, title); loopLayer.append(group);
          view = { group, ring, title, count: null, loop }; workLoopViews.set(loop.agent, view);
        }
        view.loop = loop;
        view.group.dataset.loopState = loop.state; view.group.dataset.active = String(!!loop.active);
        view.group.dataset.moving = String(!!loop.active && shownAgents.get(loop.agent)?.busy > 0);
        view.group.dataset.quiet = String(['closed', 'idle'].includes(loop.state) && !shownAgents.get(loop.agent)?.busy);
        view.ring.dataset.loopState = loop.state; view.ring.dataset.active = String(!!loop.active);
        const radius = p.r + 9, open = loop.rounds >= 2 ? .46 : .17, start = -Math.PI / 4 + open, end = -Math.PI / 4 - open;
        view.ring.setAttribute('d', 'M ' + (p.x + radius * Math.cos(start)) + ' ' + (p.y + radius * Math.sin(start)) + ' A ' + radius + ' ' + radius + ' 0 1 1 ' + (p.x + radius * Math.cos(end)) + ' ' + (p.y + radius * Math.sin(end)));
        view.ring.setAttribute('marker-end', 'url(#' + uid + (loop.state === 'stuck' ? '-work-stuck' : loop.state === 'closed' ? '-work-closed' : loop.state === 'working' && loop.active ? '-work-live' : '-work') + ')');
        view.group.setAttribute('aria-label', loop.agent + ', work loop, ' + loop.rounds + (loop.rounds === 1 ? ' round, ' : ' rounds, ') + named(WORK_LOOP_STATES, loop.state));
        view.title.textContent = loop.agent;
        if (loop.rounds >= 2) {
          if (!view.count) { view.count = svg('text', { class: 'ag-work-loop-count', 'data-work-loop-count': loop.agent, 'text-anchor': 'middle', 'dominant-baseline': 'central' }); view.group.append(view.count); }
          view.count.textContent = '×' + loop.rounds;
          view.count.setAttribute('x', p.x + (radius + 1) * Math.SQRT1_2); view.count.setAttribute('y', p.y - (radius + 1) * Math.SQRT1_2);
        } else if (view.count) { view.count.remove(); view.count = null; }
      });
      workLoopViews.forEach((view, agent) => { if (!shown.has(agent)) { view.group.remove(); workLoopViews.delete(agent); } });
    }
    function highlight() {
      nodes.forEach((node, key) => { const active = !!selected && ['agent', 'work-loop'].includes(selected.type) && selected.key === key; node.dataset.selected = String(active); node.setAttribute('aria-pressed', String(active)); });
      const selecting = !!selected && ['agent', 'work-loop'].includes(selected.type);
      edgeLayer.dataset.selecting = String(selecting);
      lines.forEach(pair => {
        const related = selecting && (pair.edge.from === selected.key || pair.edge.to === selected.key);
        pair.line.dataset.related = String(!!related); pair.flow.dataset.related = String(!!related);
      });
      loopLayer.dataset.selecting = String(!!selected && selected.type !== 'run');
      loopViews.forEach((view, id) => {
        const related = selected?.type === 'loop' ? selected.key === id : selecting && [view.loop.worker, view.loop.evaluator].includes(selected.key);
        view.group.dataset.related = String(!!related);
        view.group.setAttribute('aria-pressed', String(selected?.type === 'loop' && selected.key === id));
      });
      workLoopViews.forEach((view, agent) => {
        const review = selected?.type === 'loop' ? loopViews.get(selected.key)?.loop : null;
        const related = selecting ? selected.key === agent : !!review && [review.worker, review.evaluator].includes(agent);
        view.group.dataset.related = String(related); view.group.setAttribute('aria-pressed', String(selected?.type === 'work-loop' && selected.key === agent));
      });
    }

    function select(type, key, origin, step) {
      const agent = type === 'agent' && agentsByName.get(key);
      if (type === 'agent' && !agent) return;
      if (agent && agent.team !== activeTeam) switchTeam(agent.team);
      selected = { type, key }; selectedOrigin = origin || selectedOrigin; highlightedStep = step || '';
      if (step) openSteps.add(step);
      detailSignature = ''; detail.hidden = false; root.classList.add('ag-details-open'); renderDetail(); updateEdges(); scheduleResize();
      close.focus({ preventScroll: true });
      if (step) Array.from(detailScroll.querySelectorAll('[data-step]')).find(row => row.dataset.step === step)?.scrollIntoView({ block: 'nearest' });
    }
    function closeDetails() {
      selected = null; highlightedStep = ''; detail.hidden = true; root.classList.remove('ag-details-open'); detailSignature = ''; updateEdges(); scheduleResize();
      if (selectedOrigin && selectedOrigin.isConnected) selectedOrigin.focus({ preventScroll: true });
      else viewport.focus({ preventScroll: true });
    }
    function appendList(parent, items, draw) {
      items.slice(0, LIST_LIMIT).forEach(draw);
      if (items.length > LIST_LIMIT) parent.append(el('p', 'ag-list-more', '+' + (items.length - LIST_LIMIT) + ' more'));
    }
    function listText(items, format = text, separator = ' · ') {
      return items.slice(0, LIST_LIMIT).map(format).join(separator) + (items.length > LIST_LIMIT ? '\n+' + (items.length - LIST_LIMIT) + ' more' : '');
    }
    function section(parent, label, value, code, format = text) {
      const item = el('section', 'ag-detail-section'); item.append(el('h3', '', label));
      if (Array.isArray(value)) { const list = el('ul'); item.append(list); appendList(item, value, v => list.append(el('li', '', format(v)))); }
      else if (value !== undefined) { const p = el('p'); p.append(el(code ? 'code' : 'span', '', text(value) || '—')); item.append(p); }
      parent.append(item); return item;
    }
    function flowSection(agent, incoming, relevant) {
      const list = relevant.filter(f => (incoming ? f.to : f.from) === agent.name);
      const container = section(detailScroll, incoming ? 'Receives from' : 'Hands to');
      if (!list.length) { container.append(el('p', 'ag-flow-empty', 'None')); return; }
      appendList(container, list, flow => {
        const peer = incoming ? flow.from : flow.to;
        const current = shownAgents.has(peer);
        const row = el(current ? 'button' : 'div', 'ag-flow-row');
        if (current) { row.type = 'button'; row.dataset.flowPeer = peer; }
        else row.dataset.pastAgent = '';
        const heading = el('span', 'ag-flow-heading');
        heading.append(el('span', 'ag-flow-peer', peer + (current ? '' : ' · past agent')), el('span', 'ag-flow-type', flow.kind === 'feedback' ? 'Feedback' : 'Work'));
        row.dataset.kind = flow.kind; row.dataset.active = String(!!flow.active);
        const pairs = listText(flow.pairs, pair => listText(pair, text, ' → '));
        const artifacts = flow.artifacts.slice(0, 3).map(a => shorten(a)).join(', ');
        row.append(heading, el('span', 'ag-flow-meta', pairs));
        if (artifacts) row.append(el('span', 'ag-flow-artifacts', artifacts + (flow.artifacts.length > 3 ? ' +' + (flow.artifacts.length - 3) + ' more' : '')));
        row.title = peer + ': ' + pairs + (flow.artifacts.length ? '\nArtifacts: ' + listText(flow.artifacts, text, ', ') : '');
        container.append(row);
      });
    }
    function stepRow(parent, step, compact = false) {
      const row = el('details', 'ag-step' + (compact ? ' ag-step-compact' : '')); row.dataset.step = step.id; row.dataset.status = step.status;
      row.dataset.highlighted = String(step.id === highlightedStep); row.open = openSteps.has(step.id);
      const summary = el('summary');
      const meta = el('span', 'ag-step-meta'); meta.append(el('span', '', step.id), el('span', 'ag-status', state(step.status)));
      summary.append(meta, el('span', 'ag-step-goal', step.goal || step.id)); row.append(summary);
      let built = false;
      const build = () => {
        if (built) return;
        built = true;
        const body = el('div', 'ag-step-body');
        body.append(el('p', 'ag-loop-meta', (step.attempts || 0) + ((step.attempts || 0) === 1 ? ' try' : ' tries') + (step.stalled ? ' · Stalled' : '') + (step.pattern ? ' · ' + step.pattern : '')));
        section(body, 'Passes when', step.done_when, true);
        section(body, 'Agent trail', step.tiers?.length ? step.tiers : step.agent);
        section(body, 'Baseline', step.baseline);
        section(body, 'Latest evidence', step.evidence);
        if (step.failure_kind) section(body, 'Failure kind', step.failure_kind);
        if (step.verdict) section(body, 'Review verdict', step.verdict);
        if (step.outputs) section(body, 'Outputs', step.outputs);
        const artifactText = artifact => typeof artifact === 'string' ? artifact : [artifact.id || artifact.artifact || artifact.name || artifact.path, artifact.revision != null ? 'r' + artifact.revision : '', artifact.from ? 'from ' + artifact.from : '', artifact.stale ? 'stale' : ''].filter(Boolean).join(' · ');
        if (step.artifacts?.length) section(body, 'Artifacts', step.artifacts, false, artifactText);
        if (step.inputs?.length) section(body, 'Inputs', step.inputs, false, artifactText);
        if (step.changed_paths?.length) section(body, 'Changed files', step.changed_paths);
        if (step.scope?.length) section(body, 'Scope', step.scope);
        if (step.depends_on?.length) section(body, 'Depends on', step.depends_on);
        row.append(body);
      };
      lazySteps.set(row, build);
      if (row.open) build();
      parent.append(row);
    }
    function runSection(label, key) {
      const item = section(detailScroll, label);
      item.classList.add('ag-run-section'); item.dataset.runSection = key;
      return item;
    }
    function runRow(parent, item) {
      const actionable = item.step || item.review || item.work;
      const row = el(actionable ? 'button' : 'div', 'ag-run-item');
      if (actionable) row.type = 'button';
      if (item.step) row.dataset.runStep = item.step;
      if (item.review) row.dataset.runReview = item.review;
      if (item.work) row.dataset.runWork = item.work;
      const copy = el('span', 'ag-run-item-copy');
      copy.append(el('span', 'ag-run-item-title', item.title));
      if (item.detail) copy.append(el('span', 'ag-run-item-detail', item.detail));
      row.append(copy, el('span', 'ag-run-item-meta', item.meta || ''));
      row.title = [item.title, item.detail, item.meta].filter(Boolean).join(' · ');
      parent.append(row);
    }
    function runFold(label, key, count, build) {
      const row = el('details', 'ag-run-fold'); row.dataset.runFold = key; row.open = openFolds.has(key);
      const summary = el('summary'); summary.append(el('span', '', label), el('span', 'ag-fold-count', String(count)));
      row.append(summary);
      let built = false;
      const fill = () => {
        if (built) return;
        built = true; const body = el('div', 'ag-fold-body'); build(body); row.append(body);
      };
      lazySteps.set(row, fill); if (row.open) fill(); detailScroll.append(row);
    }
    function renderRunDetail(graph) {
      eyebrow.textContent = '';
      detailScroll.append(el('h2', 'ag-detail-title ag-run-title', 'Run'));
      if (teamChoices.length > 1) {
        const field = el('label', 'ag-run-team'), choice = el('select', 'ag-team-select'); choice.dataset.runTeam = ''; choice.dataset.teamSwitch = '';
        choice.setAttribute('aria-label', 'Team');
        teamChoices.slice(0, LIST_LIMIT).forEach(option => { const item = el('option', '', option.label); item.value = option.team; choice.append(item); });
        choice.value = activeTeam; field.append(el('span', '', 'Team'), choice); detailScroll.append(field);
      }
      stats(detailScroll, { cost: snapshot.totals?.cost, sent: snapshot.totals?.tokens_in, received: snapshot.totals?.tokens_out, context: snapshot.root?.context || 0 });
      const learning = snapshot.learning;
      if (learning) {
        const lesson = runSection('Learning', 'learning');
        const line = el('p', 'ag-learning-line', [learning.total + (learning.total === 1 ? ' note' : ' notes'), learning.pending ? learning.pending + ' new' : '', learning.due ? 'change due' : '', learning.improvements ? learning.improvements + ' applied' : ''].filter(Boolean).join(' · '));
        line.dataset.due = String(!!learning.due); lesson.append(line);
        notes(lesson, (learning.notes || []).slice(0, 3));
      }
      if (graph) {
        const progressLine = el('div', 'ag-run-progress');
        progressLine.append(el('strong', 'ag-run-number', graph.progress.passed + ' / ' + graph.progress.total), el('span', '', 'steps passed'), el('span', 'ag-run-state', graph.status === 'passed' ? 'Complete' : state(graph.status)));
        detailScroll.append(progressLine);
        const notices = graph.problems.length + snapshot.warnings.length;
        if (notices) { const link = el('button', 'ag-text-button ag-run-notices', notices + (notices === 1 ? ' notice' : ' notices')); link.type = 'button'; link.dataset.runOpenFold = 'advice'; detailScroll.append(link); }

        const running = runSection('Working now', 'working');
        const busy = graph.nodes.filter(node => node.busy), busyAgents = new Set(busy.map(node => nodeOwners.get(node.id) || node.agent));
        const live = busy.map(node => ({ title: nodeOwners.get(node.id) || node.agent, detail: node.goal, meta: node.id, step: node.id }));
        visibleAgents.filter(agent => agent.busy && !busyAgents.has(agent.name)).forEach(agent => live.push({ title: agent.name, meta: 'Working' }));
        if (!live.length) running.append(el('p', 'ag-run-empty', 'None'));
        appendList(running, live, item => runRow(running, item));

        const attention = runSection('Needs attention', 'attention');
        const trouble = graph.nodes.filter(node => ['failed', 'blocked'].includes(node.status) || node.stalled || node.verdict === 'refuted');
        const troubleIDs = new Set(trouble.map(node => node.id));
        const issues = trouble.map(node => ({ title: nodeOwners.get(node.id) || node.agent, detail: node.goal, meta: node.stalled ? 'Stuck' : node.verdict === 'refuted' ? 'Sent back' : state(node.status), step: node.id }));
        canvasLoops().filter(loop => loop.state === 'sent_back' && !loop.pairs.some(pair => troubleIDs.has(pair[1]))).forEach(loop => issues.push({ title: loop.worker, detail: loop.evaluator, meta: 'Sent back · ' + loop.rounds, review: loop.id }));
        canvasWorkLoops().filter(loop => loop.state === 'stuck' && !loop.nodes.some(id => troubleIDs.has(id))).forEach(loop => issues.push({ title: loop.agent, detail: 'Work loop', meta: 'Stuck · ' + loop.rounds, work: loop.agent }));
        if (!issues.length) attention.append(el('p', 'ag-run-empty', 'None'));
        appendList(attention, issues, item => runRow(attention, item));

        const spend = runSection('Spend', 'spend');
        const spenders = visibleAgents.map(agent => ({ name: agent.name, used: usageOf(agent.sessions) })).filter(item => item.used.cost > 0).sort((a, b) => b.used.cost - a.used.cost);
        const most = spenders[0]?.used.cost || 1;
        if (!spenders.length) spend.append(el('p', 'ag-run-empty', 'None'));
        appendList(spend, spenders, item => {
          const row = el('button', 'ag-spend-row'); row.type = 'button'; row.dataset.flowPeer = item.name;
          const bar = el('span', 'ag-spend-bar'), fill = el('i'); fill.style.width = Math.max(2, item.used.cost / most * 100) + '%'; bar.append(fill);
          row.append(el('span', 'ag-spend-name', item.name), bar, el('span', 'ag-spend-cost', cost(item.used.cost))); spend.append(row);
        });

        const circuits = runSection('Loops', 'loops');
        const loopItems = [...canvasWorkLoops().map(loop => ({ title: loop.agent, detail: 'Work · ' + named(WORK_LOOP_STATES, loop.state), meta: loop.rounds + (loop.rounds === 1 ? ' round' : ' rounds'), work: loop.agent, active: loop.active })), ...canvasLoops().map(loop => ({ title: loop.worker + ' ↔ ' + loop.evaluator, detail: 'Review · ' + named(LOOP_STATES, loop.state), meta: loop.rounds + (loop.rounds === 1 ? ' round' : ' rounds'), review: loop.id, active: loop.active }))].sort((a, b) => Number(b.active) - Number(a.active));
        if (!loopItems.length) circuits.append(el('p', 'ag-run-empty', 'None'));
        appendList(circuits, loopItems, item => runRow(circuits, item));

        const objectives = runSection('Objectives', 'objectives'), list = el('ul', 'ag-objectives'); objectives.append(list);
        if (!graph.objectives.length) objectives.append(el('p', 'ag-run-empty', 'None'));
        appendList(objectives, graph.objectives, objective => {
          const item = el('li', 'ag-objective'); item.dataset.complete = String(!!objective.delivered);
          item.setAttribute('aria-label', (objective.delivered ? 'Complete: ' : 'Not complete: ') + objective.text);
          item.append(el('span', 'ag-objective-mark', objective.delivered ? '✓' : '○'), el('span', '', objective.text)); list.append(item);
        });

        const steps = runSection('Steps', 'steps');
        const states = ['In progress', 'Ready for review', 'Needs attention', 'Waiting', 'Passed'];
        const groupOf = node => node.status === 'running' ? states[0] : node.status === 'returned' ? states[1] : ['failed', 'blocked'].includes(node.status) ? states[2] : node.status === 'passed' ? states[4] : states[3];
        const ordered = [...graph.nodes].sort((a, b) => states.indexOf(groupOf(a)) - states.indexOf(groupOf(b)));
        const highlighted = ordered.findIndex(node => node.id === highlightedStep);
        if (highlighted >= LIST_LIMIT) ordered.unshift(...ordered.splice(highlighted, 1));
        const groups = new Map();
        if (!ordered.length) steps.append(el('p', 'ag-run-empty', 'None'));
        appendList(steps, ordered, node => {
          const key = groupOf(node);
          if (!groups.has(key)) { const group = el('div', 'ag-step-group'); group.dataset.state = key; const heading = el('h4', '', key); heading.append(el('span', '', String(graph.nodes.filter(n => groupOf(n) === key).length))); group.append(heading); steps.append(group); groups.set(key, group); }
          stepRow(groups.get(key), node, true);
        });
      }
      runFold('History', 'history', graph?.events.length || 0, body => section(body, 'Recent activity', graph?.events.length ? [...graph.events].reverse() : 'None', false, event => [event.node, event.event, text(event.note)].filter(Boolean).join(' · ')));
      runFold('Amendments', 'amendments', graph?.amendments.length || 0, body => section(body, 'Amendments', graph?.amendments.length ? graph.amendments : 'None'));
      runFold('Lessons', 'lessons', graph?.lessons.length || 0, body => section(body, 'Lessons', graph?.lessons.length ? graph.lessons : 'None'));
      runFold('Advice', 'advice', (graph?.advice.length || 0) + (graph?.problems.length || 0) + snapshot.warnings.length, body => {
        if (graph?.advice.length) section(body, 'Graph notes', graph.advice);
        section(body, 'Validation', graph?.problems.length ? graph.problems : 'None');
        section(body, 'Warnings', snapshot.warnings.length ? snapshot.warnings : 'None');
        section(body, 'Usage', snapshot.totals.sessions + ' sessions · ' + cost(snapshot.totals.cost) + '\n' + snapshot.totals.tokens_in.toLocaleString() + ' input tokens · ' + snapshot.totals.tokens_out.toLocaleString() + ' output tokens');
      });
    }
    function renderDetail() {
      if (!selected || !snapshot) return;
      const graph = snapshot.graph;
      const agent = selected.type === 'agent' ? agentsByName.get(selected.key) : null;
      const steps = agent ? stepsFor(agent) : [];
      const relevant = agent ? flows().filter(f => f.from === agent.name || f.to === agent.name) : [];
      const loop = selected.type === 'loop' ? loops().find(item => item.id === selected.key) : null;
      const workLoop = selected.type === 'work-loop' ? workLoops().find(item => item.agent === selected.key) : null;
      // Do not serialize a whole graph (including unopened step lists) merely
      // to detect refreshes. Only the bounded, visible detail content is built.
      const signature = renderRevision + ':' + selected.type + ':' + selected.key + ':' + highlightedStep;
      if (detailSignature === signature) return;
      detailSignature = signature;
      const scroll = detailScroll.scrollTop;
      const focused = document.activeElement;
      const focusedStep = detailScroll.contains(focused) ? focused.closest('[data-step]')?.dataset.step : null;
      detailScroll.replaceChildren();
      if (selected.type === 'work-loop') {
        eyebrow.textContent = 'Loop';
        detailScroll.append(el('h2', 'ag-detail-title', workLoop ? workLoop.agent : 'Unavailable'));
        if (workLoop) {
          detailScroll.append(el('p', 'ag-detail-intro', named(WORK_LOOP_STATES, workLoop.state) + ' · ' + workLoop.rounds + (workLoop.rounds === 1 ? ' round' : ' rounds')));
          section(detailScroll, 'Agent', workLoop.agent);
          const steps = section(detailScroll, 'Steps in this loop');
          if (!workLoop.nodes.length) steps.append(el('p', '', 'None'));
          appendList(steps, workLoop.nodes, id => {
            const step = stepsById.get(id);
            if (step) stepRow(steps, step); else steps.append(el('p', '', id));
          });
        }
      } else if (selected.type === 'loop') {
        eyebrow.textContent = 'Review';
        detailScroll.append(el('h2', 'ag-detail-title', loop ? loop.worker + ' ↔ ' + loop.evaluator : 'Unavailable'));
        if (loop) {
          detailScroll.append(el('p', 'ag-detail-intro', named(LOOP_STATES, loop.state) + ' · ' + loop.rounds + (loop.rounds === 1 ? ' round' : ' rounds')));
          section(detailScroll, 'Worker', loop.worker);
          section(detailScroll, 'Evaluator', loop.evaluator);
          section(detailScroll, 'Step pairs', loop.pairs, false, pair => pair[0] + ' → ' + pair[1] + ' → ' + pair[0]);
          section(detailScroll, 'Artifacts sent back', loop.artifacts.length ? loop.artifacts : 'None');
        }
      } else if (selected.type === 'agent') {
        eyebrow.textContent = 'Agent';
        if (!agent) { detailScroll.append(el('h2', 'ag-detail-title', 'Unavailable')); return; }
        detailScroll.append(el('h2', 'ag-detail-title', agent.name), el('p', 'ag-detail-intro', agent.model || '—'));
        const badges = el('div', 'ag-detail-badges');
        badges.append(el('span', 'ag-badge', agent.busy ? agent.busy + ' active' : 'Idle'));
        if (agent.effort) badges.append(el('span', 'ag-badge', agent.effort + ' effort'));
        if (agent.pool) badges.append(el('span', 'ag-badge', 'Pool of ' + agent.pool));
        detailScroll.append(badges);
        stats(detailScroll, usageOf(agent.sessions));
        if (sessionToOpen(agent)) { const opener = el('button', 'ag-open-session', 'Open session'); opener.type = 'button'; opener.dataset.openSession = agent.name; detailScroll.append(opener); }
        const record = snapshot.learning?.agents && Object.hasOwn(snapshot.learning.agents, agent.name) ? snapshot.learning.agents[agent.name] : null;
        if (record) {
          const past = section(detailScroll, 'Track record');
          cells(past, [['Steps', text(record.steps)], ['First pass', record.first_pass_rate == null ? '—' : Math.round(record.first_pass_rate * 100) + '%'], ['Rounds', record.mean_attempts == null ? '—' : Number(record.mean_attempts).toFixed(1)]]);
        }
        const children = snapshot.agents.filter(a => a.parent === agent.name);
        if (children.length || agent.parent) {
          const family = section(detailScroll, agent.parent ? 'Council lead' : 'Council seats');
          appendList(family, agent.parent ? snapshot.agents.filter(a => a.name === agent.parent) : children, a => {
            const link = el('button', 'ag-family-link', a.name); link.type = 'button'; link.dataset.flowPeer = a.name; family.append(link);
          });
        }
        flowSection(agent, true, relevant); flowSection(agent, false, relevant);
        const work = section(detailScroll, 'Steps in this run' + (steps.length ? ' · ' + steps.length : ''));
        if (!steps.length) work.append(el('p', '', 'None'));
        appendList(work, steps, step => stepRow(work, step));
        const own = new Set(steps.map(step => step.id)), happened = (graph?.events || []).filter(event => own.has(event.node)).slice(-12).reverse();
        if (happened.length) {
          const history = section(detailScroll, 'History');
          happened.forEach(event => { const row = el('div', 'ag-history-row'); row.append(el('span', 'ag-history-when', ago(event.time)), el('span', 'ag-history-what', event.node + ' ' + event.event), el('span', 'ag-history-note', text(event.note))); row.title = text(event.note); history.append(row); });
        }
        const said = (snapshot.learning?.notes || []).filter(note => note.agent === agent.name).slice(0, 5);
        if (said.length) notes(section(detailScroll, 'From you'), said);
        const sessions = section(detailScroll, 'Sessions · ' + agent.sessions.length);
        if (!agent.sessions.length) sessions.append(el('p', '', 'None'));
        appendList(sessions, agent.sessions, session => {
          const row = el('div', 'ag-session');
          row.append(el('div', 'ag-session-title', (session.busy ? '● Active · ' : '') + session.title));
          row.append(el('div', 'ag-session-meta', [session.model, session.effort ? session.effort + ' effort' : '', cost(session.cost), amount(session.tokens_in) + '↑ ' + amount(session.tokens_out) + '↓', session.context ? 'ctx ' + amount(session.context) : ''].filter(Boolean).join(' · ')));
          row.append(el('div', 'ag-session-meta', session.id)); sessions.append(row);
        });
      } else {
        renderRunDetail(graph);
      }
      detailScroll.scrollTop = scroll;
      if (focusedStep) Array.from(detailScroll.querySelectorAll('[data-step]')).find(row => row.dataset.step === focusedStep)?.querySelector('summary').focus({ preventScroll: true });
    }

    listen(close, 'click', closeDetails);
    listen(runDetails, 'click', () => select('run', '', runDetails));
    listen(usage, 'click', () => select('run', '', usage));
    listen(attention, 'click', () => { select('run', '', attention); detailScroll.querySelector('[data-run-section="attention"]')?.scrollIntoView({ block: 'start' }); });
    listen(detailScroll, 'change', event => { if (event.target.matches('[data-run-team]')) switchTeam(event.target.value); });
    listen(detailScroll, 'click', event => {
      const stepLink = event.target.closest('[data-run-step]');
      if (stepLink) { const node = stepsById.get(stepLink.dataset.runStep), owner = node && nodeOwners.get(node.id); if (node) select(owner ? 'agent' : 'run', owner || '', nodes.get(owner) || runDetails, node.id); return; }
      const reviewLink = event.target.closest('[data-run-review]');
      if (reviewLink) { select('loop', reviewLink.dataset.runReview, loopViews.get(reviewLink.dataset.runReview)?.group); return; }
      const workLink = event.target.closest('[data-run-work]');
      if (workLink) { select('work-loop', workLink.dataset.runWork, workLoopViews.get(workLink.dataset.runWork)?.group); return; }
      const foldLink = event.target.closest('[data-run-open-fold]');
      if (foldLink) { const row = [...detailScroll.querySelectorAll('[data-run-fold]')].find(item => item.dataset.runFold === foldLink.dataset.runOpenFold); if (row) { row.open = true; lazySteps.get(row)?.(); row.scrollIntoView({ block: 'nearest' }); } return; }
      const opener = event.target.closest('[data-open-session]');
      if (opener) { openSession(opener.dataset.openSession); return; }
      const link = event.target.closest('[data-flow-peer]');
      if (link) { select('agent', link.dataset.flowPeer, nodes.get(link.dataset.flowPeer)); detailScroll.scrollTop = 0; }
    });
    listen(root, 'keydown', event => { if (event.key === 'Escape' && selected) { event.preventDefault(); closeDetails(); } });
    listen(detailScroll, 'toggle', event => {
      const row = event.target;
      if (row.open) lazySteps.get(row)?.();
      if (row.dataset.runFold) { if (row.open) openFolds.add(row.dataset.runFold); else openFolds.delete(row.dataset.runFold); }
      if (row.dataset.step) {
        if (row.open) openSteps.add(row.dataset.step);
        else openSteps.delete(row.dataset.step);
      }
    }, true);
    listen(fit, 'click', () => { reseat(); visibleAgents.forEach(agent => positionNode(agent.name)); updateEdges(); fitView(true); });
    listen(panCue, 'click', () => {
      camera.y += (panCue.dataset.direction === 'down' ? -1 : 1) * viewport.clientHeight * .7;
      camera.touched = true; clampPan(); transform();
    });
    listen(viewport, 'wheel', event => {
      event.preventDefault(); const rect = viewport.getBoundingClientRect();
      if (arrangement === 'compact' && !event.ctrlKey && !event.metaKey) {
        camera.y -= event.deltaY; camera.touched = true; clampPan(); transform(); return;
      }
      zoomAt(camera.scale * Math.exp(-clamp(event.deltaY, -160, 160) * .007), event.clientX - rect.left, event.clientY - rect.top);
    }, { passive: false });
    listen(nodeLayer, 'click', event => {
      const inspect = event.target.closest('[data-agent-inspect]');
      if (inspect) { select('agent', inspect.dataset.agentInspect, nodes.get(inspect.dataset.agentInspect)); return; }
      const node = event.target.closest('[data-agent]');
      if (node && !suppressClick) activateAgent(node.dataset.agent, node, event.shiftKey);
    });
    listen(loopLayer, 'click', event => {
      const work = event.target.closest('[data-work-loop-id]');
      if (work && !suppressClick) { select('work-loop', work.dataset.workLoopId, work); return; }
      const loop = event.target.closest('[data-loop-id]');
      if (loop && !suppressClick) select('loop', loop.dataset.loopId, loop);
    });
    listen(viewport, 'keydown', event => {
      const inspect = event.target.closest('[data-agent-inspect]');
      if (inspect && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); select('agent', inspect.dataset.agentInspect, nodes.get(inspect.dataset.agentInspect)); return; }
      const work = event.target.closest('[data-work-loop-id]');
      if (work && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); select('work-loop', work.dataset.workLoopId, work); return; }
      const loop = event.target.closest('[data-loop-id]');
      if (loop && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); select('loop', loop.dataset.loopId, loop); return; }
      const node = event.target.closest('[data-agent]');
      if (node && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); activateAgent(node.dataset.agent, node, event.shiftKey); return; }
      if (node && event.shiftKey && event.key.startsWith('Arrow')) {
        const p = positions.get(node.dataset.agent);
        stopLayout(); let x = p.x, y = p.y;
        if (event.key === 'ArrowLeft') x -= 12;
        else if (event.key === 'ArrowRight') x += 12;
        else if (event.key === 'ArrowUp') y -= 12;
        else if (event.key === 'ArrowDown') y += 12;
        else return;
        event.preventDefault(); camera.touched = true; moveNode(node.dataset.agent, x, y);
        if (arrangement === 'compact') { clampPan(); transform(); } else fitView(false);
        return;
      }
      if (event.target !== viewport) return;
      if (event.key === '+' || event.key === '=') zoomAt(camera.scale * 1.2);
      else if (event.key === '-') zoomAt(camera.scale / 1.2);
      else if (event.key.toLowerCase() === 'f') fitView(true);
      else if (event.key === 'ArrowLeft') camera.x += 45;
      else if (event.key === 'ArrowRight') camera.x -= 45;
      else if (event.key === 'ArrowUp') camera.y += 45;
      else if (event.key === 'ArrowDown') camera.y -= 45;
      else return;
      event.preventDefault(); camera.touched = true; clampPan(); transform();
    });
    listen(viewport, 'pointerdown', event => {
      if (event.button !== 0 && event.pointerType !== 'touch') return;
      if (event.target.closest('[data-agent-inspect]')) return;
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (pointers.size === 2) {
        const [a, b] = Array.from(pointers.values()), rect = viewport.getBoundingClientRect();
        pinch = { distance: Math.hypot(a.x - b.x, a.y - b.y), scale: camera.scale, x: ((a.x + b.x) / 2 - rect.left - camera.x) / camera.scale, y: ((a.y + b.y) / 2 - rect.top - camera.y) / camera.scale };
        drag = null; suppressClick = true;
      } else if (pointers.size === 1) {
        const node = event.target.closest('[data-agent]'), p = node && positions.get(node.dataset.agent), loop = event.target.closest('[data-loop-id], [data-work-loop-id]');
        if (p) stopLayout();
        drag = { id: event.pointerId, key: p ? node.dataset.agent : null, loopKey: loop?.dataset.workLoopId || loop?.dataset.loopId, loopType: loop?.hasAttribute('data-work-loop-id') ? 'work-loop' : 'loop', startX: event.clientX, startY: event.clientY, x: p ? p.x : camera.x, y: p ? p.y : camera.y, moved: false };
        suppressClick = false;
        if (node) node.focus({ preventScroll: true });
        else if (loop) loop.focus({ preventScroll: true });
      }
      viewport.setPointerCapture(event.pointerId);
    });
    listen(viewport, 'pointermove', event => {
      if (!pointers.has(event.pointerId)) return;
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (pinch && pointers.size >= 2) {
        const [a, b] = Array.from(pointers.values()), rect = viewport.getBoundingClientRect();
        camera.scale = clamp(pinch.scale * Math.hypot(a.x - b.x, a.y - b.y) / Math.max(1, pinch.distance), .2, 3);
        camera.x = (a.x + b.x) / 2 - rect.left - pinch.x * camera.scale;
        camera.y = (a.y + b.y) / 2 - rect.top - pinch.y * camera.scale;
        camera.touched = true; transform(); return;
      }
      if (!drag || drag.id !== event.pointerId) return;
      const dx = event.clientX - drag.startX, dy = event.clientY - drag.startY;
      if (!drag.moved && Math.hypot(dx, dy) < 4) return;
      drag.moved = true; camera.touched = true; suppressClick = true; viewport.dataset.dragging = 'true';
      if (drag.key) { if (positions.has(drag.key)) moveNode(drag.key, drag.x + dx / camera.scale, drag.y + dy / camera.scale); }
      else { camera.x = drag.x + dx; camera.y = drag.y + dy; clampPan(); transform(); }
    });
    function pointerUp(event) {
      if (!pointers.has(event.pointerId)) return;
      if (event.type === 'pointerup' && drag && !drag.moved && drag.key && !pinch) { activateAgent(drag.key, nodes.get(drag.key), event.shiftKey); suppressClick = true; }
      if (event.type === 'pointerup' && drag?.loopKey && !drag.moved && !pinch) { select(drag.loopType, drag.loopKey, (drag.loopType === 'work-loop' ? workLoopViews : loopViews).get(drag.loopKey)?.group); suppressClick = true; }
      if (drag?.key && drag.moved) { if (arrangement === 'compact') { clampPan(); transform(); } else fitView(false); }
      pointers.delete(event.pointerId); pinch = null; drag = null; viewport.dataset.dragging = 'false';
      if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
    }
    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(name => listen(viewport, name, pointerUp));

    function render(data) {
      const previousTeam = activeTeam;
      snapshot = { ...data, agents: prepareRoster(data) };
      const stamp = JSON.stringify([data.agents, data.graph, data.warnings, data.totals, data.escalation]);
      if (stamp !== renderStamp) { renderStamp = stamp; renderRevision++; }
      indexSteps();
      if (activeTeam !== previousTeam) { rememberTeam(previousTeam); restoreTeam(activeTeam); }
      const graph = data.graph;
      runDetails.dataset.notices = String(data.warnings.length + (graph ? graph.problems.length : 0) > 0 || !!data.learning?.due);
      renderActivity();
      const previousLayout = layoutSignature;
      if (root.clientWidth > 0 && viewport.clientWidth > 0) {
        addPositions(visibleAgents);
        const labelsChanged = renderNodes(visibleAgents); updateEdges();
        if (!camera.initialized || previousLayout !== layoutSignature || (labelsChanged && !camera.touched)) fitView(false);
      }
      root.dataset.state = visibleAgents.length || graph ? 'ready' : 'empty';
      showEmpty();
      if (initialSelection) {
        const key = initialSelection, step = graph?.nodes.find(n => n.id === key);
        initialSelection = '';
        const agent = agentsByName.get(key) || (step && agentsByName.get(nodeOwners.get(step.id)));
        if (agent) select('agent', agent.name, nodes.get(agent.name), step?.id);
        else if (step) select('run', '', runDetails, step.id);
      }
      renderDetail(); highlight();
    }
    function scheduleResize() {
      if (!alive || resizeFrame !== null) return;
      resizeFrame = requestAnimationFrame(() => {
        resizeFrame = null;
        if (!alive || root.clientWidth <= 0 || root.clientHeight <= 0) return;
        root.classList.toggle('ag-compact', root.clientWidth <= 650);
        updateDetailSpace();
        const rect = viewport.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return;
        const next = root.clientWidth <= 650 ? 'compact' : 'wide';
        const resized = next !== arrangement || Math.abs(rect.width - camera.width) > .1 || Math.abs(rect.height - camera.height) > .1;
        if (resized) cancelInteraction();
        root.classList.toggle('ag-canvas-compact', next === 'compact');
        if (snapshot && next !== arrangement) {
          rememberTeam(activeTeam);
          arrangement = next;
          restoreTeam(activeTeam);
        } else arrangement = next;
        if (snapshot) { reseat(); addPositions(visibleAgents); renderNodes(visibleAgents); updateEdges(); }
        // The user's own pan and zoom survive anything that did not change the space.
        if (resized || !camera.initialized) { fitView(false); reveal(); } else { clampPan(); transform(); }
      });
    }
    const resize = new ResizeObserver(scheduleResize);
    root.classList.toggle('ag-compact', root.clientWidth > 0 && root.clientWidth <= 650);
    root.classList.toggle('ag-canvas-compact', arrangement === 'compact');
    resize.observe(root); resize.observe(viewport);
    function refresh() {
      if (!alive) return Promise.resolve();
      if (pending) return pending;
      const requestGeneration = generation;
      const request = Promise.resolve().then(() => {
        if (alive && requestGeneration === generation) return options.fetchSnapshot();
      }).then(data => {
        if (!alive || requestGeneration !== generation) return;
        if (!data || data.error || data.version !== 1 || !Array.isArray(data.agents)) {
          const error = new Error(data?.error ? text(data.error) : 'Snapshot is not available');
          error.documentError = true;
          throw error;
        }
        render(data); notice.hidden = true;
      }).catch(error => {
        if (!alive || requestGeneration !== generation) return;
        root.dataset.state = 'error'; notice.hidden = false;
        notice.textContent = text(error?.message).replace(/\s+/g, ' ').trim() || 'Unavailable';
        notice.title = notice.textContent;
        showEmpty();
      }).finally(() => { if (pending === request) pending = null; });
      pending = request;
      return pending;
    }
    function reset() {
      if (!alive) return Promise.resolve();
      generation++; pending = null; snapshot = null; stopLayout();
      if (resizeFrame !== null) cancelAnimationFrame(resizeFrame);
      resizeFrame = null;
      pointers.forEach((_, id) => { if (viewport.hasPointerCapture(id)) viewport.releasePointerCapture(id); });
      pointers.clear(); drag = null; pinch = null; suppressClick = false;
      viewport.dataset.dragging = 'false';
      selected = null; selectedOrigin = null; initialSelection = ''; highlightedStep = ''; detailSignature = '';
      teamChoice = null; activeTeam = 'main'; teamChoices = []; layoutSignature = ''; renderStamp = '';
      arrangement = root.clientWidth > 0 && root.clientWidth <= 650 ? 'compact' : 'wide';
      visibleAgents = []; hubName = ''; openSteps.clear(); openFolds.clear(); teamViews.clear(); positions.clear(); nodes.clear(); lines.clear(); loopViews.clear(); workLoopViews.clear();
      agentsByName.clear(); stepsByAgent.clear(); stepsById.clear(); nodeOwners.clear(); shownAgents.clear();
      nodeLayer.replaceChildren(); spokeLayer.replaceChildren(); escalationLayer.replaceChildren(); flowLayer.replaceChildren(); loopLayer.replaceChildren();
      detailScroll.replaceChildren(); detail.hidden = true; root.classList.remove('ag-details-open');
      root.style.removeProperty('--ag-sheet-height');
      delete root.dataset.team; root.dataset.state = 'loading';
      notice.hidden = true; notice.textContent = ''; notice.title = '';
      activity.hidden = true; activity.textContent = ''; attention.hidden = true; usage.hidden = true; delete runDetails.dataset.notices;
      showEmpty();
      Object.assign(camera, { x: 0, y: 0, scale: 1, initialized: false, touched: false, width: 0, height: 0 }); transform();
      return refresh();
    }
    const timer = setInterval(() => { if (!document.hidden) refresh(); }, Math.max(250, Number(options.intervalMs) || 1500));
    listen(document, 'visibilitychange', () => { if (!document.hidden) refresh(); });
    refresh();
    return { refresh, reset, destroy() {
      if (!alive) return;
      alive = false; generation++; stopLayout(); if (resizeFrame !== null) cancelAnimationFrame(resizeFrame); clearInterval(timer); resize.disconnect(); schemeObserver.disconnect(); cleanups.forEach(fn => fn());
      pointers.forEach((_, id) => { if (viewport.hasPointerCapture(id)) viewport.releasePointerCapture(id); });
      pointers.clear(); teamViews.clear(); root.replaceChildren(); root.classList.remove('agentgraph', 'ag-compact', 'ag-canvas-compact', 'ag-details-open'); root.style.removeProperty('--ag-sheet-height'); root.style.removeProperty('--ag-header-height'); delete root.dataset.state; delete root.dataset.team; delete root.dataset.agScheme; delete root.dataset.agSchemeSource; delete root.dataset.agMotion;
    } };
  }
  window.AgentGraph = { mount };
})();
