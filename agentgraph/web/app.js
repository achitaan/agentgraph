(function () {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const LIST_LIMIT = 200;
  const EDGE = 12, SLOT = 104, GROUP_GAP = 34, SEAT_DISTANCE = 68;
  const DETAIL_WIDTH = 340; // the details panel beside a wide graph (style.css: .ag-details-open .ag-network margin)
  const READABLE = 11 / 12; // agent names are 12 px: the automatic view never shows them under 11 px; it pans instead
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
  // How full and how worn the root session is, from `root.wear`; null when the snapshot has none.
  function wearOf(root) {
    const wear = root && root.wear;
    if (!wear || typeof wear !== 'object' || !Number.isFinite(Number(wear.context)) || wear.context == null) return null;
    const positive = value => (value != null && Number(value) > 0 ? Number(value) : 0);
    const context = Math.max(0, Number(wear.context)), limit = positive(wear.compact_at) || positive(wear.window) || 1000000;
    const share = Math.min(1, context / limit), summaries = Math.max(0, Math.floor(Number(wear.summaries) || 0));
    return { wear, context, limit, share, summaries, step: share >= 0.85 ? 'high' : share >= 0.6 ? 'mid' : 'low', toNext: positive(wear.compact_at) > 0 };
  }
  function plural(count, one, many) { return count + ' ' + (count === 1 ? one : many); }
  function wearTip(info) {
    const wear = info.wear, turns = Math.max(0, Math.floor(Number(wear.turns) || 0));
    return ['context ' + amount(info.context) + ' of ' + amount(info.limit) + (info.toNext ? ' before the next summary' : wear.window_known === false ? ' window (assumed)' : ' window'),
      info.summaries ? 'summarized ' + plural(info.summaries, 'time', 'times') : 'not summarized yet',
      turns ? plural(turns, 'turn', 'turns') : ''].filter(Boolean).join(' · ');
  }
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
    // Drawing flows: only when the host can write the structure. A click starts a path at an agent, each
    // further click adds a step (nothing is saved yet), and Enter, a second click on the newest agent or a
    // double-click finishes it and asks the host to add the flow. Details open on a right-click (contextmenu),
    // on `i`, the context-menu key or Shift+F10, and on a touch long press; a left click never opens them.
    // A press that moves moves the agent, in every mode.
    const flowAction = options.actions && typeof options.actions.flow === 'function' ? options.actions.flow : null;
    const skillAction = options.actions && typeof options.actions.skills === 'function' ? options.actions.skills : null;
    // Skill changes asked of the host: the one pending per agent ("add:NAME", "remove:NAME" or "all"), the host's
    // answer until a snapshot shows it, and the lines said under the agent's skills after an answer.
    const skillPending = new Map(), skillOverride = new Map(), skillNotes = new Map();
    if (flowAction) root.dataset.agFlows = 'draw';
    const MOVE_PX = 5, NUDGE_PX = 12, LONG_PRESS_MS = 500, DOUBLE_MS = 500, UNDO_STEPS = 10, FLOW_TEXT_CHARS = 2000;
    // A touch press that may become a long press (details), and whether the browser's contextmenu event that a
    // keyboard opening is followed by should be swallowed.
    let hold = null, keyMenu = false;
    // One meaning per gesture. `lastClick` is the newest clean click on an agent (a drag forgets it), so two of
    // them on one lone agent within DOUBLE_MS are a double-click; `swallowClick` eats the browser's click after a
    // drag ends, and `swallowDouble` the dblclick after a double-click that was already acted on.
    let lastClick = null, swallowClick = false, swallowDouble = false;
    // The drawn flow (or loop on itself) a left click picked: marked on the canvas, the target of Delete.
    let pickedFlow = '';
    let trail = [], trailPending = false, press = null, hovered = '', busyFlows = 0, loneCancel = '';
    // Where the user put agents, per team and arrangement ("main:wide"): a body as { x, y } on the stage, a
    // council seat as { dx, dy } from its lead. Kept for the session and, where the host has localStorage, across
    // sessions under one key; an agent never moved keeps its automatic place. Nothing goes to the structure folder.
    const POSITIONS_KEY = typeof options.positionsKey === 'string' && options.positionsKey ? options.positionsKey : 'agentgraph.positions.v1';
    const moves = loadMoves();
    // Presses since the last drag that moved something: a double-click counts only when its two clicks were clean.
    let cleanPresses = Infinity;
    const flowErrors = new Map(), flowHistory = [], routeViews = new Map(), freshRoutes = new Set(), timers = new Set();
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
    viewport.setAttribute('aria-keyshortcuts', 'F R + - ArrowUp ArrowDown ArrowLeft ArrowRight Shift+Enter');
    const drawing = svg('svg', { class: 'ag-drawing', width: '100%', height: '100%', 'aria-label': 'Agents' });
    const defs = svg('defs');
    ['data', 'feedback', 'loop', 'work', 'work-stuck', 'work-closed', 'data-live', 'feedback-live', 'loop-live', 'work-live', 'route'].forEach(kind => {
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
    const routeLayer = svg('g', { class: 'ag-routes' });
    const trailLayer = svg('g', { class: 'ag-trail', 'aria-hidden': 'true' });
    const wire = svg('line', { class: 'ag-wire', 'aria-hidden': 'true' });
    wire.style.display = 'none';
    stage.append(edgeLayer, loopLayer, routeLayer, trailLayer, wire, nodeLayer); drawing.append(defs, stage);
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
    // The context bar: the track is the distance to the next summary, the fill is the context now.
    function renderWear(info) {
      const fill = el('i'), track = el('span', 'ag-wear-bar'), parts = [track, el('span', 'ag-wear-number', amount(info.context))];
      track.dataset.step = info.step; fill.style.width = (info.share * 100).toFixed(1) + '%'; track.append(fill);
      if (info.summaries) {
        const dots = el('span', 'ag-wear-dots');
        for (let i = 0; i < Math.min(5, info.summaries); i++) dots.append(el('i'));
        parts.push(dots, el('span', 'ag-wear-count', String(info.summaries)));
      }
      usageContext.dataset.level = String(info.wear.level || '');
      usageContext.replaceChildren(...parts);
    }
    // The pending path, for assistive technology only: nothing is written in the graph area.
    const trailStatus = el('div', 'ag-sr-only');
    trailStatus.setAttribute('role', 'status'); trailStatus.setAttribute('aria-live', 'polite');
    network.append(status, usage, viewport, controls, trailStatus);
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
      const info = wearOf(snapshot.root);
      usageContext.hidden = !info && !snapshot.root?.context;
      if (info) { renderWear(info); usage.title = wearTip(info); }
      else { delete usageContext.dataset.level; usage.removeAttribute('title'); usageContext.textContent = snapshot.root?.context ? 'ctx ' + amount(snapshot.root.context) : ''; }
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
    // What an agent takes up round its centre: its circle, its name and its busy count (top right).
    function extentOf(p) {
      return { left: Math.min(-p.r - 14, p.labelMin ?? -40), right: Math.max(p.r + 14, p.labelMax ?? 40), top: -p.r - (p.labelUp ? 26 : 16), bottom: p.r + (p.labelUp ? 16 : 28) };
    }
    function graphBounds() {
      const points = visibleAgents.map(a => positions.get(a.name)).filter(Boolean);
      if (!points.length) return null;
      const spans = points.map(p => ({ p, e: extentOf(p) }));
      return {
        left: Math.min(...spans.map(({ p, e }) => p.x + e.left)),
        right: Math.max(...spans.map(({ p, e }) => p.x + e.right)),
        top: Math.min(...spans.map(({ p, e }) => p.y + e.top)),
        bottom: Math.max(...spans.map(({ p, e }) => p.y + e.bottom))
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
    // Bring the selected agent, with its name and its count, fully into the space the details leave uncovered
    // (the graph may be larger than that space: it is never shrunk below a readable size).
    function reveal() {
      const point = selected?.type === 'agent' || selected?.type === 'work-loop' ? positions.get(selected.key) : null;
      const width = viewport.clientWidth, height = viewport.clientHeight;
      if (!point || !width || !height) return;
      const e = extentOf(point), s = camera.scale;
      const top = (point.y + e.top) * s + camera.y, bottom = (point.y + e.bottom) * s + camera.y;
      const left = (point.x + e.left) * s + camera.x, right = (point.x + e.right) * s + camera.x;
      const outside = top < EDGE || bottom > height - EDGE || left < EDGE || right > width - EDGE;
      if (!outside) return;
      if (top < EDGE || bottom > height - EDGE) camera.y += height / 2 - (top + bottom) / 2;
      if (left < EDGE || right > width - EDGE) camera.x += width / 2 - (left + right) / 2;
      clampPan(); transform();
    }
    function fitView(userAction) {
      if (!positions.size) return;
      const rect = viewport.getBoundingClientRect(), bounds = graphBounds();
      if (!rect.width || !rect.height || !bounds) return;
      if (arrangement === 'compact' && !userAction) {
        // Every agent, its name and its count fit in the space left (the details sheet takes the lower part):
        // full size when they fit, smaller as far as names stay readable; past that the graph pans.
        const pad = 8, fit = Math.min((rect.width - pad * 2) / (bounds.right - bounds.left), (rect.height - pad * 2) / (bounds.bottom - bounds.top));
        camera.scale = clamp(fit, READABLE, 1);
        camera.x = rect.width / 2 - (bounds.left + bounds.right) / 2 * camera.scale;
        camera.y = rect.height / 2 - (bounds.top + bounds.bottom) / 2 * camera.scale;
        clampPan();
      } else {
        const fitWith = pad => clamp(Math.min((rect.width - pad * 2) / (bounds.right - bounds.left), (rect.height - pad * 2) / (bounds.bottom - bounds.top), arrangement === 'compact' ? 1 : 1.3), .05, 3);
        camera.scale = fitWith(arrangement === 'compact' ? 6 : 26);
        // The automatic wide view keeps names readable: a narrower margin first, then the floor (and the graph pans).
        if (!userAction && camera.scale < READABLE) camera.scale = Math.max(READABLE, fitWith(EDGE));
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
    function isHubAgent(name) { const agent = shownAgents.get(name); return !!agent && (agent.name === hubName || agent.group === 'hub'); }
    function isSeatAgent(agent) { return agent.parent ? agent.parent !== hubName : agent.name.startsWith('councillor-'); }
    // The agent a step of a flow stands for: a council seat stands for its council.
    function standIn(name) {
      const agent = shownAgents.get(name);
      if (!agent) return '';
      return isSeatAgent(agent) && agent.parent && shownAgents.has(agent.parent) ? agent.parent : name;
    }
    function later(fn, ms) {
      const timer = setTimeout(() => { timers.delete(timer); if (alive) fn(); }, ms);
      timers.add(timer); return timer;
    }
    function canvasFlows() {
      const loopPairs = new Set(canvasLoops().map(loop => connectionKey(loop.worker, loop.evaluator)));
      return flows().filter(f => f.from !== hubName && f.to !== hubName && allowedConnection(f.from, f.to) && !(['data', 'feedback'].includes(f.kind) && loopPairs.has(connectionKey(f.from, f.to))));
    }
    function displayName(agent) { return agent.parent ? agent.name.replace(/^councillor-/, '') : agent.name; }
    function positionStore() { try { return typeof localStorage === 'undefined' ? null : localStorage; } catch (_) { return null; } }
    function loadMoves() {
      const kept = {};
      try {
        const raw = positionStore()?.getItem(POSITIONS_KEY), saved = raw ? JSON.parse(raw) : null;
        Object.entries(saved && typeof saved.layouts === 'object' && saved.layouts ? saved.layouts : {}).forEach(([layout, agents]) => {
          if (!agents || typeof agents !== 'object') return;
          Object.entries(agents).forEach(([name, value]) => {
            const body = value && Number.isFinite(value.x) && Number.isFinite(value.y), seat = value && Number.isFinite(value.dx) && Number.isFinite(value.dy);
            if (body || seat) (kept[layout] ||= {})[name] = body ? { x: value.x, y: value.y } : { dx: value.dx, dy: value.dy };
          });
        });
      } catch (_) { /* no storage, or nothing usable in it: the automatic layout */ }
      return kept;
    }
    function saveMoves() {
      try {
        const store = positionStore();
        if (!store) return;
        if (Object.keys(moves).length) store.setItem(POSITIONS_KEY, JSON.stringify({ version: 1, layouts: moves }));
        else store.removeItem(POSITIONS_KEY);
      } catch (_) { /* storage full or refused: the positions still hold for this session */ }
    }
    function layoutKey() { return activeTeam + ':' + arrangement; }
    function movedHere() { return moves[layoutKey()] || {}; }
    function copyMoves(layout = layoutKey()) { return JSON.parse(JSON.stringify(moves[layout] || {})); }
    function stopLayout() {
      if (layoutFrame !== null) cancelAnimationFrame(layoutFrame);
      layoutFrame = null;
    }
    function cancelInteraction() {
      if (drag || pinch || pointers.size) suppressClick = true;
      if (press?.moved) { finishMove(press.before); swallowClick = true; lastClick = null; }
      cancelPress(); clearHold(); if (!trail.length || trailPending) wire.style.display = 'none';
      drag = null; pinch = null;
      const captured = Array.from(pointers.keys()); pointers.clear();
      captured.forEach(id => { if (viewport.hasPointerCapture(id)) viewport.releasePointerCapture(id); });
      viewport.dataset.dragging = 'false';
    }
    function updateDetailSpace() {
      if (root.classList.contains('ag-compact') && !detail.hidden) root.style.setProperty('--ag-sheet-height', Math.max(0, Math.min(root.clientHeight * .4, root.clientHeight - 112)) + 'px');
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
      // The canvas as it is with the details closed: a narrow oval grows into it, and opening the sheet does not re-lay
      // out the agents (the camera fits them into what is left).
      const sheet = compact && root.classList.contains('ag-details-open') ? parseFloat(getComputedStyle(root).getPropertyValue('--ag-sheet-height')) || 0 : 0;
      const canvasHeight = Math.max(1, Math.round(viewport.clientHeight + sheet));
      // Wide: the oval is shaped for the canvas the details panel (DETAIL_WIDTH) leaves, open or not, so opening it
      // never re-lays out the agents and they still fit at a readable size beside it.
      const aspect = Math.round(clamp((rect.width - DETAIL_WIDTH) / rect.height, 1, 1.3) * 20) / 20;
      const signature = JSON.stringify([arrangement, compact ? [Math.round(rect.width), canvasHeight] : aspect, hubName, snapshot?.structure?.source === 'folder' ? (snapshot.groups || []).map(group => group.id) : null, agents.map(a => [a.name, a.group, a.parent]).sort()]);
      if (signature === layoutSignature) return;
      layoutSignature = signature;
      stopLayout();
      const byAgent = new Map(agents.map(a => [a.name, a]));
      const isSeat = a => { const lead = a.parent && a.parent !== a.name && a.parent !== hubName && byAgent.get(a.parent); return !!lead && !(lead.parent && byAgent.has(lead.parent)); };
      const seatsOf = new Map();
      agents.filter(isSeat).sort((a, b) => a.name.localeCompare(b.name)).forEach(a => { if (!seatsOf.has(a.parent)) seatsOf.set(a.parent, []); seatsOf.get(a.parent).push(a); });
      const ring = agents.filter(a => a.name !== hubName && !isSeat(a));
      const keys = Array.from(new Set(ring.map(a => a.group)));
      // With a structure folder the ring follows the folder order, first group at the top; otherwise the built-in order.
      const listed = snapshot?.structure?.source === 'folder' ? (snapshot.groups || []).map(group => group.id).filter(id => keys.includes(id)) : null;
      const order = listed ? listed.slice(1) : ['decide', 'build', 'verify', 'understand'];
      const lead = listed ? listed.slice(0, 1) : keys.filter(key => !order.includes(key)).sort((a, b) => Number(b === 'council') - Number(a === 'council') || String(a).localeCompare(String(b)));
      const slot = compact ? 100 : SLOT;
      const groups = [...lead, ...order.filter(key => keys.includes(key)), ...(listed ? keys.filter(key => !listed.includes(key)).sort() : [])].map(key => {
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
        ry = Math.max(ry, Math.min(rx * 2.2, canvasHeight / 2 - Math.max(depth + 34, 96)));
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
      const placedByUser = movedHere();
      const targets = Array.from(seeds, ([name, seed]) => {
        const saved = placedByUser[name], pinned = !seed.parent && !!saved && Number.isFinite(saved.x);
        const seat = !!seed.parent && !!saved && Number.isFinite(saved.dx) ? { dx: saved.dx, dy: saved.dy } : {};
        return { name, ...seed, ...seat, ...(pinned ? { x: saved.x, y: saved.y } : {}), pinned, still: pinned || name === hubName };
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
    function learningLine(learning) { return [learning.total + (learning.total === 1 ? ' note' : ' notes'), learning.pending ? learning.pending + ' new' : '', learning.due ? 'change due' : '', learning.improvements ? learning.improvements + ' applied' : ''].filter(Boolean).join(' · '); }
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
    // Move one agent to (x, y) on the stage, as far as it goes without landing on another. A lead carries its
    // seats; a seat moves on its own and keeps its new place beside its lead. The place is remembered.
    function moveNode(name, x, y) {
      const point = positions.get(name);
      if (!point) return;
      const lead = point.parent && positions.get(point.parent);
      let dx = x - point.x, dy = y - point.y;
      // A lead carries its seats, so the council stays a circle while it moves.
      const moving = new Set([name]);
      if (!lead) positions.forEach((child, childName) => { if (child.parent === name) moving.add(childName); });
      // Nothing is moved out of the canvas: each agent, its name and its count stay inside, EDGE px in. One already
      // outside (the space shrank) can still move, only not further out.
      const width = viewport.clientWidth, height = viewport.clientHeight;
      if (width > 0 && height > 0) {
        let lowX = -Infinity, highX = Infinity, lowY = -Infinity, highY = Infinity;
        moving.forEach(key => {
          const member = positions.get(key), e = extentOf(member);
          lowX = Math.max(lowX, (EDGE - camera.x) / camera.scale - e.left - member.x); highX = Math.min(highX, (width - EDGE - camera.x) / camera.scale - e.right - member.x);
          lowY = Math.max(lowY, (EDGE - camera.y) / camera.scale - e.top - member.y); highY = Math.min(highY, (height - EDGE - camera.y) / camera.scale - e.bottom - member.y);
        });
        if (lowX <= highX) dx = clamp(dx, Math.min(0, lowX), Math.max(0, highX));
        if (lowY <= highY) dy = clamp(dy, Math.min(0, lowY), Math.max(0, highY));
      }
      // Too close to another is refused, unless the move takes it further away (so a crowded agent can get out).
      const valid = (dx, dy) => {
        for (const key of moving) {
          const member = positions.get(key);
          for (const [otherName, other] of positions) {
            if (moving.has(otherName)) continue;
            const room = member.r + other.r + 14, after = Math.hypot(member.x + dx - other.x, member.y + dy - other.y);
            if (after < room && after < Math.hypot(member.x - other.x, member.y - other.y) - .001) return false;
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
      const here = moves[layoutKey()] ||= {};
      if (lead) { point.dx = point.x - lead.x; point.dy = point.y - lead.y; here[name] = { dx: point.dx, dy: point.dy }; }
      else { point.fixed = true; here[name] = { x: point.x, y: point.y }; }
      updateEdges();
    }
    // A move is over: keep it (storage and undo) when it changed anything; the camera stays where it is.
    function finishMove(before, entry = {}) {
      cleanPresses = 0;
      const layout = layoutKey();
      if (JSON.stringify(before) === JSON.stringify(moves[layout] || {})) return;
      renderNodes(visibleAgents); updateEdges(); saveMoves();
      const last = flowHistory.at(-1);
      // Alt+arrow presses on one agent in a row are one move for undo.
      if (entry.agent && last?.op === 'move' && last.agent === entry.agent && last.layout === layout && last.by === 'key') return;
      remember({ op: 'move', layout, before, ...entry });
    }
    // Put the given positions back (undo), or none (every agent back in its automatic place).
    function applyMoves(layout, positionsThen, record) {
      const before = copyMoves(layout);
      if (JSON.stringify(before) === JSON.stringify(positionsThen)) return false;
      if (Object.keys(positionsThen).length) moves[layout] = JSON.parse(JSON.stringify(positionsThen)); else delete moves[layout];
      saveMoves();
      if (record) remember({ op: 'move', layout, before });
      if (layout !== layoutKey()) { teamViews.delete(layout); return true; }
      stopLayout(); layoutSignature = '';
      positions.forEach(point => { point.fixed = false; });
      addPositions(visibleAgents); renderNodes(visibleAgents); updateEdges();
      if (trail.length && !trailPending) trailChanged();
      return true;
    }
    function resetMoves() {
      if (applyMoves(layoutKey(), {}, true)) fitView(false);
    }
    function measureLabels(node, point) {
      if (!node || !point) return false;
      let low = -point.r - 8, high = point.r + 8;
      ['.ag-agent-name'].forEach(selector => {
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
          // The only text on an agent is its name, and a count of its sessions working now when there are two or more.
          node.append(svg('text', { class: 'ag-busy-count', 'data-busy-count': agent.name, 'text-anchor': 'middle', 'dominant-baseline': 'central' }));
          node.append(svg('circle', { class: 'ag-learn-track' }), svg('g', { class: 'ag-learn-beads' }), svg('circle', { class: 'ag-learn-hit', 'data-learning': '', role: 'button', tabindex: 0, 'aria-label': 'Learning' }), svg('circle', { class: 'ag-alert', r: 4 }), svg('title'));
          nodes.set(agent.name, node); nodeLayer.append(node);
        }
        const active = busySteps(agent), isHub = agent.name === hubName;
        // The task is in the accessible name and the details only, never drawn on the canvas.
        const work = busy && active.length ? text(active[0].goal || active[0].id) + (active.length > 1 ? ', and ' + (active.length - 1) + ' more' : '') : '';
        node.dataset.busy = String(busy); node.dataset.group = agent.group;
        node.dataset.openChat = String(!!sessionToOpen(agent));
        node.dataset.attention = String(alerts.has(agent.name));
        // The outer loop sits round the centre: one bead for each thing the user said, filled until a prompt change answers it.
        const learn = isHub ? snapshot.learning : null, reach = p.r + 27, from = 158, sweep = 224, round = 2 * Math.PI * reach;
        node.dataset.outer = learn ? learn.due ? 'due' : 'on' : 'off';
        [node.querySelector('.ag-learn-track'), node.querySelector('.ag-learn-hit')].forEach(ring => { ring.setAttribute('r', reach); ring.setAttribute('stroke-dasharray', (round * sweep / 360) + ' ' + round); ring.setAttribute('transform', 'rotate(' + from + ')'); });
        const beads = node.querySelector('.ag-learn-beads'), count = learn ? Math.min(40, Number(learn.total) || 0) : 0, fresh = learn ? Math.min(count, Number(learn.pending) || 0) : 0;
        if (beads.dataset.stamp !== count + ':' + fresh + ':' + reach) {
          beads.dataset.stamp = count + ':' + fresh + ':' + reach;
          beads.replaceChildren(...Array.from({ length: count }, (_, i) => {
            const angle = (from + (count === 1 ? sweep / 2 : i * sweep / (count - 1))) * Math.PI / 180;
            return svg('circle', { class: 'ag-learn-bead', 'data-fresh': String(i < fresh), cx: reach * Math.cos(angle), cy: reach * Math.sin(angle), r: 2.2 });
          }));
        }
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
        const badge = node.querySelector('.ag-busy-count'), corner = (p.r + 10) * Math.SQRT1_2;
        badge.setAttribute('x', corner); badge.setAttribute('y', -corner);
        badge.textContent = busy >= 2 ? '×' + busy : ''; badge.dataset.shown = String(busy >= 2);
        node.classList.toggle('ag-hub', isHub);
        node.classList.toggle('ag-council-lead', visibleAgents.some(a => a.parent === agent.name));
        node.querySelector('.ag-agent-name').textContent = displayName(agent);
        node.querySelector('title').textContent = flowErrors.get(agent.name) || agent.name;
        node.dataset.armed = String(!trailPending && trail.at(-1) === agent.name);
        node.dataset.trail = String(trail.includes(agent.name));
        if (flowAction) node.setAttribute('aria-keyshortcuts', 'Enter Shift+Enter I Shift+F10 ContextMenu C L R Alt+ArrowUp Alt+ArrowDown Alt+ArrowLeft Alt+ArrowRight Backspace Escape'); else node.removeAttribute('aria-keyshortcuts');
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
      // Settings alone draw nothing: an escalation route is drawn only once a task graph has used it.
      edges.push(...snapshot.escalation.filter(e => allowedConnection(e.from, e.to) && e.used).map(e => ({ ...e, kind: 'escalation', active: false })), ...workFlows);
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
          const layer = edge.kind === 'handoff' ? spokeLayer : edge.kind === 'escalation' ? escalationLayer : flowLayer;
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
      updateRoutes();
      updateWorkLoops();
    }
    // Flows drawn in the structure: a forward step is a straight line with an arrowhead, a step back to an
    // agent already on the path is a curved arc. Steps of different flows on one pair are offset so none
    // hides another. A flow at rest is still; a new one draws in once. The pending path is drawn dimmed.
    function routes() { return (snapshot?.structure?.flows || []).filter(flow => flow && typeof flow.id === 'string' && Array.isArray(flow.path) && flow.path.length >= 2 && flow.path.every(name => typeof name === 'string')); }
    function routeById(id) { return routes().find(flow => flow.id === id) || null; }
    // Step `index` (path[index] -> path[index + 1]) goes back to an agent already on the path; the hub ending a path adds no return.
    function returnsAt(path, index) {
      const to = path[index + 1];
      return path.slice(0, index + 1).includes(to) && !(index + 1 === path.length - 1 && isHubAgent(to));
    }
    function pairKey(a, b) { return a < b ? a + '\n' + b : b + '\n' + a; }
    function isSelfLoop(path) { return path.length === 2 && path[0] === path[1]; }
    // An agent's loop on itself: a small drop on its outer side (away from the hub), never over its name below it.
    function selfLoopPath(p, name) {
      const hub = positions.get(hubName);
      let ux = 0, uy = -1;
      if (hub && name !== hubName) { const d = Math.hypot(p.x - hub.x, p.y - hub.y); if (d > 1) { ux = (p.x - hub.x) / d; uy = (p.y - hub.y) / d; } }
      if (uy > -.35) { const n = Math.hypot(ux || 1, .35); ux = (ux < 0 ? -1 : 1) / n; uy = -.35 / n; }
      const angle = Math.atan2(uy, ux), at = (a, r) => (p.x + r * Math.cos(a)) + ' ' + (p.y + r * Math.sin(a));
      return 'M ' + at(angle - .5, p.r + 5) + ' C ' + at(angle - .85, p.r + 32) + ' ' + at(angle + .85, p.r + 32) + ' ' + at(angle + .5, p.r + 9);
    }
    // What else is drawn on each pair, as the band of sideways offsets it covers in the pair's own frame
    // (the pair in name order): a straight line (a hub spoke, a work line, a used escalation)
    // covers the middle, and a review loop from the task graph covers its two curved lanes.
    function takenBands() {
      const taken = new Map();
      const cover = (key, lo, hi) => { const band = taken.get(key); taken.set(key, band ? { lo: Math.min(band.lo, lo), hi: Math.max(band.hi, hi) } : { lo, hi }); };
      lines.forEach(pair => { if (pair.line.isConnected) cover(pairKey(pair.edge.from, pair.edge.to), -6, 6); });
      loopViews.forEach(view => {
        const { worker, evaluator } = view.loop, a = positions.get(worker), b = positions.get(evaluator), count = view.geometry?.count;
        if (!a || !b || !count || !view.group.isConnected) return;
        const distance = Math.hypot(b.x - a.x, b.y - a.y) || 1, nx = -(b.y - a.y) / distance, ny = (b.x - a.x) / distance;
        const bow = ((count.x - (a.x + b.x) / 2) * nx + (count.y - (a.y + b.y) / 2) * ny) * (worker < evaluator ? 1 : -1);
        const bulge = clamp(distance * .08, 8, 16);
        cover(pairKey(worker, evaluator), bow - bulge - 3, bow + bulge + 3);
      });
      return taken;
    }
    // The sideways offset of the `index`-th of `count` flow steps on a pair, and the side a return bows to.
    // Alone on a pair, steps are spread round the middle; next to anything else, they go beside it, on the
    // side nearer the middle, so a flow is never drawn on top of a line or a loop.
    function laneOf(band, index, count) {
      if (!band) { const offset = (index - (count - 1) / 2) * 8; return { offset, side: offset < 0 ? -1 : 1 }; }
      const side = band.hi + 6 <= -(band.lo - 6) ? 1 : -1;
      return { offset: side > 0 ? band.hi + 6 + index * 8 : band.lo - 6 - index * 8, side };
    }
    function stepPath(a, b, from, to, back, lane) {
      // One frame per pair, whichever way it is walked, so lanes on a pair never cross.
      const p = from < to ? a : b, q = from < to ? b : a;
      const distance = Math.hypot(q.x - p.x, q.y - p.y) || 1, nx = -(q.y - p.y) / distance, ny = (q.x - p.x) / distance;
      const offset = lane.offset;
      if (!back) {
        const ux = (b.x - a.x) / distance, uy = (b.y - a.y) / distance;
        return 'M ' + (a.x + ux * (a.r + 6) + nx * offset) + ' ' + (a.y + uy * (a.r + 6) + ny * offset) + ' L ' + (b.x - ux * (b.r + 9) + nx * offset) + ' ' + (b.y - uy * (b.r + 9) + ny * offset);
      }
      const side = lane.side, bow = 2 * (offset + side * clamp(distance * .14, 14, 30));
      const cx = (a.x + b.x) / 2 + nx * bow, cy = (a.y + b.y) / 2 + ny * bow;
      const out = Math.hypot(cx - a.x, cy - a.y) || 1, back2 = Math.hypot(cx - b.x, cy - b.y) || 1;
      return 'M ' + (a.x + (cx - a.x) / out * (a.r + 6)) + ' ' + (a.y + (cy - a.y) / out * (a.r + 6)) + ' Q ' + cx + ' ' + cy + ' ' + (b.x + (cx - b.x) / back2 * (b.r + 9)) + ' ' + (b.y + (cy - b.y) / back2 * (b.r + 9));
    }
    function updateRoutes() {
      const list = routes(), lanes = new Map(), shown = new Set(), taken = takenBands();
      list.forEach(flow => flow.path.slice(1).forEach((to, i) => {
        const key = pairKey(flow.path[i], to);
        if (!lanes.has(key)) lanes.set(key, []);
        lanes.get(key).push(flow.id + '#' + i);
      }));
      list.forEach(flow => {
        const points = flow.path.map(name => positions.get(name));
        if (points.some(p => !p)) return;
        shown.add(flow.id);
        let view = routeViews.get(flow.id);
        if (!view) {
          const group = svg('g', { class: 'ag-route', 'data-route': flow.id, role: 'button', tabindex: 0 });
          if (flowAction) group.setAttribute('aria-keyshortcuts', 'Enter I Shift+F10 ContextMenu Delete Backspace');
          const title = svg('title'); group.append(title); routeLayer.append(group);
          view = { group, title, steps: [], flow }; routeViews.set(flow.id, view);
        }
        view.flow = flow;
        while (view.steps.length > flow.path.length - 1) { const step = view.steps.pop(); step.hit.remove(); step.line.remove(); }
        while (view.steps.length < flow.path.length - 1) {
          const hit = svg('path', { class: 'ag-route-hit' }), line = svg('path', { class: 'ag-route-step', pathLength: 1, 'marker-end': 'url(#' + uid + '-route)' });
          view.group.insertBefore(hit, view.title); view.group.insertBefore(line, view.title); view.steps.push({ hit, line });
        }
        view.steps.forEach((step, i) => {
          const key = pairKey(flow.path[i], flow.path[i + 1]), sharing = lanes.get(key), lane = laneOf(taken.get(key), sharing.indexOf(flow.id + '#' + i), sharing.length);
          const self = isSelfLoop(flow.path), back = !self && returnsAt(flow.path, i);
          const d = self ? selfLoopPath(points[0], flow.path[0]) : stepPath(points[i], points[i + 1], flow.path[i], flow.path[i + 1], back, lane);
          step.line.setAttribute('d', d); step.hit.setAttribute('d', d);
          step.line.dataset.step = self ? 'self' : back ? 'return' : 'forward'; step.line.dataset.index = String(i);
        });
        view.group.setAttribute('aria-label', 'Flow ' + flow.path.join(' → ')); view.title.textContent = flow.path.join(' → ');
        if (freshRoutes.has(flow.id)) {
          freshRoutes.delete(flow.id); view.group.dataset.fresh = 'true';
          later(() => { delete view.group.dataset.fresh; }, 400);
        }
      });
      routeViews.forEach((view, id) => { if (!shown.has(id)) { view.group.remove(); routeViews.delete(id); } });
      updateTrail();
    }
    function updateTrail() {
      trailLayer.replaceChildren();
      trailLayer.dataset.pending = String(trailPending);
      trail.slice(1).forEach((to, i) => {
        const a = positions.get(trail[i]), b = positions.get(to);
        if (!a || !b) return;
        if (trail[i] === to) { trailLayer.append(svg('path', { class: 'ag-trail-step', 'data-step': 'self', d: selfLoopPath(a, to), 'marker-end': 'url(#' + uid + '-route)' })); return; }
        const back = returnsAt(trail, i);
        const lane = laneOf(takenBands().get(pairKey(trail[i], to)), 0, 1);
        trailLayer.append(svg('path', { class: 'ag-trail-step', 'data-step': back ? 'return' : 'forward', d: stepPath(a, b, trail[i], to, back, lane), 'marker-end': 'url(#' + uid + '-route)' }));
      });
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
          view = { group, ring, title, loop }; workLoopViews.set(loop.agent, view);
        }
        view.loop = loop;
        view.group.dataset.loopState = loop.state; view.group.dataset.active = String(!!loop.active);
        view.group.dataset.moving = String(!!loop.active && shownAgents.get(loop.agent)?.busy > 0);
        view.group.dataset.quiet = String(['closed', 'idle'].includes(loop.state) && !shownAgents.get(loop.agent)?.busy);
        view.ring.dataset.loopState = loop.state; view.ring.dataset.active = String(!!loop.active);
        const radius = p.r + 9, open = (Number(shownAgents.get(loop.agent)?.busy) || 0) >= 2 ? .46 : .17, start = -Math.PI / 4 + open, end = -Math.PI / 4 - open;
        view.ring.setAttribute('d', 'M ' + (p.x + radius * Math.cos(start)) + ' ' + (p.y + radius * Math.sin(start)) + ' A ' + radius + ' ' + radius + ' 0 1 1 ' + (p.x + radius * Math.cos(end)) + ' ' + (p.y + radius * Math.sin(end)));
        view.ring.setAttribute('marker-end', 'url(#' + uid + (loop.state === 'stuck' ? '-work-stuck' : loop.state === 'closed' ? '-work-closed' : loop.state === 'working' && loop.active ? '-work-live' : '-work') + ')');
        view.group.setAttribute('aria-label', loop.agent + ', work loop, ' + loop.rounds + (loop.rounds === 1 ? ' round, ' : ' rounds, ') + named(WORK_LOOP_STATES, loop.state));
        view.title.textContent = loop.agent;
      });
      workLoopViews.forEach((view, agent) => { if (!shown.has(agent)) { view.group.remove(); workLoopViews.delete(agent); } });
    }
    function highlight() {
      // With flow drawing the pressed state is the newest step of the pending path.
      nodes.forEach((node, key) => { const active = !!selected && ['agent', 'work-loop'].includes(selected.type) && selected.key === key; node.dataset.selected = String(active); node.setAttribute('aria-pressed', String(flowAction ? !trailPending && trail.at(-1) === key : active)); });
      const selecting = !!selected && ['agent', 'work-loop'].includes(selected.type);
      const chosenFlow = selected?.type === 'flow' ? selected.key : '';
      if (pickedFlow && !routeById(pickedFlow)) pickedFlow = '';
      routeLayer.dataset.selecting = String(!!selected && !['run', 'learning'].includes(selected.type));
      routeViews.forEach((view, id) => {
        const marked = chosenFlow === id || pickedFlow === id;
        view.group.dataset.selected = String(marked); view.group.setAttribute('aria-pressed', String(marked));
        view.group.dataset.related = String(chosenFlow ? chosenFlow === id : selecting && view.flow.path.includes(selected.key));
      });
      edgeLayer.dataset.selecting = String(selecting);
      lines.forEach(pair => {
        const related = selecting && (pair.edge.from === selected.key || pair.edge.to === selected.key);
        pair.line.dataset.related = String(!!related); pair.flow.dataset.related = String(!!related);
      });
      loopLayer.dataset.selecting = String(!!selected && !['run', 'learning'].includes(selected.type));
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
      reveal();
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
    function skillsOf(agent) { return skillOverride.get(agent.name) || agent.skills || []; }
    // An agent's skills. Read-only without actions.skills (or for an agent whose skills cannot be set here: a council
    // seat, a hub outside the roster, which then shows why); otherwise each skill row has a remove control, a closed
    // fold offers the installed skills it lacks, and one control gives it every skill.
    function skillSection(agent) {
      const everything = (snapshot.skills || []).filter(skill => skill && typeof skill.name === 'string');
      const about = new Map(everything.map(skill => [skill.name, skill.description]));
      const editable = !!skillAction && agent.skills_editable === true, list = skillsOf(agent), busy = skillPending.get(agent.name) || '';
      const control = (label, aria, request, key) => {
        const button = el('button', 'ag-text-button ag-skill-control', label); button.type = 'button';
        button.dataset.skillRequest = JSON.stringify(request); button.dataset.skillKey = key; button.setAttribute('aria-label', aria);
        if (busy) button.disabled = true;
        return button;
      };
      const row = (parent, name, on) => {
        const item = el('div', 'ag-skill-row'); item.dataset.skill = text(name);
        item.append(el('span', 'ag-skill-name', text(name)));
        if (about.get(name)) item.append(el('span', 'ag-skill-about', text(about.get(name))));
        item.title = text(about.get(name) || name);
        if (editable && on !== undefined) {
          const key = (on ? 'add:' : 'remove:') + name;
          if (busy === key) { item.dataset.pending = 'true'; item.setAttribute('aria-busy', 'true'); }
          item.classList.add('ag-skill-editable');
          item.append(control(on ? 'add' : 'remove', (on ? 'Add ' : 'Remove ') + name + (on ? ' to ' : ' from ') + agent.name, { agent: agent.name, skill: name, on }, key));
        }
        parent.append(item);
      };
      const fold = (parent, id, title, names, on) => {
        const item = el('details', 'ag-run-fold ag-skill-fold'); item.dataset.runFold = id; item.open = openFolds.has(id);
        const summary = el('summary'); summary.append(el('span', '', title), el('span', 'ag-fold-count', String(names.length)));
        item.append(summary);
        const fill = () => {
          if (item.querySelector('.ag-fold-body')) return;
          const body = el('div', 'ag-fold-body');
          appendList(body, names, name => row(body, name, on));
          item.append(body);
        };
        lazySteps.set(item, fill); if (item.open || names.some(name => busy.endsWith(':' + name))) { item.open = true; fill(); }
        parent.append(item);
      };
      let holder = null;
      if (list.includes('*')) {
        // "all" is every installed skill: say so, and let the reader open what that is.
        holder = section(detailScroll, 'Skills', 'all' + (everything.length ? ' · ' + everything.length : ''));
        if (everything.length) fold(holder, 'agent-skills', 'Installed skills', everything.map(skill => skill.name), editable ? false : undefined);
      } else if (list.length || editable) {
        holder = section(detailScroll, 'Skills · ' + list.length);
        appendList(holder, list, name => row(holder, name, editable ? false : undefined));
        if (editable) {
          const missing = everything.map(skill => skill.name).filter(name => !list.includes(name));
          if (missing.length) fold(holder, 'agent-skills-add', 'Add skills', missing, true);
          const every = control('Give it every skill', 'Give ' + agent.name + ' every installed skill', { agent: agent.name, all: true }, 'all');
          const line = el('div', 'ag-skill-all'); line.append(every);
          if (busy === 'all') { line.dataset.pending = 'true'; line.setAttribute('aria-busy', 'true'); }
          holder.append(line);
        }
      }
      if (!skillAction) return;
      if (!editable && agent.skills_reason) (holder || section(detailScroll, 'Skills')).append(el('p', 'ag-skill-reason', text(agent.skills_reason)));
      const notes = skillNotes.get(agent.name);
      if (notes?.length && holder) notes.forEach(note => { const line = el('p', 'ag-skill-note', note.text); line.dataset.kind = note.kind; holder.append(line); });
    }
    function redrawDetail() {
      if (!selected || selected.type !== 'agent') return;
      const key = document.activeElement?.closest?.('[data-skill-key]')?.dataset.skillKey;
      detailSignature = ''; renderDetail();
      if (key) Array.from(detailScroll.querySelectorAll('[data-skill-key]')).find(button => button.dataset.skillKey === key)?.focus({ preventScroll: true });
    }
    function skillKey(request) { return request.all ? 'all' : (request.on ? 'add:' : 'remove:') + request.skill; }
    // Ask the host for one skill change. `record` false for an undo. One change per agent at a time.
    function askSkill(request, record = true) {
      const agent = agentsByName.get(request.agent);
      if (!skillAction || !agent || skillPending.has(agent.name)) return Promise.reject(new Error('busy'));
      const before = skillsOf(agent).slice();
      skillPending.set(agent.name, skillKey(request)); skillNotes.delete(agent.name); redrawDetail();
      let answer;
      try { answer = Promise.resolve(skillAction(JSON.parse(JSON.stringify(request)))); } catch (error) { answer = Promise.reject(error); }
      return answer.then(async result => {
        const reply = result && typeof result === 'object' ? result : {};
        if (!alive) return reply;
        skillPending.delete(agent.name);
        const after = Array.isArray(reply.skills) ? reply.skills.filter(name => typeof name === 'string') : null;
        if (after) skillOverride.set(agent.name, after);
        const notes = reply.changed === false ? [{ kind: 'done', text: 'No change needed.' }] : [{ kind: 'done', text: 'Applies after OpenCode restarts.' }];
        (Array.isArray(reply.warnings) ? reply.warnings : []).forEach(warning => notes.push({ kind: 'warning', text: text(warning) }));
        skillNotes.set(agent.name, notes);
        if (record && reply.changed !== false) remember({ op: 'skill', agent: agent.name, request, before, after: after || before });
        redrawDetail();
        if (pending) await pending; // a snapshot asked for before the change may not show it
        await refresh();
        return reply;
      }, error => {
        if (alive) {
          skillPending.delete(agent.name);
          skillNotes.set(agent.name, [{ kind: 'error', text: message(error, 'The skills could not be changed') }]);
          redrawDetail();
        }
        throw error;
      });
    }
    // Undo one skill change: the opposite request, or (after "every skill") a remove for each skill it lacked before.
    function undoSkill(last) {
      const { agent, request, before } = last, installed = (snapshot.skills || []).map(skill => skill.name);
      const requests = request.all ? (before.includes('*') ? [] : installed.filter(name => !before.includes(name)).map(skill => ({ agent, skill, on: false })))
        : request.on ? [{ agent, skill: request.skill, on: false }]
        : before.includes('*') ? [{ agent, all: true }] : [{ agent, skill: request.skill, on: true }];
      requests.reduce((chain, next) => chain.then(() => askSkill(next, false)), Promise.resolve()).catch(() => { if (alive) flowHistory.push(last); });
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
    // One block for the root session: a line chart of the context over its turns, then plain facts.
    function renderWearDetail(info) {
      const wear = info.wear, section = runSection('Session', 'wear');
      const history = (Array.isArray(wear.history) ? wear.history : []).map(Number).filter(Number.isFinite).slice(-60);
      if (history.length) {
        const width = 240, height = 44, top = Math.max(info.limit * (info.toNext ? 1 : 0), ...history, 1);
        const point = (value, index) => [history.length > 1 ? index / (history.length - 1) * (width - 6) + 3 : width / 2, height - 3 - Math.max(0, value) / top * (height - 6)];
        const chart = svg('svg', { class: 'ag-wear-chart', viewBox: '0 0 ' + width + ' ' + height, preserveAspectRatio: 'none', role: 'img', 'aria-label': 'Context over the session' });
        const points = history.map(point);
        if (points.length > 1) chart.append(svg('polyline', { class: 'ag-wear-history', points: points.map(item => item[0].toFixed(1) + ',' + item[1].toFixed(1)).join(' ') }));
        chart.append(svg('circle', { class: 'ag-wear-last', cx: points[points.length - 1][0].toFixed(1), cy: points[points.length - 1][1].toFixed(1), r: 2.5 }));
        section.append(chart);
      }
      const fact = value => section.append(el('p', 'ag-wear-fact', value));
      fact('context now ' + amount(info.context) + (info.toNext ? ' · next summary at ' + amount(info.limit) : ' · window ' + amount(info.limit) + (wear.window_known === false ? ' (assumed)' : '') + ' · no automatic summary'));
      fact(info.summaries ? 'summarized ' + plural(info.summaries, 'time', 'times') : 'not summarized yet');
      if (wear.level === 'worn') fact('early work has been condensed ' + info.summaries + ' times; details from the start may be gone');
      const turns = Number(wear.turns), since = Number(wear.turns_since_summary);
      if (wear.turns != null && Number.isFinite(turns)) fact(plural(turns, 'turn', 'turns') + ' in all' + (info.summaries && wear.turns_since_summary != null && Number.isFinite(since) ? ' · ' + since + ' since the last summary' : ''));
      const per = wear.per_turn || {}, start = per.start == null ? NaN : Number(per.start), recent = per.recent == null ? NaN : Number(per.recent);
      const costs = [Number.isFinite(start) ? cost(start) + ' at the start' : '', Number.isFinite(recent) ? cost(recent) + ' now' : ''].filter(Boolean);
      if (costs.length) fact('cost per turn ' + costs.join(' · '));
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
      const wearInfo = wearOf(snapshot.root);
      if (wearInfo) renderWearDetail(wearInfo);
      const learning = snapshot.learning;
      if (learning) {
        const lesson = runSection('Learning', 'learning');
        const line = el('p', 'ag-learning-line', learningLine(learning));
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
      const catalogue = snapshot.skills || [];
      if (catalogue.length) runFold('Skills', 'skills', catalogue.length, body => appendList(body, catalogue, skill => {
        const users = visibleAgents.filter(agent => (agent.skills || []).includes(skill.name)).map(agent => agent.name);
        const row = el('div', 'ag-skill-row'); row.dataset.skill = text(skill.name);
        row.append(el('span', 'ag-skill-name', text(skill.name)), el('span', 'ag-skill-users', users.join(' · ') || '—'), el('span', 'ag-skill-about', text(skill.description)));
        row.title = text(skill.description); body.append(row);
      }));
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
      // An instruction being edited is never rebuilt under the user's hands; it is rebuilt once they leave it.
      const editing = selected.type === 'flow' ? detailScroll.querySelector('[data-route-text]') : null;
      if (editing && editing.dataset.routeText === selected.key && routeById(selected.key) && (document.activeElement === editing || editing.value !== editing.dataset.saved)) return;
      detailSignature = signature;
      const scroll = detailScroll.scrollTop;
      const focused = document.activeElement;
      const focusedStep = detailScroll.contains(focused) ? focused.closest('[data-step]')?.dataset.step : null;
      detailScroll.replaceChildren();
      if (selected.type === 'learning') {
        const learning = snapshot.learning;
        eyebrow.textContent = 'Loop';
        detailScroll.append(el('h2', 'ag-detail-title', 'Learning'));
        if (learning) {
          const line = el('p', 'ag-detail-intro ag-learning-line', learningLine(learning)); line.dataset.due = String(!!learning.due); detailScroll.append(line);
          cells(detailScroll, [['Notes', text(learning.total)], ['New', text(learning.pending)], ['Applied', text(learning.improvements)], ['Runs', text(learning.runs)]]);
          const rate = side => learning.trend?.[side]?.first_pass_rate == null ? '—' : Math.round(learning.trend[side].first_pass_rate * 100) + '%';
          const rounds = side => learning.trend?.[side]?.mean_attempts == null ? '—' : Number(learning.trend[side].mean_attempts).toFixed(1);
          if (learning.trend) cells(section(detailScroll, 'Since the last change'), [['First pass', rate('before') + ' → ' + rate('after')], ['Rounds', rounds('before') + ' → ' + rounds('after')]]);
          const said = learning.notes || [], waiting = said.slice(0, Number(learning.pending) || 0), answered = said.slice(waiting.length);
          const rows = (parent, list) => appendList(parent, list, note => { const row = el('div', 'ag-note-row'); row.append(el('p', 'ag-note', text(note.text)), el('span', 'ag-note-meta', [note.agent, ago(note.time)].filter(Boolean).join(' · '))); row.title = text(note.text); parent.append(row); });
          if (waiting.length) rows(section(detailScroll, 'New'), waiting);
          if (learning.changes?.length) { const applied = section(detailScroll, 'Applied'); appendList(applied, learning.changes, change => { const row = el('div', 'ag-history-row'); row.append(el('span', 'ag-history-when', ago(change.time)), el('span', 'ag-history-what', text(change.summary)), el('span', 'ag-history-note', text(change.file))); applied.append(row); }); }
          if (answered.length) rows(section(detailScroll, 'Answered'), answered);
          if (learning.lessons?.length) section(detailScroll, 'Lessons', learning.lessons);
        } else detailScroll.append(el('p', 'ag-detail-intro', 'None'));
      } else if (selected.type === 'flow') {
        const flow = routeById(selected.key);
        eyebrow.textContent = 'Flow';
        detailScroll.append(el('h2', 'ag-detail-title ag-route-title', flow ? flow.path.join(' → ') : 'Unavailable'));
        if (flow) {
          const item = el('section', 'ag-detail-section ag-route-detail'), box = el('textarea', 'ag-route-text');
          box.value = flow.text; box.dataset.routeText = flow.id; box.dataset.saved = flow.text;
          box.rows = 6; box.spellcheck = false; box.setAttribute('aria-label', 'Instruction');
          item.append(el('h3', '', 'Instruction'), box);
          if (flowAction) {
            const actions = el('div', 'ag-route-actions'), state = el('p', 'ag-route-state', 'Saved'), remove = el('button', 'ag-text-button ag-route-remove', 'Remove');
            state.dataset.routeState = 'saved'; state.setAttribute('aria-live', 'polite');
            remove.type = 'button'; remove.dataset.routeRemove = flow.id; remove.setAttribute('aria-label', 'Remove this flow');
            actions.append(state, remove); item.append(actions);
          } else box.readOnly = true;
          detailScroll.append(item);
        }
      } else if (selected.type === 'work-loop') {
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
        detailScroll.append(el('h2', 'ag-detail-title', agent.name));
        if (agent.about) detailScroll.append(el('p', 'ag-detail-about', text(agent.about)));
        detailScroll.append(el('p', 'ag-detail-intro', agent.model || '—'));
        const badges = el('div', 'ag-detail-badges');
        badges.append(el('span', 'ag-badge', agent.busy ? agent.busy + ' active' : 'Idle'));
        if (agent.effort) badges.append(el('span', 'ag-badge', agent.effort + ' effort'));
        if (agent.pool) badges.append(el('span', 'ag-badge', 'Pool of ' + agent.pool));
        detailScroll.append(badges);
        stats(detailScroll, usageOf(agent.sessions));
        skillSection(agent);
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

    function stagePoint(clientX, clientY) {
      const rect = viewport.getBoundingClientRect();
      return { x: (clientX - rect.left - camera.x) / camera.scale, y: (clientY - rect.top - camera.y) / camera.scale };
    }
    function agentAt(clientX, clientY) {
      const point = stagePoint(clientX, clientY);
      let found = '', best = Infinity;
      visibleAgents.forEach(agent => {
        const p = positions.get(agent.name);
        if (!p || !nodes.has(agent.name)) return;
        const distance = Math.hypot(point.x - p.x, point.y - p.y);
        if (distance <= p.r + 8 && distance < best) { best = distance; found = agent.name; }
      });
      return found;
    }
    function showWire(from, x, y) {
      const p = positions.get(from);
      if (!p) { wire.style.display = 'none'; return; }
      [['x1', p.x], ['y1', p.y], ['x2', x], ['y2', y]].forEach(([key, value]) => wire.setAttribute(key, value));
      wire.style.display = '';
    }
    // Only a valid next step is highlighted under the pointer: any agent but the newest step (a seat stands for its council).
    function aim(name) {
      const step = name && trail.length && !trailPending ? standIn(name) : '';
      const next = step && step !== trail.at(-1) ? step : '';
      if (next === hovered) return;
      const old = nodes.get(hovered);
      if (old) delete old.dataset.target;
      hovered = next;
      if (hovered && nodes.get(hovered)) nodes.get(hovered).dataset.target = 'true';
    }
    function setTitle(name) {
      const title = nodes.get(name)?.querySelector('title');
      if (title) title.textContent = flowErrors.get(name) || name;
    }
    function markArmed() {
      nodes.forEach((node, key) => { node.dataset.armed = String(!trailPending && trail.at(-1) === key); node.dataset.trail = String(trail.includes(key)); });
      highlight();
    }
    function announce() {
      trailStatus.textContent = !trail.length ? '' : trailPending ? 'Adding the flow ' + trail.join(' → ')
        : trail.length > 1 && isHubAgent(trail.at(-1)) ? 'Flow so far: ' + trail.join(' → ') + '. ' + trail.at(-1) + ' ends it: Enter or a click on it adds the flow, Backspace removes the last step, Escape cancels.'
        : 'Flow so far: ' + trail.join(' → ') + '. Click or press Enter on another agent to add a step; Enter on ' + trail.at(-1) + ' finishes, Backspace removes the last step, Escape cancels. With one agent, a double-click or l adds its loop on itself.';
      if (trail.length) root.dataset.agTrail = trail.join(' '); else delete root.dataset.agTrail;
    }
    function trailChanged() {
      const last = trail.at(-1), p = last && positions.get(last);
      if (p && !trailPending) showWire(last, p.x, p.y); else wire.style.display = 'none';
      aim(''); updateTrail(); markArmed(); announce();
    }
    function startTrail(name) {
      const step = standIn(name);
      if (!step || trailPending) return;
      loneCancel = '';
      trail = [step]; flowErrors.delete(step); setTitle(step); trailChanged();
    }
    function dropTrail() { if (trailPending) return; trail = []; trailChanged(); }
    function backStep() { if (trailPending) return; trail.pop(); trailChanged(); }
    function addStep(name) {
      const step = standIn(name);
      if (!step || trailPending || !trail.length) return;
      // A second click on a lone armed agent cancels it; a double-click there then makes its loop on itself.
      if (step === trail.at(-1)) { loneCancel = trail.length === 1 ? step : ''; finishTrail(); return; }
      // The hub can only end a path: once it is the newest step, only finishing it, Backspace or Escape is left.
      if (trail.length > 1 && isHubAgent(trail.at(-1))) { refuseFlow(step, trail.at(-1) + ' ends this flow: press Enter or click it again to add the flow'); return; }
      trail.push(step); trailChanged();
    }
    // A double-click (or `l`) on an agent with no path pending: its loop on itself, added once; if it has one,
    // that flow is selected instead. It is never removed or doubled this way.
    function loopOnItself(name) {
      const step = standIn(name);
      if (!flowAction || !step || trailPending) return;
      loneCancel = '';
      const own = routes().find(flow => isSelfLoop(flow.path) && flow.path[0] === step);
      if (own) { if (trail.length) dropTrail(); select('flow', own.id, routeViews.get(own.id)?.group); return; }
      addFlow([step, step]);
    }
    function cancelPress() { press = null; }
    // Escape during a drag: the agent goes back to where it was, the canvas back to where it was, and nothing else.
    function cancelDrag() {
      if (press?.moved) {
        const current = press; press = null;
        applyMoves(layoutKey(), current.before, false);
        if (arrangement === 'compact') { clampPan(); transform(); }
      } else if (drag?.moved) {
        camera.x = drag.x; camera.y = drag.y; clampPan(); transform(); drag = null;
      } else return false;
      viewport.dataset.dragging = 'false'; swallowClick = true; lastClick = null; cleanPresses = 0;
      if (trail.length && !trailPending) trailChanged();
      return true;
    }
    function clearHold() { if (hold?.timer) { clearTimeout(hold.timer); timers.delete(hold.timer); } hold = null; }
    // What a right-click, a long press or `i` opens: a node (the hub's learning ring opens learning), a drawn flow,
    // a review loop or a work loop. Anything else (empty canvas, the controls) is not a target.
    function infoTarget(element) {
      if (!element?.closest || !viewport.contains(element)) return null;
      if (element.closest('[data-learning]')) return { type: 'learning', key: '', origin: element.closest('[data-learning]') };
      const node = element.closest('[data-agent]');
      if (node && nodeLayer.contains(node)) return { type: 'agent', key: node.dataset.agent, origin: node };
      const route = element.closest('[data-route]');
      if (route) return { type: 'flow', key: route.dataset.route, origin: route };
      const work = element.closest('[data-work-loop-id]');
      if (work) return { type: 'work-loop', key: work.dataset.workLoopId, origin: work };
      const loop = element.closest('[data-loop-id]');
      if (loop) return { type: 'loop', key: loop.dataset.loopId, origin: loop };
      return null;
    }
    // Opening details touches nothing else: a pending path stays pending.
    function openInfo(target) {
      if (target.type === 'agent') activateAgent(target.key, target.origin);
      else select(target.type, target.key, target.origin);
    }
    function pickFlow(id) { if (pickedFlow === id) return; pickedFlow = id; highlight(); }
    // What Delete or Backspace is about: the focused node, flow or loop, else the picked flow, else the flow whose
    // details are open.
    function deleteTarget(element) {
      const focused = infoTarget(element);
      if (focused) return focused;
      if (pickedFlow && routeById(pickedFlow)) return { type: 'flow', key: pickedFlow };
      if (selected?.type === 'flow' && routeById(selected.key)) return { type: 'flow', key: selected.key };
      return null;
    }
    // How each kind of target is deleted, or null when it cannot be. Only the user's own lines go today: a drawn
    // flow or loop on itself, through actions.flow. The hub's and council's lines and everything drawn from a
    // live task graph are not the user's data; a node (turning an agent off) is a later addition.
    function deleter(target) {
      switch (target?.type) {
        case 'flow': return flowAction && routeById(target.key) ? () => removeFlow(target.key) : null;
        case 'agent': return null;
        default: return null; // a review or work loop, the learning ring
      }
    }
    function infoKey(event) { return (event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey) || (event.key.toLowerCase() === 'i' && !event.shiftKey)) && !event.ctrlKey && !event.metaKey && !event.altKey; }
    function message(error, fallback) { return text(error && typeof error === 'object' && 'message' in error ? error.message : error).replace(/\s+/g, ' ').trim() || fallback; }
    function refuseFlow(name, error) {
      if (!name) return;
      flowErrors.set(name, message(error, 'The flow could not be changed')); setTitle(name);
      const node = nodes.get(name);
      if (node) { node.dataset.shake = 'true'; later(() => { delete node.dataset.shake; }, 400); }
    }
    // Ask the host for a change; `before` runs on its answer, before the next snapshot is drawn.
    function askFlow(request, before) {
      busyFlows++;
      let answer;
      try { answer = Promise.resolve(flowAction(request)); } catch (error) { answer = Promise.reject(error); }
      return answer.then(async result => {
        const reply = result && typeof result === 'object' ? result : {};
        if (!alive) return reply;
        if (before) before(reply);
        if (pending) await pending; // a snapshot asked for before the change may not show it
        await refresh();
        return reply;
      }).finally(() => { busyFlows--; });
    }
    function remember(entry) { flowHistory.push(entry); if (flowHistory.length > UNDO_STEPS) flowHistory.shift(); }
    function finishTrail() {
      if (trailPending) return; // the same path is never asked for twice
      const path = trail.slice();
      if (path.length < 2) { dropTrail(); return; }
      addFlow(path);
    }
    function addFlow(path) {
      if (trailPending) return;
      trail = path.slice(); trailPending = true; trailChanged();
      askFlow({ op: 'add', path }, reply => {
        if (reply.flow?.id) freshRoutes.add(reply.flow.id);
        trailPending = false; trail = []; trailChanged();
      }).then(reply => {
        if (!alive) return;
        const flow = reply.flow;
        if (flow?.id) {
          remember({ op: 'add', id: flow.id, path: flow.path || path, text: flow.text });
          if (routeById(flow.id)) {
            // A flow just made opens its details with the instruction box focused and its made-up text selected:
            // typing replaces it, and Escape or a click away keeps it.
            select('flow', flow.id, routeViews.get(flow.id)?.group);
            const box = [...detailScroll.querySelectorAll('[data-route-text]')].find(item => item.dataset.routeText === flow.id);
            if (box && !box.readOnly) { box.focus({ preventScroll: true }); box.select(); }
          }
        }
      }, error => {
        if (!alive) return;
        trailPending = false; trail = []; trailChanged();
        refuseFlow(path[0], error);
      });
    }
    function setRouteState(kind, said) {
      const state = detailScroll.querySelector('[data-route-state]');
      if (!state) return;
      state.dataset.routeState = kind;
      state.textContent = said || { saved: 'Saved', unsaved: 'Not saved', saving: 'Saving' }[kind];
      state.title = state.textContent;
    }
    // The instruction's limit counts characters (code points), as the command does, not UTF-16 units.
    function overLimit(value) { const length = [...value].length; return length > FLOW_TEXT_CHARS ? 'Too long: ' + length + ' of ' + FLOW_TEXT_CHARS + ' characters' : ''; }
    function saveRouteText(box) {
      if (!flowAction || box.readOnly) return;
      // A save asked for while one is out waits for its answer, then goes, so a newer edit is never dropped.
      if (box.dataset.saving === 'true') { box.dataset.queued = 'true'; return; }
      const id = box.dataset.routeText, before = box.dataset.saved, value = box.value;
      if (value === before) return;
      const long = overLimit(value);
      if (long) { setRouteState('error', long); return; }
      const next = () => {
        if (box.dataset.queued !== 'true') return;
        delete box.dataset.queued;
        if (alive && box.isConnected && box.value !== box.dataset.saved) saveRouteText(box);
      };
      box.dataset.saving = 'true'; setRouteState('saving');
      askFlow({ op: 'text', id, text: value }, reply => {
        // A blank text comes back as the one made from the path.
        box.dataset.saved = typeof reply.flow?.text === 'string' ? reply.flow.text : value;
        if (box.value === value) box.value = box.dataset.saved;
      }).then(() => {
        box.dataset.saving = 'false';
        remember({ op: 'text', id, before, after: box.dataset.saved });
        setRouteState(box.value === box.dataset.saved ? 'saved' : 'unsaved');
        next();
      }, error => {
        box.dataset.saving = 'false';
        setRouteState('error', message(error, 'Not saved'));
        next();
      });
    }
    function removeFlow(id) {
      const flow = routeById(id);
      if (!flowAction || !flow) return;
      // Undo puts back the newest instruction: one being saved as the remove goes, else the last one saved.
      const box = [...detailScroll.querySelectorAll('[data-route-text]')].find(item => item.dataset.routeText === id);
      const undoText = box && !box.readOnly ? (box.dataset.saving === 'true' && !overLimit(box.value) ? box.value : box.dataset.saved) : flow.text;
      askFlow({ op: 'remove', id }).then(() => {
        if (!alive) return;
        remember({ op: 'remove', id, path: flow.path, text: undoText });
        if (selected?.type === 'flow' && selected.key === id) closeDetails();
      }, error => {
        if (!alive) return;
        // The flow stays; its details say why, as a line.
        refuseFlow(flow.path[0], error);
        if (routeById(id) && !(selected?.type === 'flow' && selected.key === id)) select('flow', id, routeViews.get(id)?.group);
        setRouteState('error', message(error, 'Not removed'));
      });
    }
    function undoFlow() {
      const last = flowHistory.pop();
      if (!last) return;
      if (last.op === 'skill') { undoSkill(last); return; }
      if (last.op === 'move') { applyMoves(last.layout, last.before, false); return; }
      const request = last.op === 'add' ? { op: 'remove', id: last.id } : last.op === 'remove' ? { op: 'add', path: last.path, text: last.text } : { op: 'text', id: last.id, text: last.before };
      askFlow(request, reply => {
        // A flow put back gets a new file, so later steps that name it follow it there.
        if (last.op === 'remove' && reply.flow?.id) flowHistory.forEach(entry => { if (entry.id === last.id) entry.id = reply.flow.id; });
        if (last.op !== 'add' && selected?.type === 'flow' && selected.key === last.id && reply.flow?.id) selected.key = reply.flow.id;
      }).then(() => {
        if (alive && last.op === 'add' && selected?.type === 'flow' && selected.key === last.id) closeDetails();
      }, error => {
        if (!alive) return;
        flowHistory.push(last);
        refuseFlow((last.path || routeById(last.id)?.path || [])[0], error);
      });
    }
    // A click (or Enter) on an agent while flows can be drawn: start a path, add a step, or finish on the newest.
    function pressAgent(name) {
      if (trailPending) return;
      if (!trail.length) startTrail(name);
      else addStep(name);
    }
    // A clean pointer click on an agent. Two on the same lone agent within DOUBLE_MS, with no path pending before
    // the first, are a double-click: the second makes its loop on itself at once (never "cancel"), and the
    // browser's dblclick that follows is swallowed. A slower second click cancels the lone agent as before.
    function clickAgent(name) {
      const step = standIn(name), now = performance.now();
      if (step && !trailPending && trail.length === 1 && trail[0] === step && lastClick?.step === step && lastClick.lone && now - lastClick.at <= DOUBLE_MS) {
        lastClick = null; swallowDouble = true; loopOnItself(step); return;
      }
      const lone = !trail.length;
      pressAgent(name);
      lastClick = step ? { step, at: now, lone: lone && trail.length === 1 && trail[0] === step } : null;
    }
    function focusNext(name, step) {
      const list = Array.from(nodes.values()).filter(node => node.isConnected), index = list.indexOf(nodes.get(name));
      if (list.length) list[(index + step + list.length) % list.length].focus({ preventScroll: true });
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
      const skillButton = event.target.closest('[data-skill-request]');
      if (skillButton) { if (!skillButton.disabled) askSkill(JSON.parse(skillButton.dataset.skillRequest)).catch(() => {}); return; }
      const removeRoute = event.target.closest('[data-route-remove]');
      if (removeRoute) { removeFlow(removeRoute.dataset.routeRemove); return; }
      const link = event.target.closest('[data-flow-peer]');
      if (link) { select('agent', link.dataset.flowPeer, nodes.get(link.dataset.flowPeer)); detailScroll.scrollTop = 0; }
    });
    // The instruction is saved when focus leaves it or on Ctrl/Cmd+Enter; its state shows beside it.
    listen(detailScroll, 'input', event => { const box = event.target.closest('[data-route-text]'); if (box && box.dataset.saving !== 'true') { const long = overLimit(box.value); if (long) setRouteState('error', long); else setRouteState(box.value === box.dataset.saved ? 'saved' : 'unsaved'); } });
    listen(detailScroll, 'focusout', event => { const box = event.target.closest('[data-route-text]'); if (box) saveRouteText(box); });
    listen(detailScroll, 'keydown', event => {
      const box = event.target.closest('[data-route-text]');
      if (box && event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); saveRouteText(box); }
    });
    listen(root, 'keydown', event => {
      if (event.key === 'Escape' && (press?.moved || drag?.moved)) { event.preventDefault(); cancelDrag(); return; }
      const typing = !!event.target.closest('textarea, input, select');
      if (!typing && !event.defaultPrevented && (event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && event.key.toLowerCase() === 'z') {
        if (flowHistory.length) { event.preventDefault(); undoFlow(); }
        return;
      }
      if ((flowAction || skillAction) && !typing && !event.defaultPrevented) {
        const plain = !event.ctrlKey && !event.metaKey && !event.altKey;
        // Delete and Backspace edit text in a field (excluded above) and do nothing on the details panel's controls.
        const inPanel = !!event.target.closest('.ag-detail');
        if (event.key === 'Escape' && pickedFlow && !trail.length && !press && !inPanel) { pickFlow(''); }
        if (event.key === 'Escape' && (trail.length || press)) { event.preventDefault(); if (press?.moved) finishMove(press.before); cancelPress(); dropTrail(); return; }
        if (event.key === 'Backspace' && plain && trail.length && !inPanel) { event.preventDefault(); backStep(); return; }
        if (event.key === 'Enter' && plain && trail.length && !event.target.closest('.ag-detail, button, [data-agent]')) { event.preventDefault(); finishTrail(); return; }
        if ((event.key === 'Delete' || event.key === 'Backspace') && plain && !trail.length && !inPanel) {
          const remove = deleter(deleteTarget(event.target));
          if (remove) { event.preventDefault(); remove(); }
          return;
        }
      }
      if (event.key === 'Escape' && selected) { event.preventDefault(); closeDetails(); }
    });
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
      const ring = event.target.closest('[data-learning]');
      if (ring) { select('learning', '', ring); return; }
      const inspect = event.target.closest('[data-agent-inspect]');
      if (inspect) { select('agent', inspect.dataset.agentInspect, nodes.get(inspect.dataset.agentInspect)); return; }
      const node = event.target.closest('[data-agent]');
      // A left click builds (with actions.flow); it never opens details, which a right-click does.
      if (node && !suppressClick && flowAction) pressAgent(node.dataset.agent);
    });
    // Moving between agents with the keyboard while a path is pending draws the line to the focused one.
    function aimAtFocus() {
      const node = document.activeElement?.closest?.('[data-agent]'), p = node && nodeLayer.contains(node) && positions.get(node.dataset.agent);
      if (!flowAction || !trail.length || trailPending || !p) return;
      showWire(trail.at(-1), p.x, p.y); aim(node.dataset.agent);
    }
    listen(nodeLayer, 'focusin', aimAtFocus);
    // A right-click (two-finger click, Ctrl+click on a Mac) on a node, a flow or a loop opens its details and
    // replaces the browser's menu there only. It is never a click: the press it began is forgotten, so it does
    // not arm, connect, finish or cancel a path, start a drag or count towards a double-click.
    listen(root, 'contextmenu', event => {
      if (keyMenu) { keyMenu = false; event.preventDefault(); return; }
      // During a drag a right-click is ignored: the drag goes on and no menu opens.
      if (press?.moved || drag?.moved) { event.preventDefault(); return; }
      const target = infoTarget(event.target);
      if (!target) return;
      event.preventDefault();
      if (hold && !hold.fired) { clearTimeout(hold.timer); timers.delete(hold.timer); hold.timer = null; hold.fired = true; openInfo(target); return; }
      if (hold?.fired) return;
      if (press && !press.moved) press = null;
      if (drag && !drag.moved) drag = null;
      openInfo(target);
    });
    listen(viewport, 'keydown', event => {
      const ring = event.target.closest('[data-learning]');
      if (ring && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); select('learning', '', ring); return; }
      const inspect = event.target.closest('[data-agent-inspect]');
      if (inspect && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); select('agent', inspect.dataset.agentInspect, nodes.get(inspect.dataset.agentInspect)); return; }
      const work = event.target.closest('[data-work-loop-id]');
      if (work && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); select('work-loop', work.dataset.workLoopId, work); return; }
      const loop = event.target.closest('[data-loop-id]');
      if (loop && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); select('loop', loop.dataset.loopId, loop); return; }
      const route = event.target.closest('[data-route]');
      if (route && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); select('flow', route.dataset.route, route); return; }
      // `i`, the context-menu key and Shift+F10 open the focused node's, flow's or loop's details; nothing else changes.
      if (infoKey(event)) {
        const target = infoTarget(event.target);
        if (target) {
          event.preventDefault();
          if (event.key !== 'i' && event.key !== 'I') { keyMenu = true; later(() => { keyMenu = false; }, 600); }
          openInfo(target); return;
        }
      }
      const node = event.target.closest('[data-agent]');
      // Shift+Enter opens an agent's details whatever is pending; `r` puts every agent back in its automatic place.
      if (node && event.key === 'Enter' && event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) { event.preventDefault(); activateAgent(node.dataset.agent, node); return; }
      if ((node || event.target === viewport) && event.key.toLowerCase() === 'r' && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) { event.preventDefault(); resetMoves(); return; }
      if (node && flowAction && !event.ctrlKey && !event.metaKey && !event.altKey) {
        const key = node.dataset.agent;
        if ((event.key === 'Enter' || event.key === ' ') && trail.length) { event.preventDefault(); pressAgent(key); return; }
        if (event.key.toLowerCase() === 'c' && !event.shiftKey) { event.preventDefault(); if (trail.length === 1 && trail[0] === standIn(key)) dropTrail(); else startTrail(key); return; }
        // `l`: the keyboard's double-click, the agent's loop on itself (only with no path of two or more pending).
        if (event.key.toLowerCase() === 'l' && !event.shiftKey) { event.preventDefault(); if (trail.length <= 1 && !trailPending) loopOnItself(key); return; }
        if (!event.shiftKey && event.key.startsWith('Arrow')) { event.preventDefault(); focusNext(key, event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : -1); aimAtFocus(); return; }
      }
      if (node && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); activateAgent(node.dataset.agent, node, event.shiftKey); return; }
      // Alt+arrow (or Shift+arrow) moves the focused agent; the presses in a row on one agent undo as one move.
      if (node && (event.altKey || event.shiftKey) && !event.ctrlKey && !event.metaKey && event.key.startsWith('Arrow')) {
        const key = node.dataset.agent, p = positions.get(key);
        if (!p) return;
        stopLayout(); let x = p.x, y = p.y;
        if (event.key === 'ArrowLeft') x -= NUDGE_PX;
        else if (event.key === 'ArrowRight') x += NUDGE_PX;
        else if (event.key === 'ArrowUp') y -= NUDGE_PX;
        else if (event.key === 'ArrowDown') y += NUDGE_PX;
        else return;
        event.preventDefault(); camera.touched = true;
        const before = copyMoves(); moveNode(key, x, y); finishMove(before, { agent: key, by: 'key' });
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
      keyMenu = false;
      if (event.button !== 0 && event.pointerType !== 'touch') return;
      // While an agent is pressed or dragged, a second pointer is ignored.
      if (press && press.id !== event.pointerId) return;
      swallowClick = false; swallowDouble = false;
      if (event.target.closest('[data-learning]')) return;
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      clearHold();
      // Touch: a press held still opens the details of what it is on; one that moves first is a drag or a pan.
      const touched = event.pointerType === 'touch' && pointers.size === 1 && infoTarget(event.target);
      if (touched) {
        const id = event.pointerId;
        hold = { id, x: event.clientX, y: event.clientY, fired: false, timer: null };
        hold.timer = later(() => { if (hold?.id === id && !hold.fired) { hold.timer = null; hold.fired = true; openInfo(touched); } }, LONG_PRESS_MS);
      }
      if (pointers.size === 2) {
        const [a, b] = Array.from(pointers.values()), rect = viewport.getBoundingClientRect();
        pinch = { distance: Math.hypot(a.x - b.x, a.y - b.y), scale: camera.scale, x: ((a.x + b.x) / 2 - rect.left - camera.x) / camera.scale, y: ((a.y + b.y) / 2 - rect.top - camera.y) / camera.scale };
        drag = null; suppressClick = true;
        if (press?.moved) finishMove(press.before);
        cancelPress(); clearHold();
      } else if (pointers.size === 1 && event.target.closest('[data-agent]')) {
        // On an agent a press is a click, whatever its length; once it moves past MOVE_PX it moves the agent
        // instead, and then it never arms, selects, starts or finishes a path.
        const node = event.target.closest('[data-agent]'), key = node.dataset.agent, p = positions.get(key);
        cancelPress(); drag = null; suppressClick = false;
        press = { id: event.pointerId, key, startX: event.clientX, startY: event.clientY, x: p ? p.x : 0, y: p ? p.y : 0, moved: false, before: null };
        node.focus({ preventScroll: true });
      } else if (pointers.size === 1) {
        const loop = event.target.closest('[data-loop-id], [data-work-loop-id], [data-route]');
        drag = { id: event.pointerId, route: loop?.dataset.route || '', startX: event.clientX, startY: event.clientY, x: camera.x, y: camera.y, moved: false };
        suppressClick = false;
        if (loop) loop.focus({ preventScroll: true });
      }
      viewport.setPointerCapture(event.pointerId);
    });
    listen(viewport, 'pointermove', event => {
      if (flowAction && trail.length && !trailPending && !press) { const point = stagePoint(event.clientX, event.clientY); showWire(trail.at(-1), point.x, point.y); aim(agentAt(event.clientX, event.clientY)); }
      if (!pointers.has(event.pointerId)) return;
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (hold?.id === event.pointerId && !hold.fired && Math.hypot(event.clientX - hold.x, event.clientY - hold.y) > MOVE_PX) clearHold();
      if (pinch && pointers.size >= 2) {
        const [a, b] = Array.from(pointers.values()), rect = viewport.getBoundingClientRect();
        camera.scale = clamp(pinch.scale * Math.hypot(a.x - b.x, a.y - b.y) / Math.max(1, pinch.distance), .2, 3);
        camera.x = (a.x + b.x) / 2 - rect.left - pinch.x * camera.scale;
        camera.y = (a.y + b.y) / 2 - rect.top - pinch.y * camera.scale;
        camera.touched = true; transform(); return;
      }
      if (press && press.id === event.pointerId) {
        const dx = event.clientX - press.startX, dy = event.clientY - press.startY;
        if (!press.moved && Math.hypot(dx, dy) <= MOVE_PX) return;
        if (!positions.has(press.key)) return;
        if (!press.moved) { press.moved = true; press.before = copyMoves(); stopLayout(); suppressClick = true; camera.touched = true; viewport.dataset.dragging = 'true'; }
        moveNode(press.key, press.x + dx / camera.scale, press.y + dy / camera.scale);
        // A pending path stays as it was: its line waits at the newest step instead of following the pointer.
        const last = flowAction && trail.length && !trailPending && positions.get(trail.at(-1));
        if (last) { showWire(trail.at(-1), last.x, last.y); aim(''); }
        return;
      }
      if (!drag || drag.id !== event.pointerId) return;
      const dx = event.clientX - drag.startX, dy = event.clientY - drag.startY;
      if (!drag.moved && Math.hypot(dx, dy) < 4) return;
      drag.moved = true; camera.touched = true; suppressClick = true; viewport.dataset.dragging = 'true';
      camera.x = drag.x + dx; camera.y = drag.y + dy; clampPan(); transform();
    });
    function pointerUp(event) {
      if (!pointers.has(event.pointerId)) return;
      // A long press already opened details: its release is not a click.
      const held = hold?.id === event.pointerId && hold.fired;
      clearHold();
      if (press && press.id === event.pointerId) {
        const current = press; cancelPress();
        if (current.moved) {
          // Dropped where it is: the camera does not move, so the agent stays under the pointer. The browser's
          // click (and dblclick) after it is swallowed, and the double-click timer starts again.
          swallowClick = true; lastClick = null;
          finishMove(current.before);
          if (arrangement === 'compact') { clampPan(); transform(); }
          if (trail.length && !trailPending) trailChanged();
        } else if (event.type === 'pointerup' && !pinch && !held) {
          cleanPresses++;
          // A click builds: on a canvas that draws flows it starts a path or adds a step. It never opens details.
          pickFlow('');
          if (flowAction) clickAgent(current.key);
        }
        suppressClick = true;
      }
      if (drag?.moved) { cleanPresses = 0; swallowClick = true; lastClick = null; } else if (event.type === 'pointerup' && drag && !pinch && !held) cleanPresses++;
      // A click on empty canvas (or on a line) while a path is pending drops the whole path. Otherwise a click
      // on a drawn flow picks it (Delete removes it), and anywhere else lets it go.
      if (event.type === 'pointerup' && drag && !drag.moved && !pinch && !held) {
        if (trail.length) dropTrail();
        else pickFlow(drag.route && routeById(drag.route) ? drag.route : '');
      }
      if (drag && !drag.moved) suppressClick = true;
      pointers.delete(event.pointerId); pinch = null; drag = null; viewport.dataset.dragging = 'false';
      if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
    }
    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(name => listen(viewport, name, pointerUp));
    // The click a browser fires after a drag (or an Escape-cancelled drag) ends is not a click.
    listen(viewport, 'click', event => { if (swallowClick) { swallowClick = false; event.stopPropagation(); event.preventDefault(); } }, true);
    // A double-click is two clicks first: on an agent with no path the second click has already made its loop on
    // itself (see clickAgent); this only catches a double-click slower than DOUBLE_MS (a long system setting),
    // where the second click cancelled. With a path pending the two clicks have already finished it there.
    // A double-click on empty canvas puts every agent back in its automatic place. One whose clicks were part of a
    // drag does nothing.
    listen(viewport, 'dblclick', event => {
      if (swallowDouble) { swallowDouble = false; event.preventDefault(); loneCancel = ''; return; }
      if (cleanPresses < 2) { loneCancel = ''; return; }
      const name = event.target.closest?.('[data-agent]')?.dataset.agent || agentAt(event.clientX, event.clientY);
      if (!name && !event.target.closest?.('[data-route], [data-loop-id], [data-work-loop-id], [data-learning], button')) { event.preventDefault(); resetMoves(); return; }
      if (!flowAction || trailPending) return;
      const step = name && standIn(name);
      if (!step) return;
      if ((!trail.length && loneCancel === step) || (trail.length === 1 && trail[0] === step)) { event.preventDefault(); loopOnItself(step); }
      loneCancel = '';
    });

    function render(data) {
      const previousTeam = activeTeam;
      snapshot = { ...data, agents: prepareRoster(data) };
      const stamp = JSON.stringify([data.agents, data.graph, data.warnings, data.totals, data.escalation, data.root?.wear, data.structure, data.skill_warnings, (data.skills || []).length]);
      if (stamp !== renderStamp) { renderStamp = stamp; renderRevision++; }
      // The host's answer stands for an agent's skills only until a snapshot shows the same list.
      skillOverride.forEach((list, name) => { const agent = (data.agents || []).find(item => item?.name === name); if (!agent || JSON.stringify(agent.skills) === JSON.stringify(list)) skillOverride.delete(name); });
      indexSteps();
      if (activeTeam !== previousTeam) { rememberTeam(previousTeam); restoreTeam(activeTeam); }
      if (trail.some(name => !shownAgents.has(name))) dropTrail();
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
      // A flow removed elsewhere closes its details; one this view is changing waits for the answer.
      if (selected?.type === 'flow' && !routeById(selected.key) && !busyFlows) closeDetails();
      else renderDetail();
      highlight();
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
      cancelPress(); trail = []; trailPending = false; hovered = ''; wire.style.display = 'none';
      freshRoutes.clear(); flowErrors.clear(); flowHistory.length = 0; routeViews.clear(); trailStatus.textContent = ''; delete root.dataset.agTrail;
      selected = null; selectedOrigin = null; initialSelection = ''; highlightedStep = ''; detailSignature = '';
      teamChoice = null; activeTeam = 'main'; teamChoices = []; layoutSignature = ''; renderStamp = '';
      arrangement = root.clientWidth > 0 && root.clientWidth <= 650 ? 'compact' : 'wide';
      visibleAgents = []; hubName = ''; openSteps.clear(); openFolds.clear(); teamViews.clear(); positions.clear(); nodes.clear(); lines.clear(); loopViews.clear(); workLoopViews.clear();
      agentsByName.clear(); stepsByAgent.clear(); stepsById.clear(); nodeOwners.clear(); shownAgents.clear();
      nodeLayer.replaceChildren(); spokeLayer.replaceChildren(); escalationLayer.replaceChildren(); flowLayer.replaceChildren(); loopLayer.replaceChildren(); routeLayer.replaceChildren(); trailLayer.replaceChildren();
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
      alive = false; generation++; stopLayout(); if (resizeFrame !== null) cancelAnimationFrame(resizeFrame); clearInterval(timer); timers.forEach(clearTimeout); timers.clear(); resize.disconnect(); schemeObserver.disconnect(); cleanups.forEach(fn => fn());
      pointers.forEach((_, id) => { if (viewport.hasPointerCapture(id)) viewport.releasePointerCapture(id); });
      pointers.clear(); teamViews.clear(); root.replaceChildren(); root.classList.remove('agentgraph', 'ag-compact', 'ag-canvas-compact', 'ag-details-open'); root.style.removeProperty('--ag-sheet-height'); root.style.removeProperty('--ag-header-height'); delete root.dataset.state; delete root.dataset.team; delete root.dataset.agScheme; delete root.dataset.agSchemeSource; delete root.dataset.agMotion; delete root.dataset.agFlows; delete root.dataset.agTrail;
    } };
  }
  window.AgentGraph = { mount };
})();
