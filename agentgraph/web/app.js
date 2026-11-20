(function () {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const LIST_LIMIT = 200;
  const EDGE = 12, SLOT = 104, GROUP_GAP = 34, SEAT_DISTANCE = 68, MEMBER_DISTANCE = 96, MEMBER_REACH = 40;
  // A full loop's agents sit on a circle of their own: neighbours RING_GAP apart, the circle never under RING_MIN.
  const RING_GAP = 124, RING_MIN = 82, RING_MS = 240;
  // Out of the canvas counts against a place, but less than running into a circle or a name (n103).
  const RING_OUT = .25;
  const DETAIL_WIDTH = 340; // the details panel beside a wide graph (style.css: .ag-details-open .ag-network margin)
  const READABLE = 11 / 12; // agent names are 12 px: the automatic view never shows them under 11 px; it pans instead
  let instance = 0;
  // The bottom sheet's height the user dragged to, per positions key, while the page is open (n103 item 7).
  const sheetShares = new Map();
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
    // The three back ends the details panel writes through (WEB_API.md): agents (add, remove, restore), skill text
    // (read, write, create, delete, restore) and groups ("teams" in code and files: add, members, text, remove).
    const action = name => options.actions && typeof options.actions[name] === 'function' ? options.actions[name] : null;
    // Contract n96: a host that can talk to the orchestrator runs a path ({ flow, goal }); without it the view copies
    // the run message ({ op: "prompt" } through actions.flow).
    const runAction = action('run');
    const RUN_GOAL_CHARS = 4000, RUN_GOAL_WARN = 3600;
    const goals = new Map(), runStates = new Map(), commandApplies = new Map(), pathErrors = new Map();
    const rosterAction = action('roster'), skillfileAction = action('skillfile'), teamAction = action('team');
    // Lines said in an agent's details after a change to it (added, a refused delete); the open skill's text; the
    // form a new group is being chosen in; the group line a left click picked (the target of Delete).
    const agentNotes = new Map();
    let skillView = null, teamDraft = null, pickedMember = '', formBusy = false;
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
    // The details sheet's height in a narrow pane (n103 item 7): the user's share of the pane (null: the default),
    // kept for the page and, where storage works, across reloads; clamped to the pane whenever it is used.
    const SHEET_KEY = POSITIONS_KEY + '.sheet', SHEET_MIN = 120, SHEET_GRAPH = 100, SHEET_FIGURES = 28;
    let sheetShare = loadSheetShare(), sheetDrag = null;
    // Presses since the last drag that moved something: a double-click counts only when its two clicks were clean.
    let cleanPresses = Infinity;
    const flowErrors = new Map(), flowHistory = [], routeViews = new Map(), freshRoutes = new Set(), timers = new Set();
    let layoutSignature = '', layoutFrame = null, resizeFrame = null, generation = 0, renderStamp = '', layoutChange = { changed: [], steady: false };
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
    viewport.setAttribute('aria-label', 'Agent network');
    viewport.setAttribute('aria-keyshortcuts', 'F R N + - ArrowUp ArrowDown ArrowLeft ArrowRight Shift+Enter');
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
    // A group's lead joined to each member (WEB_API.md): drawn under the flows, picked by a left click like a flow.
    const teamLayer = svg('g', { class: 'ag-teams' });
    const trailLayer = svg('g', { class: 'ag-trail', 'aria-hidden': 'true' });
    const wire = svg('line', { class: 'ag-wire', 'aria-hidden': 'true' });
    wire.style.display = 'none';
    // The agent being added: a circle under the pointer until it is dropped, then the details name it.
    const draftLayer = svg('g', { class: 'ag-drafts', 'aria-hidden': 'true' });
    stage.append(edgeLayer, teamLayer, loopLayer, routeLayer, trailLayer, wire, nodeLayer, draftLayer); drawing.append(defs, stage);
    const teamLines = new Map(); // "id\nmember" -> { group, hit, line, team, member }
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
    // The + adds an agent: a glyph (no words) that opens a small menu of starting points anchored to it.
    const plus = el('button', 'ag-control ag-add', '+');
    plus.type = 'button'; plus.hidden = true; plus.setAttribute('aria-label', 'Add an agent'); plus.title = 'Add an agent';
    plus.setAttribute('aria-haspopup', 'menu'); plus.setAttribute('aria-expanded', 'false'); plus.setAttribute('aria-keyshortcuts', 'N');
    const addMenu = el('div', 'ag-add-menu');
    addMenu.hidden = true; addMenu.setAttribute('role', 'menu'); addMenu.setAttribute('aria-label', 'Add an agent');
    controls.append(plus, fit, runDetails);
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
    network.append(status, usage, viewport, controls, addMenu, trailStatus);
    const detail = el('aside', 'ag-detail');
    detail.hidden = true; detail.setAttribute('aria-label', 'Details');
    const detailHeader = el('div', 'ag-detail-header');
    const eyebrow = el('div', 'ag-eyebrow');
    const close = el('button', 'ag-close', '×');
    close.type = 'button'; close.setAttribute('aria-label', 'Close');
    detailHeader.append(eyebrow, close);
    const detailScroll = el('div', 'ag-detail-scroll');
    // The sheet's grab handle (narrow panes only): dragging it or its arrow keys resize the sheet, nothing else.
    const sheetHandle = el('div', 'ag-sheet-handle');
    sheetHandle.tabIndex = 0; sheetHandle.setAttribute('role', 'separator'); sheetHandle.setAttribute('aria-orientation', 'horizontal'); sheetHandle.setAttribute('aria-label', 'Details height');
    detail.append(sheetHandle, detailHeader, detailScroll); root.append(notice, network, detail);

    function renderActivity() {
      const working = snapshot ? visibleAgents.filter(agent => agent.busy > 0).length : 0;
      activity.hidden = !working;
      activity.textContent = working ? working + ' working' : '';
      activity.dataset.active = String(working > 0);
      const alerts = snapshot ? attentionAgents().size : 0;
      attention.hidden = !alerts; attention.textContent = alerts ? '! ' + alerts : '';
      // The marker says what it counts; its details list them.
      const said = alerts === 1 ? '1 agent needs attention' : alerts + ' agents need attention';
      attention.setAttribute('aria-label', alerts ? said + ': open the run details' : 'Needs attention'); attention.title = alerts ? said : '';
      usage.hidden = !snapshot;
      if (!snapshot) return;
      usageCost.textContent = cost(snapshot.totals?.cost);
      usageTokens.textContent = amount(snapshot.totals?.tokens_in) + '↑ ' + amount(snapshot.totals?.tokens_out) + '↓';
      const info = wearOf(snapshot.root);
      usageContext.hidden = !info && !snapshot.root?.context;
      if (info) { renderWear(info); usage.title = wearTip(info); }
      else { delete usageContext.dataset.level; usage.removeAttribute('title'); usageContext.textContent = snapshot.root?.context ? 'ctx ' + amount(snapshot.root.context) : ''; }
      fitFigures();
    }
    // The figures keep one row in the pane the details leave (beside or above them), never overlapping: what does not
    // fit goes, least important first: the summary dots, the token figure, the context bar, the working count. The
    // cost and the red "! N" always stay. Measured, because the pane (not the whole view) is what has to hold them.
    const FIGURE_DROPS = ['ag-drop-dots', 'ag-drop-tokens', 'ag-drop-context', 'ag-drop-counts'];
    function figuresFit() {
      const pane = network.getBoundingClientRect();
      if (pane.width <= 0 || usage.hidden || !usage.getClientRects().length) return true;
      const box = usage.getBoundingClientRect();
      const left = [...status.children].filter(item => !item.hidden && item.getClientRects().length).map(item => item.getBoundingClientRect().right);
      return box.left >= pane.left + 4 && (!left.length || Math.max(...left) + 8 <= box.left);
    }
    function fitFigures() {
      if (!alive) return;
      network.classList.remove(...FIGURE_DROPS);
      for (const drop of FIGURE_DROPS) { if (figuresFit()) return; network.classList.add(drop); }
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
    // The cost and context figures and the status line float over the top of the canvas, the controls over its
    // lower corner (canvas coordinates, with a 2 px margin). The graph is placed clear of them wherever panning can.
    function coverRects() {
      const view = viewport.getBoundingClientRect();
      return [usage, status, controls].filter(item => !item.hidden && item.getClientRects().length).map(item => {
        const b = item.getBoundingClientRect();
        return { left: b.left - view.left - 2, right: b.right - view.left + 2, top: b.top - view.top - 2, bottom: b.bottom - view.top + 2 };
      }).filter(b => b.right - b.left > 6 && b.bottom - b.top > 6 && b.bottom > 0 && b.top < view.height);
    }
    // How far down the top band (the figures and the status line) reaches.
    function topInset() { return Math.max(8, ...coverRects().filter(b => b.top < 24).map(b => b.bottom)); }
    // After the camera is placed automatically: when a circle, name or count would sit under one of the covers,
    // the smallest vertical (else horizontal) shift that clears every cover, within the pan limits (and keeping
    // `keep`, the agent just brought into view, inside the canvas).
    function clearOfCovers(keep) {
      const covers = coverRects(), width = viewport.clientWidth, height = viewport.clientHeight, s = camera.scale;
      if (!covers.length || !width || !height) return;
      const boxes = visibleAgents.map(a => positions.get(a.name)).filter(Boolean).map(p => { const e = extentOf(p); return { left: (p.x + e.left) * s + camera.x, right: (p.x + e.right) * s + camera.x, top: (p.y + e.top) * s + camera.y, bottom: (p.y + e.bottom) * s + camera.y }; });
      const hits = (dx, dy) => boxes.some(b => {
        const l = Math.max(b.left + dx, 0), r = Math.min(b.right + dx, width), t = Math.max(b.top + dy, 0), d = Math.min(b.bottom + dy, height);
        return r - l > .5 && d - t > .5 && covers.some(c => Math.min(r, c.right) - Math.max(l, c.left) > .5 && Math.min(d, c.bottom) - Math.max(t, c.top) > .5);
      });
      if (!hits(0, 0)) return;
      const bounds = graphBounds(), limits = axis => {
        if (arrangement !== 'compact' || !bounds) return axis === 'y' ? [-height * .3, height * .3] : [-width * .3, width * .3];
        if (axis === 'y') return [height - 8 - bounds.bottom * s - camera.y, topInset() - bounds.top * s - camera.y].sort((a, b) => a - b);
        return [width - 2 - bounds.right * s - camera.x, 2 - bounds.left * s - camera.x].sort((a, b) => a - b);
      };
      const shifts = axis => {
        const [low, high] = limits(axis), out = new Set();
        boxes.forEach(b => covers.forEach(c => { if (axis === 'y') { out.add(c.bottom - b.top + 1); out.add(c.top - b.bottom - 1); } else { out.add(c.right - b.left + 1); out.add(c.left - b.right - 1); } }));
        return [...out].filter(d => d >= low - .01 && d <= high + .01).sort((a, b) => Math.abs(a) - Math.abs(b));
      };
      const kept = keep && (() => { const e = extentOf(keep); return { left: (keep.x + e.left) * s + camera.x, right: (keep.x + e.right) * s + camera.x, top: (keep.y + e.top) * s + camera.y, bottom: (keep.y + e.bottom) * s + camera.y }; })();
      const inside = (dx, dy) => !kept || (kept.left + dx >= 0 && kept.right + dx <= width && kept.top + dy >= 0 && kept.bottom + dy <= height);
      const dy = shifts('y').find(d => inside(0, d) && !hits(0, d));
      if (dy !== undefined) { camera.y += dy; return; }
      const dx = shifts('x').find(d => inside(d, 0) && !hits(d, 0));
      if (dx !== undefined) camera.x += dx;
    }
    // The sidebar keeps a readable scale: what does not fit is reached by
    // panning, and the picture stays centred on the orchestrator.
    // `steady` (n103): a view already placed stays where it is while the whole graph is in it (no re-centring).
    function clampPan(steady) {
      if (arrangement !== 'compact') return;
      const bounds = graphBounds();
      if (!bounds) return;
      const width = viewport.clientWidth, height = viewport.clientHeight;
      // Panned to the top, the topmost agent sits below the figures.
      const top = topInset() - bounds.top * camera.scale, bottom = height - 8 - bounds.bottom * camera.scale;
      camera.y = bottom >= top ? (steady ? clamp(camera.y, top, bottom) : (top + bottom) / 2) : clamp(camera.y, bottom, top);
      const left = 2 - bounds.left * camera.scale, right = width - 2 - bounds.right * camera.scale;
      camera.x = right >= left ? clamp(steady ? camera.x : width / 2, left, right) : clamp(camera.x, right, left);
    }
    // Zooming out (wheel, pinch, keys) stops before names go under 11 px (agent names are 12 px in the drawing), the
    // floor Fit keeps too: the user's rule is that names are never drawn smaller.
    const NAME_FLOOR = READABLE;
    function zoomAt(next, x, y) {
      const rect = viewport.getBoundingClientRect();
      x = x === undefined ? rect.width / 2 : x; y = y === undefined ? rect.height / 2 : y;
      next = clamp(next, NAME_FLOOR, 3);
      camera.x = x - (x - camera.x) * next / camera.scale;
      camera.y = y - (y - camera.y) * next / camera.scale;
      camera.scale = next; camera.touched = true; camera.own = true; transform();
    }
    // Bring the selected agent, with its name and its count, fully into the space the details leave uncovered
    // (the graph may be larger than that space: it is never shrunk below a readable size).
    function reveal() {
      const point = selected?.type === 'agent' || selected?.type === 'work-loop' ? positions.get(selected.key) : null;
      const width = viewport.clientWidth, height = viewport.clientHeight;
      if (!point || !width || !height) return;
      // An open agent comes into view with the agents its paths join it to, when they fit together (n103).
      if (selected.type === 'agent') {
        const names = [selected.key, ...new Set(routes().flatMap(flow => (flow.path || []).flatMap((name, i, path) => name !== selected.key ? [] : [path[i - 1], path[i + 1]]).filter(name => name && name !== selected.key && positions.has(name))))];
        if (names.length > 1 && revealNames(names, true)) { clampPan(true); clearOfCovers(point); transform(); return; }
      }
      const e = extentOf(point), s = camera.scale;
      const top = (point.y + e.top) * s + camera.y, bottom = (point.y + e.bottom) * s + camera.y;
      const left = (point.x + e.left) * s + camera.x, right = (point.x + e.right) * s + camera.x;
      const inset = Math.max(EDGE, topInset());
      const outside = top < inset || bottom > height - EDGE || left < EDGE || right > width - EDGE;
      if (!outside) return;
      // Only as far as it takes, so a view the user placed moves least (n103).
      revealNames([selected.key]);
      clampPan(); clearOfCovers(point); transform();
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
        // Names stay readable, the user's Fit included: a narrower margin first, then the floor (and the graph pans).
        if (camera.scale < READABLE) camera.scale = Math.max(READABLE, fitWith(EDGE));
        camera.x = rect.width / 2 - (bounds.left + bounds.right) / 2 * camera.scale;
        camera.y = rect.height / 2 - (bounds.top + bounds.bottom) / 2 * camera.scale;
      }
      clearOfCovers();
      camera.width = rect.width; camera.height = rect.height;
      camera.initialized = true;
      camera.touched = !!userAction;
      // A fit (automatic or the user's) hands the view back: later changes of space refit it again.
      camera.own = false;
      transform();
    }

    // Where an agent is going (the end of a glide in progress), on the stage.
    function finalOf(name) {
      const item = glideState?.items.find(entry => entry.name === name), p = positions.get(name);
      return p && (item ? { ...p, x: item.tx, y: item.ty } : p);
    }
    // The smallest pan that brings the given agents (circle, name and count) into the space left uncovered; when they
    // cannot all fit, centred on them (or, `whole`, left alone: returns false). Returns whether they are now in view.
    function revealNames(names, whole) {
      const width = viewport.clientWidth, height = viewport.clientHeight, s = camera.scale;
      const points = names.map(finalOf).filter(Boolean);
      if (!points.length || !width || !height) return false;
      const spans = points.map(p => { const e = extentOf(p); return { left: (p.x + e.left) * s + camera.x, right: (p.x + e.right) * s + camera.x, top: (p.y + e.top) * s + camera.y, bottom: (p.y + e.bottom) * s + camera.y }; });
      const box = { left: Math.min(...spans.map(b => b.left)), right: Math.max(...spans.map(b => b.right)), top: Math.min(...spans.map(b => b.top)), bottom: Math.max(...spans.map(b => b.bottom)) };
      // The margins the fit itself keeps (a narrow canvas is filled closer to its edges).
      let side = arrangement === 'compact' ? 2 : EDGE, foot = arrangement === 'compact' ? 8 : EDGE;
      let inset = Math.max(arrangement === 'compact' ? 8 : EDGE, topInset()), x0 = camera.x, y0 = camera.y;
      const fits = () => box.right - box.left <= width - 2 * side && box.bottom - box.top <= height - foot - inset;
      // A set that must come into view together may use the margins too (the extents already pad each name).
      if (whole && !fits()) { side = 0; foot = 0; inset = Math.max(0, ...coverRects().filter(b => b.top < 24).map(b => b.bottom)); }
      if (whole && !fits()) return false;
      const shift = (low, high, from, to) => to - from > high - low ? (low + high) / 2 - (from + to) / 2 : from < low ? low - from : to > high ? high - to : 0;
      camera.x += shift(side, width - side, box.left, box.right);
      camera.y += shift(inset, height - foot, box.top, box.bottom);
      return whole || Math.abs(camera.x - x0) + Math.abs(camera.y - y0) > .01;
    }
    // After the layout changed under a camera already placed (n8 major 6, n103): a view the user panned or zoomed stays
    // as it is. An automatic view stays too when only a loop formed or went or an agent was added (nothing else moved,
    // so nothing else moves on screen); it only pans what moved (and the open agent) into view. Otherwise it is fitted again.
    function settleCamera() {
      if (camera.own) { clampPan(true); transform(); return; }
      if (!layoutChange.steady) { fitView(false); return; }
      revealNames(layoutChange.changed);
      clampPan(true); transform();
      reveal();
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
    // Groups from the structure folder (structure.teams): a lead and the agents that report to it.
    function structureTeams() {
      return (snapshot?.structure?.teams || []).filter(team => team && typeof team.id === 'string' && typeof team.lead === 'string' && Array.isArray(team.members))
        .map(team => ({ id: team.id, lead: team.lead, members: team.members.filter(name => typeof name === 'string'), text: typeof team.text === 'string' ? team.text : '', rev: typeof team.rev === 'string' ? team.rev : '' }));
    }
    function teamById(id) { return structureTeams().find(team => team.id === id) || null; }
    function teamLedBy(name) { return structureTeams().find(team => team.lead === name) || null; }
    // An agent that can lead a group or sit in one: not the hub, not the council or one of its seats.
    function ordinaryAgent(name) {
      const agent = shownAgents.get(name);
      return !!agent && name !== hubName && agent.group !== 'hub' && !isSeatAgent(agent) && !visibleAgents.some(other => other.parent === name);
    }
    // Members drawn round their lead (member -> lead): ordinary agents that lead no group, each in the first group
    // that has it. Any other member keeps its place and is still joined to its lead.
    function satellites(byAgent) {
      const found = new Map(), leads = new Set(structureTeams().map(team => team.lead));
      structureTeams().forEach(team => {
        if (!byAgent.has(team.lead) || !ordinaryAgent(team.lead)) return;
        team.members.forEach(name => { if (name !== team.lead && byAgent.has(name) && ordinaryAgent(name) && !leads.has(name) && !found.has(name)) found.set(name, team.lead); });
      });
      return found;
    }
    // The circles of full loops (see loopStart): for each drawn flow that closes, in the order the flows are listed,
    // the agents on its ring and those of them that move onto it. The hub, the council and its seats, a group's
    // members (drawn round their lead) and an agent already on an earlier loop's circle stay where they are and the
    // ring is drawn through them. Keyed by its agents, so undoing a removal finds the same circle.
    function loopRings(byAgent, orbit) {
      const claimed = new Set(), rings = [];
      routes().forEach(flow => {
        const start = loopStart(flow.path);
        if (start < 0) return;
        const names = Array.from(new Set(flow.path.slice(start, -1)));
        if (names.length < 2 || names.some(name => !byAgent.has(name))) return;
        const movable = names.filter(name => ordinaryAgent(name) && !orbit.has(name) && !claimed.has(name));
        if (!movable.length) return;
        movable.forEach(name => claimed.add(name));
        // A, B, C, B: A leads into the circle (it stays near A).
        const into = start > 0 && byAgent.has(flow.path[start - 1]) && !names.includes(flow.path[start - 1]) ? flow.path[start - 1] : null;
        rings.push({ key: 'ring:' + names.slice().sort().join('|'), names, movable, into });
      });
      return rings;
    }
    // Where a full loop's circle goes (n103): in free space. Its line keeps RING_SPACE clear of every circle and name not
    // on it (none of them inside it), of the orchestrator's name always, and RING_APART from every other loop's line (two
    // loops meet only at an agent on both, and touch there rather than cross). Its moving agents, circle and name, land
    // clear of everything else. Of the places that do, the one that moves its agents least and grows the picture least.
    // Nothing else moves, so removing the loop puts every agent back.
    const RING_SPACE = 12, RING_APART = 24;
    // An agent's name box round its centre: as last drawn, else estimated from its length.
    function labelBox(name, r, labelUp, lead) {
      const p = positions.get(name);
      if (p && Number.isFinite(p.labelLeft) && Math.abs((p.r || r) - r) < .01 && !!p.labelUp === !!labelUp) return { left: p.labelLeft, right: p.labelRight, top: p.labelTop, bottom: p.labelBottom };
      const agent = shownAgents.get(name), shown = agent ? displayName(agent) : name, size = name === hubName ? 18 : lead ? 15 : 12;
      const half = shown.length * size * .31 + 2, base = labelUp ? -r - 8 : r + 20;
      return { left: -half, right: half, top: base - size * .8, bottom: base + size * .25 };
    }
    // An agent as the loops see it: its circle (the orchestrator's with its outer ring) and its name box, on the stage.
    function shapeAt(name, x, y, r, labelUp, lead) {
      const b = labelBox(name, r, labelUp, lead);
      return { name, x, y, r: name === hubName ? r + 27 : r, box: { left: x + b.left, right: x + b.right, top: y + b.top, bottom: y + b.bottom } };
    }
    const boxNear = (b, x, y) => Math.hypot(Math.max(b.left - x, 0, x - b.right), Math.max(b.top - y, 0, y - b.bottom));
    const boxGap = (a, b) => Math.max(a.left - b.right, b.left - a.right, a.top - b.bottom, b.top - a.bottom);
    // How far shape `m` comes into shape `o` (0: clear), and how far a shape comes into the inside of a circle (c, R).
    const clashOf = (m, o) => Math.max(0, m.r + o.r + 14 - Math.hypot(m.x - o.x, m.y - o.y)) + Math.max(0, o.r + 4 - boxNear(m.box, o.x, o.y)) + Math.max(0, m.r + 4 - boxNear(o.box, m.x, m.y)) + Math.max(0, 4 - boxGap(m.box, o.box));
    // How far a circle (c, R) actually runs through or round a shape, and how far two shapes actually overlap.
    const realOf = (s, x, y, R) => Math.max(0, R + s.r - Math.hypot(s.x - x, s.y - y)) + Math.max(0, R - boxNear(s.box, x, y));
    const hitOf = (m, o) => Math.max(0, m.r + o.r - Math.hypot(m.x - o.x, m.y - o.y)) + Math.max(0, o.r - boxNear(m.box, o.x, o.y)) + Math.max(0, m.r - boxNear(o.box, m.x, m.y)) + Math.max(0, -boxGap(m.box, o.box));
    const insideOf = (s, x, y, R) => Math.max(0, R + s.r + RING_SPACE - Math.hypot(s.x - x, s.y - y)) + Math.max(0, R + RING_SPACE - boxNear(s.box, x, y));
    function placeRings(rings, placed, targets, placedByUser, canvas) {
      const byName = new Map(targets.map(target => [target.name, target])), movers = new Set(rings.flatMap(ring => ring.movable).filter(name => !byName.get(name)?.pinned)), bands = [];
      const at = target => { const owner = target.parent && placed.get(target.parent); return owner ? { x: owner.x + target.dx, y: owner.y + target.dy } : { x: target.x, y: target.y }; };
      const lead = target => !target.parent && target.r === 29;
      const shapeOf = (target, p = at(target)) => shapeAt(target.name, p.x, p.y, target.r, !!target.parent && target.dy < -1, lead(target));
      const extent = list => ({ left: Math.min(...list.map(s => Math.min(s.x - s.r, s.box.left))), right: Math.max(...list.map(s => Math.max(s.x + s.r, s.box.right))), top: Math.min(...list.map(s => Math.min(s.y - s.r, s.box.top))), bottom: Math.max(...list.map(s => Math.max(s.y + s.r, s.box.bottom))) });
      // The picture as it is without loops; what stays put; and the room the canvas has round the picture's middle (the
      // figures take the top; a wide canvas as the details leave it), so a loop formed there stays in view.
      const area = extent(targets.map(target => shapeOf(target))), shapes = targets.filter(target => !movers.has(target.name)).map(target => shapeOf(target));
      // A wide canvas shows the picture at up to 1.3 times, with the details open and closed: the room is what both show.
      const across = area.right - area.left || 1, high = area.bottom - area.top || 1;
      let half = Infinity, halfY = Infinity;
      // A narrow one at up to 1 (its fit keeps 2 px each side and 8 px top and bottom), and its panel may be taller than
      // the canvas laid out for (the sheet closed), so the view can be as large as the width allows.
      (canvas.wide ? [canvas.width, canvas.width + DETAIL_WIDTH] : [canvas.width]).forEach(w => {
        const side = canvas.wide ? EDGE : 2, top = canvas.wide ? EDGE + 44 : 8;
        const s = clamp(Math.min((w - 2 * side) / across, canvas.wide ? (canvas.height - 2 * top) / high : Infinity), READABLE, canvas.wide ? 1.3 : 1);
        // Less the room the view keeps round each circle and name when it brings one into view (extentOf).
        half = Math.min(half, (w / 2 - side) / s - 14); halfY = Math.min(halfY, (canvas.height / 2 - top) / s - 22);
      });
      half = Math.max(half, across / 2); halfY = Math.max(halfY, high / 2);
      const centre = (area.left + area.right) / 2, middle = (area.top + area.bottom) / 2;
      // The best place for one loop's circle, its `movable` agents moving onto it (null: it cannot be drawn).
      // `ahead`: later loops through one of this loop's moving agents (and otherwise free): each needs room on the far side
      // of that agent, touching this circle there, so this circle leaves it.
      const search = (ring, ahead = []) => {
        const count = ring.names.length, radius = Math.max(RING_MIN, RING_GAP / (2 * Math.sin(Math.PI / count))), step = Math.PI * 2 / count;
        const spots = ring.names.map(name => byName.has(name) ? at(byName.get(name)) : null);
        if (spots.some(spot => !spot)) return;
        const fixed = ring.names.map((name, i) => i).filter(i => !ring.movable.includes(ring.names[i]));
        const others = shapes.filter(s => !ring.names.includes(s.name)), hub = shapes.find(s => s.name === hubName && ring.names.includes(s.name));
        let best = null;
        // The ring's line at (x, y): how far it comes into what stays and into the other loops.
        const lineBad = (x, y) => {
          let bad = hub ? Math.max(0, radius + RING_SPACE - boxNear(hub.box, x, y)) : 0;
          for (const s of others) { bad += insideOf(s, x, y, radius); if (best && bad > best.bad) return bad; }
          bands.forEach(band => {
            const d = Math.hypot(x - band.x, y - band.y) || 1e-6, shared = ring.names.find(name => band.names.includes(name));
            if (!shared) { bad += Math.max(0, radius + band.r + RING_APART - d); return; }
            // Through a common agent: they touch there (the second meeting point inside its circle), each outside the other.
            const s = placed.get(shared) || at(byName.get(shared)), ux = (band.x - x) / d, uy = (band.y - y) / d, along = (s.x - x) * ux + (s.y - y) * uy;
            const sx = 2 * (x + ux * along) - s.x, sy = 2 * (y + uy * along) - s.y;
            bad += Math.max(0, Math.hypot(sx - s.x, sy - s.y) - (byName.get(shared).r + 10)) + Math.max(0, .9 * (radius + band.r) - d);
          });
          return bad;
        };
        const consider = (x, y, start, dir, line) => {
          let bad = line, moved = 0;
          const made = [], list = [];
          for (let i = 0; i < count; i++) {
            const name = ring.names[i];
            if (!ring.movable.includes(name)) continue;
            const a = start + dir * i * step, px = x + radius * Math.cos(a), py = y + radius * Math.sin(a), m = shapeOf(byName.get(name), { x: px, y: py });
            for (const s of others) bad += clashOf(m, s);
            bands.forEach(band => { if (!band.names.includes(name)) bad += insideOf(m, band.x, band.y, band.r); });
            if (best && bad > best.bad + 1e-6) return;
            moved += Math.hypot(px - spots[i].x, py - spots[i].y); made.push([name, px, py]); list.push(m);
          }
          for (const next of ahead) {
            const spot = made.find(([name]) => name === next.name);
            if (!spot) continue;
            const nx = spot[1] + (spot[1] - x) / radius * next.radius, ny = spot[2] + (spot[2] - y) / radius * next.radius, skip = new Set([...ring.names, ...next.names]);
            for (const s of shapes) if (!skip.has(s.name)) bad += insideOf(s, nx, ny, next.radius);
            for (const s of list) if (!next.names.includes(s.name)) bad += insideOf(s, nx, ny, next.radius);
            bands.forEach(band => { if (!band.names.some(name => next.names.includes(name))) bad += Math.max(0, next.radius + band.r + RING_APART - Math.hypot(nx - band.x, ny - band.y)); });
            bad += RING_OUT * (Math.max(0, centre - half - (nx - next.radius)) + Math.max(0, nx + next.radius - centre - half) + Math.max(0, middle - halfY - (ny - next.radius)) + Math.max(0, ny + next.radius - middle - halfY));
            if (best && bad > best.bad + 1e-6) return;
          }
          // A, B, C, B: the circle stays near A, so the step into it is short and both come into view together.
          const from = ring.into && (placed.get(ring.into) || (byName.has(ring.into) && at(byName.get(ring.into)))), entry = made.find(([name]) => name === ring.names[0]) || [null, spots[0].x, spots[0].y];
          if (from) moved += 3 * Math.hypot(entry[1] - from.x, entry[2] - from.y);
          // A and the circle must fit in one view at the smallest readable size (the open agent and its
          // neighbours are shown together); that counts for more than staying inside the canvas.
          if (from && byName.has(ring.into)) {
            const both = extent([shapeOf(byName.get(ring.into), from), ...list]);
            bad += 3 * (Math.max(0, both.right - both.left + 28 - (canvas.width - 4) / READABLE) + Math.max(0, both.bottom - both.top + 44 - (canvas.height - 16) / READABLE));
            if (best && bad > best.bad + 1e-6) return;
          }
          list.push({ x, y, r: radius, box: { left: x, right: x, top: y, bottom: y } });
          const e = extent(list), grown = Math.max(0, area.left - e.left) + Math.max(0, e.right - area.right) + Math.max(0, area.top - e.top) + Math.max(0, e.bottom - area.bottom);
          // The canvas has no room to spare: a circle out of it would refit the view, which moves everything on
          // screen, so it counts as running into something.
          const over = Math.max(0, centre - half - e.left) + Math.max(0, e.right - centre - half) + Math.max(0, middle - halfY - e.top) + Math.max(0, e.bottom - middle - halfY);
          if (over > 0) { bad += RING_OUT * over; if (best && bad > best.bad + 1e-6) return; }
          const cost = moved + 3 * grown + 1000 * (Math.max(0, centre - half - e.left) + Math.max(0, e.right - centre - half) + Math.max(0, middle - halfY - e.top) + Math.max(0, e.bottom - middle - halfY));
          if (!best || bad < best.bad - 1e-6 || cost < best.cost) best = { bad, cost, x, y, start, dir, made, over };
        };
        if (fixed.length === 1) {
          // One agent stays (the orchestrator, a seat, one on an earlier loop): the circle passes through it.
          const i = fixed[0], f = spots[i];
          for (let k = 0; k < 120; k++) for (const dir of [1, -1]) {
            const angle = k * Math.PI / 60, x = f.x + radius * Math.cos(angle), y = f.y + radius * Math.sin(angle);
            consider(x, y, Math.atan2(f.y - y, f.x - x) - dir * i * step, dir, lineBad(x, y));
          }
        } else if (!fixed.length) {
          const turns = Array.from({ length: 12 }, (_, k) => k * step / 12);
          const tryAt = (x, y) => { const line = lineBad(x, y); if (best && line > best.bad + 1e-6) return; turns.forEach(turn => { consider(x, y, turn, 1, line); consider(x, y, turn, -1, line); }); };
          const reach = radius + 140;
          for (let x = area.left - reach; x <= area.right + reach; x += 24) for (let y = area.top - reach; y <= area.bottom + reach; y += 24) tryAt(x, y);
          if (best) { const cx = best.x, cy = best.y; for (let dx = -24; dx <= 24; dx += 6) for (let dy = -24; dy <= 24; dy += 6) tryAt(cx + dx, cy + dy); }
        } else {
          // Two or more stay where they are (the council's seats): the circle round their middle, turned so the others move least.
          const x = spots.reduce((sum, spot) => sum + spot.x, 0) / count, y = spots.reduce((sum, spot) => sum + spot.y, 0) / count;
          const line = lineBad(x, y);
          for (let k = 0; k < 72; k++) consider(x, y, k * Math.PI / 36, 1, line);
        }
        return best;
      };
      // One pass over the loops in order. `looking`: the loops that leave room for a later loop through one of their agents.
      const pass = (looking, order = rings.map((ring, index) => index)) => {
        const results = [];
        order.forEach(index => {
          const full = rings[index];
          const ahead = !looking.has(index) ? [] : looking.get(index).map(k => rings[k]).flatMap(next => {
            const common = next.names.filter(name => full.movable.includes(name));
            if (common.length !== 1 || next.names.some(name => name !== common[0] && (!next.movable.includes(name) || byName.get(name)?.pinned))) return [];
            return [{ name: common[0], names: next.names, radius: Math.max(RING_MIN, RING_GAP / (2 * Math.sin(Math.PI / next.names.length))) }];
          });
          // An agent the user put somewhere stays there and the circle goes through it, unless the circle would then run
          // into something; then it moves onto a circle in free space like the rest.
          let ring = { ...full, movable: full.movable.filter(name => !byName.get(name)?.pinned) }, best = search(ring, ahead);
          if (ring.movable.length < full.movable.length && (!best || best.bad > .5)) { const other = search(full, ahead); if (other && (!best || other.bad < best.bad)) { ring = full; best = other; } }
          const count = ring.names.length, radius = Math.max(RING_MIN, RING_GAP / (2 * Math.sin(Math.PI / count)));
          results[index] = (best ? { bad: best.bad, x: best.x, y: best.y, radius, names: ring.names, made: best.made.map(([name]) => name), over: best.over } : null);
          if (!best) return;
          const shift = placedByUser[ring.key], sx = shift && Number.isFinite(shift.dx) ? shift.dx : 0, sy = shift && Number.isFinite(shift.dy) ? shift.dy : 0;
          best.made.forEach(([name, x, y]) => {
            const body = placed.get(name);
            if (!body) return;
            Object.assign(body, { x: x + sx, y: y + sy, still: true, ring: ring.key });
            const stayed = shapes.findIndex(shape => shape.name === name);
            if (stayed >= 0) shapes.splice(stayed, 1);
            shapes.push(shapeOf(byName.get(name), body));
          });
          bands.push({ x: best.x + sx, y: best.y + sy, r: radius, names: ring.names });
        });
        return results;
      };
      // Earlier loops stay where they are when a loop is added. Only when a later loop through an agent of an earlier one
      // then has no clear place does that earlier loop go where it leaves room for it (still a function of the loops
      // drawn, so removing one puts every agent back).
      const bodies = [...new Set(rings.flatMap(ring => ring.movable))].map(name => placed.get(name)).filter(Boolean), saved = bodies.map(body => ({ body, x: body.x, y: body.y, still: body.still, ring: body.ring })), shapes0 = shapes.slice();
      // No clear place: its line or a moved agent actually runs into an agent or a name (not only into the margins).
      const clashes = result => !result || shapes.some(s => !result.names.includes(s.name) && (realOf(s, result.x, result.y, result.radius) > 1 || result.made.some(name => { const m = shapes.find(t => t.name === name); return m && hitOf(m, s) > 1; })));
      // Out of the canvas counts too when comparing passes, but only a real clash makes an earlier loop make room.
      const blocked = result => clashes(result) || result.over > 1;
      // The earlier loop is then placed after every other loop (so it keeps clear of them too) and just before the loops
      // it makes room for; only when no other loop goes through its agents.
      const first = pass(new Map()), looking = new Map(), worse = list => [clashes, blocked].map(test => rings.filter((ring, index) => test(list[index])).length), before = worse(first);
      rings.forEach((ring, k) => {
        if (!clashes(first[k])) return;
        const fixedOn = ring.names.filter(name => !ring.movable.includes(name));
        rings.forEach((earlier, j) => {
          if (j >= k || fixedOn.length !== 1 || !earlier.movable.includes(fixedOn[0]) || blocked(first[j])) return;
          if (rings.some((other, m) => m !== j && m !== k && other.names.some(name => earlier.names.includes(name)))) return;
          looking.set(j, [...(looking.get(j) || []), k]);
        });
      });
      if (!looking.size) return;
      const last = new Set([...looking.keys(), ...[...looking.values()].flat()]), order = rings.map((ring, index) => index).filter(index => !last.has(index));
      [...looking.keys()].sort((a, b) => a - b).forEach(j => { order.push(j); looking.get(j).forEach(k => { if (!order.includes(k)) order.push(k); }); });
      const keep = { bodies: bodies.map(body => ({ body, x: body.x, y: body.y, still: body.still, ring: body.ring })), shapes: shapes.slice(), bands: bands.slice() };
      saved.forEach(({ body, x, y, still, ring }) => Object.assign(body, { x, y, still, ring }));
      shapes.splice(0, shapes.length, ...shapes0); bands.length = 0;
      const second = pass(looking, order);
      // The second pass stays when fewer loops run into something, or as many and fewer leave the canvas.
      const after = worse(second);
      if (after[0] < before[0] || (after[0] === before[0] && after[1] < before[1])) return;
      keep.bodies.forEach(({ body, x, y, still, ring }) => Object.assign(body, { x, y, still, ring }));
      shapes.splice(0, shapes.length, ...keep.shapes); bands.splice(0, bands.length, ...keep.bands);
    }
    function positionStore() { try { return typeof localStorage === 'undefined' ? null : localStorage; } catch (_) { return null; } }
    function loadMoves() {
      const kept = {};
      try {
        const raw = positionStore()?.getItem(POSITIONS_KEY), saved = raw ? JSON.parse(raw) : null;
        Object.entries(saved && typeof saved.layouts === 'object' && saved.layouts ? saved.layouts : {}).forEach(([layout, agents]) => {
          if (!agents || typeof agents !== 'object') return;
          Object.entries(agents).forEach(([name, value]) => {
            const body = value && Number.isFinite(value.x) && Number.isFinite(value.y), seat = value && Number.isFinite(value.dx) && Number.isFinite(value.dy);
            // A group's member keeps both: its place round its lead, and the place it had before it joined.
            if (body || seat) (kept[layout] ||= {})[name] = { ...(body ? { x: value.x, y: value.y } : {}), ...(seat ? { dx: value.dx, dy: value.dy } : {}) };
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
      // A glide cut short ends where it was going.
      if (glideState) { glideState.items.forEach(item => { const point = positions.get(item.name); if (point) { point.x = item.tx; point.y = item.ty; positionNode(item.name); } }); glideState = null; }
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
      if (root.classList.contains('ag-compact') && !detail.hidden) {
        const height = sheetHeight(), { min, max } = sheetStops(), pane = root.clientHeight || 1;
        root.style.setProperty('--ag-sheet-height', height + 'px');
        sheetHandle.setAttribute('aria-valuemin', String(Math.round(min / pane * 100))); sheetHandle.setAttribute('aria-valuemax', String(Math.round(max / pane * 100)));
        sheetHandle.setAttribute('aria-valuenow', String(Math.round(height / pane * 100)));
      } else root.style.removeProperty('--ag-sheet-height');
    }
    // From a small minimum up to leaving the figures row and a strip of graph (enough to see the picked agent).
    function sheetStops() {
      const max = Math.max(0, root.clientHeight - (notice.hidden ? 0 : notice.offsetHeight) - SHEET_FIGURES - SHEET_GRAPH);
      return { min: Math.min(SHEET_MIN, max), max };
    }
    function sheetHeight() {
      const pane = root.clientHeight;
      if (sheetShare === null) return Math.max(0, Math.min(pane * .4, pane - 112));
      const { min, max } = sheetStops();
      return clamp(sheetShare * pane, min, max);
    }
    function loadSheetShare() {
      if (sheetShares.has(SHEET_KEY)) return sheetShares.get(SHEET_KEY);
      try { const share = Number(JSON.parse(positionStore()?.getItem(SHEET_KEY) || 'null')); return share > 0 && share < 1 ? share : null; } catch (_) { return null; }
    }
    // The graph above refits to what the sheet leaves (the resize observer: fitted with the open agent revealed, or
    // the user's own pan and zoom kept); the agents are laid out for the default sheet, so none of them moves.
    function setSheet(height, keep) {
      const pane = root.clientHeight;
      if (pane <= 0) return;
      const { min, max } = sheetStops();
      sheetShare = clamp(height, min, max) / pane;
      updateDetailSpace(); scheduleResize();
      if (!keep) return;
      sheetShares.set(SHEET_KEY, sheetShare);
      try { positionStore()?.setItem(SHEET_KEY, JSON.stringify(sheetShare)); } catch (_) { /* no storage: kept for the page */ }
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
    // A group's members (full-size agents with names) sit further out, on the same kind of circle.
    function seatOffsets(count, members = false) {
      const distance = members ? (count > 1 ? Math.max(MEMBER_DISTANCE, 46 / Math.sin(Math.PI / count)) : MEMBER_DISTANCE)
        : count > 1 ? Math.max(SEAT_DISTANCE, 31 / Math.sin(Math.PI / count)) : SEAT_DISTANCE;
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
      // A narrow canvas is laid out for what the bottom sheet leaves of it (n103), open or not: every circle and name fits
      // above the sheet at a readable size, and opening or closing it never re-lays out the agents.
      const sheetOpen = compact && root.classList.contains('ag-details-open'), sheetHigh = Math.max(0, Math.min(root.clientHeight * .4, root.clientHeight - 112));
      const full = viewport.clientHeight + (sheetOpen ? (parseFloat(getComputedStyle(root).getPropertyValue('--ag-sheet-height')) || 0) + 28 : 0);
      const canvasHeight = Math.max(1, Math.round(compact ? full - sheetHigh - 28 : viewport.clientHeight));
      // Wide: the oval is shaped for the canvas the details panel (DETAIL_WIDTH) leaves, open or not, so opening it
      // never re-lays out the agents and they still fit at a readable size beside it.
      // A canvas the details leave taller than wide (n103: 760 px) gets a taller oval, chosen below once its size is known.
      const open = (rect.width - DETAIL_WIDTH) / rect.height, tall = open < 1;
      const aspect = Math.round(clamp(open, 1, 1.3) * 20) / 20;
      const byAgent = new Map(agents.map(a => [a.name, a])), orbit = satellites(byAgent), rings = loopRings(byAgent, orbit);
      const base = JSON.stringify([arrangement, compact ? [Math.round(rect.width), canvasHeight] : tall ? ['tall', Math.round(rect.width), Math.round(rect.height)] : aspect, hubName, snapshot?.structure?.source === 'folder' ? (snapshot.groups || []).map(group => group.id) : null, agents.map(a => [a.name, a.group, a.parent]).sort(), [...orbit].sort()]);
      const signature = JSON.stringify([base, rings.map(ring => [ring.key, ring.names, ring.movable])]);
      if (signature === layoutSignature) return;
      // Only the loops changed: the agents that move glide to their places (once, briefly).
      const glide = !!layoutSignature && JSON.parse(layoutSignature)[0] === base && !reducedMotion();
      layoutSignature = signature;
      stopLayout();
      const isSeat = a => { const lead = a.parent && a.parent !== a.name && a.parent !== hubName && byAgent.get(a.parent); return !!lead && !(lead.parent && byAgent.has(lead.parent)); };
      const seatsOf = new Map();
      agents.filter(isSeat).sort((a, b) => a.name.localeCompare(b.name)).forEach(a => { if (!seatsOf.has(a.parent)) seatsOf.set(a.parent, []); seatsOf.get(a.parent).push(a); });
      // A group's members sit round their lead as seats sit round the council (and leave the ring).
      agents.filter(a => orbit.has(a.name)).sort((a, b) => a.name.localeCompare(b.name)).forEach(a => { const lead = orbit.get(a.name); if (!seatsOf.has(lead)) seatsOf.set(lead, []); seatsOf.get(lead).push(a); });
      // An agent the user added and dropped somewhere stays there and takes no place on the ring, so adding it moves no
      // other agent (n103).
      const dropped = a => a.added === true && !isSeat(a) && !orbit.has(a.name) && a.name !== hubName && Number.isFinite(movedHere()[a.name]?.x) && !seatsOf.has(a.name);
      const ring = agents.filter(a => a.name !== hubName && !isSeat(a) && !orbit.has(a.name) && !dropped(a));
      const keys = Array.from(new Set(ring.map(a => a.group)));
      // With a structure folder the ring follows the folder order, first group at the top; otherwise the built-in order.
      const listed = snapshot?.structure?.source === 'folder' ? (snapshot.groups || []).map(group => group.id).filter(id => keys.includes(id)) : null;
      const order = listed ? listed.slice(1) : ['decide', 'build', 'verify', 'understand'];
      const lead = listed ? listed.slice(0, 1) : keys.filter(key => !order.includes(key)).sort((a, b) => Number(b === 'council') - Number(a === 'council') || String(a).localeCompare(String(b)));
      const slot = compact ? 100 : SLOT;
      const groups = [...lead, ...order.filter(key => keys.includes(key)), ...(listed ? keys.filter(key => !listed.includes(key)).sort() : [])].map(key => {
        const units = ring.filter(a => a.group === key).sort((a, b) => a.name.localeCompare(b.name)).map(agent => {
          const seats = seatsOf.get(agent.name) || [], members = seats.some(seat => orbit.has(seat.name)), offsets = seatOffsets(seats.length, members);
          const reach = seats.length ? offsets[0].distance + (members ? MEMBER_REACH : 19) : 23;
          return { agent, seats, members, offsets, reach, width: seats.length ? Math.max(slot, 2 * reach + 34) : slot };
        });
        return { key, units, width: units.reduce((sum, unit) => sum + unit.width, 0) };
      });
      // With nothing to sit between them at the top, keep a gap there so the
      // first and last groups do not close up.
      if (!lead.length && groups.length > 2) groups.unshift({ key: '', units: [], width: slot });
      const depth = Math.max(0, ...groups.flatMap(group => group.units.map(unit => unit.seats.length ? unit.reach : 0)));
      const filled = groups.reduce((sum, group) => sum + group.width, 0), needed = filled + groups.length * GROUP_GAP;
      // A narrow canvas is short above its sheet: the oval is as flat as the council's seats allow (n103).
      const least = 38 + (depth ? depth + (compact ? 24 : 64) : (compact ? 64 : 104));
      const grow = fits => { let low = least, high = least + needed; for (let i = 0; i < 32; i++) { const middle = (low + high) / 2; if (fits(middle)) high = middle; else low = middle; } return high; };
      let rx, ry;
      if (compact) {
        // What the sheet leaves is wider than tall: the oval takes the width the canvas shows at the smallest readable
        // size (names either side) and is as flat as that allows.
        rx = Math.max(60, (rect.width - 4) / READABLE / 2 - 50);
        ry = least;
        if (perimeter(rx, ry) < needed) ry = grow(value => perimeter(rx, value) >= needed);
        // A tall panel is used, not left empty: the oval grows into spare height above the sheet.
        ry = Math.max(ry, Math.min(rx * 2.2, canvasHeight / 2 - Math.max(depth + 34, 96)));
      } else {
        const oval = a => perimeter(a * least, least) < needed ? grow(value => perimeter(a * value, value) >= needed) : least;
        let shape = aspect;
        if (tall) {
          // Of the ovals from tall to round, the one the open canvas shows largest (names either side, the seats above
          // and the names below, the figures on top), while the closed canvas, as high and wider, still fits it at a
          // readable size.
          const across = rect.width - DETAIL_WIDTH - 2 * EDGE, high = rect.height - 2 * EDGE;
          let best = -Infinity;
          for (let step = 0; step <= 12; step++) {
            const a = .4 + step * .05, value = oval(a), w = 2 * a * value + 110, h = 2 * value + depth + 60;
            const score = Math.min(across / w, high / h) + (high / h >= READABLE ? 10 : 0);
            if (score > best + 1e-9) { best = score; shape = a; }
          }
        }
        ry = oval(shape);
        rx = shape * ry;
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
          seeds.set(unit.agent.name, { x: point.x, y: point.y, r: unit.seats.length ? 29 : 23, reach: unit.reach, group: unit.agent.group, parent: '', dx: 0, dy: 0 });
          unit.seats.forEach((seat, i) => seeds.set(seat.name, { x: 0, y: 0, r: unit.members ? 23 : 19, reach: unit.members ? 23 : 19, group: seat.group, parent: unit.agent.name, dx: unit.offsets[i].dx, dy: unit.offsets[i].dy }));
        });
        cursor += gap;
      });
      const hub = byAgent.get(hubName);
      if (hub) seeds.set(hub.name, { x: 0, y: 0, r: 38, reach: 38, group: hub.group, parent: '', dx: 0, dy: 0 });
      agents.filter(dropped).forEach(a => seeds.set(a.name, { x: 0, y: 0, r: 23, reach: 23, group: a.group, parent: '', dx: 0, dy: 0 }));
      const placedByUser = movedHere();
      const targets = Array.from(seeds, ([name, seed]) => {
        const saved = placedByUser[name], pinned = !seed.parent && !!saved && Number.isFinite(saved.x);
        const seat = !!seed.parent && !!saved && Number.isFinite(saved.dx) ? { dx: saved.dx, dy: saved.dy } : {};
        return { name, ...seed, ...seat, ...(pinned ? { x: saved.x, y: saved.y } : {}), pinned, still: pinned || name === hubName };
      });
      const bodies = targets.filter(target => !target.parent), limit = body => Math.max(0, rect.width / 2 - body.reach - EDGE);
      const settle = () => {
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
      };
      settle();
      const placed = new Map(bodies.map(body => [body.name, body]));
      // Full loops: their moving agents go onto an even circle, in path order, in free space (placeRings); nothing else moves.
      if (rings.length) placeRings(rings, placed, targets, placedByUser, compact ? { width: rect.width, height: canvasHeight } : { width: rect.width - DETAIL_WIDTH, height: rect.height, wide: true });
      const before = new Map(Array.from(positions, ([name, point]) => [name, { x: point.x, y: point.y }]));
      // What this layout moved: agents new to the picture, and those going onto, off or along a loop's circle (`steady`
      // when nothing else moved: then the camera stays where it is, see settleCamera).
      const changed = [];
      let steady = true;
      targets.forEach(target => {
        if (target.parent) { const owner = placed.get(target.parent); target.x = owner.x + target.dx; target.y = owner.y + target.dy; }
        const point = positions.get(target.name) || {}, was = before.get(target.name);
        if (!was || Math.hypot(was.x - target.x, was.y - target.y) > .5) { changed.push(target.name); if (was && !target.ring && !point.ring && !(target.parent && placed.get(target.parent)?.ring)) steady = false; }
        positions.set(target.name, Object.assign(point, { x: target.x, y: target.y, r: target.r, reach: target.reach, group: target.group, parent: target.parent, dx: target.dx, dy: target.dy, fixed: target.pinned, ring: target.ring || '' }));
      });
      layoutChange = { changed, steady: steady && before.size > 0 };
      if (glide) glideFrom(before);
    }
    function reducedMotion() { return root.dataset.agMotion === 'none' || !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches; }
    // One short glide from where the agents were to where the layout put them (a loop formed or went), then still.
    let glideState = null;
    function glideFrom(before) {
      const items = [];
      positions.forEach((point, name) => { const from = before.get(name); if (from && Number.isFinite(from.x) && Math.hypot(from.x - point.x, from.y - point.y) > .5) items.push({ name, fx: from.x, fy: from.y, tx: point.x, ty: point.y }); });
      if (!items.length) return;
      const started = performance.now();
      glideState = { items };
      const frame = now => {
        if (!alive || !glideState || glideState.items !== items) return;
        // A frame's time can be a little before the glide began: never step backwards.
        const t = clamp((now - started) / RING_MS, 0, 1), ease = 1 - (1 - t) ** 3;
        items.forEach(item => { const point = positions.get(item.name); if (point) { point.x = item.fx + (item.tx - item.fx) * ease; point.y = item.fy + (item.ty - item.fy) * ease; positionNode(item.name); } });
        updateEdges();
        if (t < 1) layoutFrame = requestAnimationFrame(frame); else { layoutFrame = null; glideState = null; }
      };
      items.forEach(item => { const point = positions.get(item.name); point.x = item.fx; point.y = item.fy; });
      layoutFrame = requestAnimationFrame(frame);
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
      // A lead carries its seats, so the council stays a circle while it moves; an agent on a loop's circle carries
      // the whole circle (and their seats), so the ring stays round.
      const ring = !lead && point.ring ? point.ring : '';
      const moving = new Set(ring ? Array.from(positions).filter(([, other]) => other.ring === ring).map(([key]) => key) : [name]);
      if (!lead) positions.forEach((child, childName) => { if (moving.has(child.parent)) moving.add(childName); });
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
      // A member keeps the place it had before it joined its group (where removing the group puts it back), and the
      // other way round.
      const kept = here[name] || {};
      if (ring) { const shift = here[ring] || { dx: 0, dy: 0 }; here[ring] = { dx: shift.dx + dx, dy: shift.dy + dy }; }
      else if (lead) { point.dx = point.x - lead.x; point.dy = point.y - lead.y; here[name] = { ...(Number.isFinite(kept.x) ? { x: kept.x, y: kept.y } : {}), dx: point.dx, dy: point.dy }; }
      else { point.fixed = true; here[name] = { x: point.x, y: point.y, ...(Number.isFinite(kept.dx) ? { dx: kept.dx, dy: kept.dy } : {}) }; }
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
        // The name's height, for lines that keep clear of it.
        const box = label.getBBox(); Object.assign(point, { labelLeft: box.x, labelRight: box.x + box.width, labelTop: box.y, labelBottom: box.y + box.height });
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
        const council = visibleAgents.some(a => a.parent === agent.name);
        node.classList.toggle('ag-council-lead', council);
        // A group's lead with its members round it is drawn as the council is.
        node.classList.toggle('ag-team-lead', !council && Array.from(positions.values()).some(point => point.parent === agent.name));
        node.querySelector('.ag-agent-name').textContent = displayName(agent);
        node.querySelector('title').textContent = flowErrors.get(agent.name) || agent.name;
        node.dataset.armed = String(!trailPending && trail.at(-1) === agent.name);
        node.dataset.trail = String(trail.includes(agent.name));
        const writes = (rosterAction ? (canAddAgent() ? ' N' : '') + (removableOf(agent.name).ok ? ' Delete' : '') : '');
        if (flowAction) node.setAttribute('aria-keyshortcuts', 'Enter Shift+Enter I Shift+F10 ContextMenu C L R Alt+ArrowUp Alt+ArrowDown Alt+ArrowLeft Alt+ArrowRight Backspace Escape' + writes);
        else if (writes) node.setAttribute('aria-keyshortcuts', writes.trim()); else node.removeAttribute('aria-keyshortcuts');
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
      // A group's member reports to its lead (its group line), not to the hub.
      const reporting = new Set(structureTeams().filter(team => shownAgents.has(team.lead)).flatMap(team => team.members.filter(name => name !== team.lead)));
      const queue = [];
      // An agent the user added is connected to nothing until it is on a flow or in a group, or works in this task.
      const placed = new Set([...routes().flatMap(flow => flow.path), ...structureTeams().flatMap(team => [team.lead, ...team.members])]);
      const loose = a => a.added === true && !placed.has(a.name) && !(a.busy > 0) && !stepsFor(a).length;
      const edges = visibleAgents.filter(a => a.name !== hubName && (a.parent || hubName) && (a.parent || !reporting.has(a.name)) && !loose(a)).map(a => ({ from: a.parent || hubName, to: a.name, kind: 'handoff', relation: a.parent ? 'membership' : 'supervision', active: (a.busy > 0 && !explained.has(a.name)) || (!a.parent && viaHub.has(a.name)) }));
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
          const flow = svg('line', { class: 'ag-flow', 'data-flow-kind': edge.kind, 'data-overlay-of': edge.from + '->' + edge.to });
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
        pair.line.dataset.active = String(!!edge.active);
        // A line moves only while work is travelling along it to an agent that is working.
        const moving = edge.active && edge.kind !== 'escalation' && (edge.kind !== 'feedback' || shownAgents.get(edge.to)?.busy > 0);
        pair.flow.style.display = moving ? '' : 'none';
        queue.push({ from: edge.from, to: edge.to, offset: edge.from < edge.to ? shift : -shift, gaps: [a.r + 5, b.r + 6], segment: [coordinates.x1, coordinates.y1, coordinates.x2, coordinates.y2],
          straight: () => { pair.line = shapeAs(pair.line, 'line'); pair.flow = shapeAs(pair.flow, 'line'); [pair.line, pair.flow].forEach(line => Object.entries(coordinates).forEach(([key, value]) => line.setAttribute(key, value))); },
          curve: d => { pair.line = shapeAs(pair.line, 'path'); pair.flow = shapeAs(pair.flow, 'path'); [pair.line, pair.flow].forEach(line => line.setAttribute('d', d)); } });
      });
      lines.forEach((pair, key) => { if (!keys.has(key)) { pair.line.remove(); pair.flow.remove(); lines.delete(key); } });
      bendQueue = queue;
      updateTeamLines();
      updateLoops();
      highlight();
    }
    // A line is the one element it always was: a straight <line>, or a <path> while it bends round an agent.
    function shapeAs(element, tag) {
      if (element.tagName.toLowerCase() === tag) return element;
      const next = svg(tag, {});
      Array.from(element.attributes).forEach(item => { if (!['x1', 'y1', 'x2', 'y2', 'd'].includes(item.name)) next.setAttribute(item.name, item.value); });
      element.replaceWith(next); return next;
    }
    // Lines round agents. A handoff, flow, escalation or group line between two agents stays straight unless it
    // passes through, or within CLEAR px of, a third agent's circle (NAME_CLEAR px of its name's box); then every line
    // on that pair bends together, as one smooth curve (a quadratic through the pair's middle), just far enough to
    // clear it. The seats round an end (its council or group members) are part of that end: a line bends for them
    // only when it would cross one.
    const CLEAR = 2, NAME_CLEAR = 1, BEND_STEP = 3;
    let bendQueue = [];
    function obstaclesFor(ends, box) {
      const list = [];
      positions.forEach((p, name) => {
        if (ends.includes(name) || !nodes.has(name) || !shownAgents.has(name)) return;
        const own = ends.includes(p.parent), r = p.r + (own ? 0 : CLEAR), pad = own ? 0 : NAME_CLEAR, label = Number.isFinite(p.labelTop) && Number.isFinite(p.labelLeft);
        const title = label ? { left: p.x + p.labelLeft - pad, right: p.x + p.labelRight + pad, top: p.y + p.labelTop - pad, bottom: p.y + p.labelBottom + pad } : null;
        const left = Math.min(p.x - r, title ? title.left : Infinity), right = Math.max(p.x + r, title ? title.right : -Infinity);
        const top = Math.min(p.y - r, title ? title.top : Infinity), bottom = Math.max(p.y + r, title ? title.bottom : -Infinity);
        if (box && (right < box.left || left > box.right || bottom < box.top || top > box.bottom)) return;
        list.push({ x: p.x, y: p.y, r });
        if (title) list.push(title);
      });
      return list;
    }
    function blockedAt(points, obstacles) {
      let penalty = 0;
      for (const [x, y] of points) for (const o of obstacles) {
        if (o.r !== undefined) { const d = Math.hypot(x - o.x, y - o.y); if (d < o.r) penalty += (o.r - d + 1) ** 2; }
        else if (x > o.left && x < o.right && y > o.top && y < o.bottom) penalty += (Math.min(x - o.left, o.right - x, y - o.top, o.bottom - y) + 1) ** 2;
      }
      return penalty;
    }
    function segmentPoints(sx, sy, ex, ey) {
      const count = Math.max(2, Math.ceil(Math.hypot(ex - sx, ey - sy) / 4));
      return Array.from({ length: count + 1 }, (_, i) => [sx + (ex - sx) * i / count, sy + (ey - sy) * i / count]);
    }
    function quadPoints(sx, sy, cx, cy, ex, ey) {
      const count = Math.max(4, Math.ceil((Math.hypot(cx - sx, cy - sy) + Math.hypot(ex - cx, ey - cy)) / 4));
      return Array.from({ length: count + 1 }, (_, i) => { const t = i / count, u = 1 - t; return [u * u * sx + 2 * u * t * cx + t * t * ex, u * u * sy + 2 * u * t * cy + t * t * ey]; });
    }
    // The curve of one line when its pair bends by `bend` (sideways, in the pair's own frame, at the middle).
    function curveOf(entry, bend, p, q) {
      const a = positions.get(entry.from), b = positions.get(entry.to);
      const distance = Math.hypot(q.x - p.x, q.y - p.y) || 1, nx = -(q.y - p.y) / distance, ny = (q.x - p.x) / distance;
      const apex = bend + entry.offset, cx = (p.x + q.x) / 2 + nx * 2 * apex, cy = (p.y + q.y) / 2 + ny * 2 * apex;
      const out = Math.hypot(cx - a.x, cy - a.y) || 1, back = Math.hypot(cx - b.x, cy - b.y) || 1;
      const sx = a.x + (cx - a.x) / out * entry.gaps[0], sy = a.y + (cy - a.y) / out * entry.gaps[0], ex = b.x + (cx - b.x) / back * entry.gaps[1], ey = b.y + (cy - b.y) / back * entry.gaps[1];
      return { d: 'M ' + sx + ' ' + sy + ' Q ' + cx + ' ' + cy + ' ' + ex + ' ' + ey, points: quadPoints(sx, sy, cx, cy, ex, ey) };
    }
    function bendLines(entries) {
      const pairs = new Map();
      entries.forEach(entry => { if (entry.from === entry.to) { entry.straight(); return; } const key = pairKey(entry.from, entry.to); if (!pairs.has(key)) pairs.set(key, []); pairs.get(key).push(entry); });
      pairs.forEach((list, key) => {
        const [first, second] = key.split('\n'), p = positions.get(first), q = positions.get(second);
        if (!p || !q) { list.forEach(entry => entry.straight()); return; }
        const distance = Math.hypot(q.x - p.x, q.y - p.y), limit = Math.max(60, distance * .9), reach = limit * 1.2 + 40;
        const obstacles = obstaclesFor([first, second], { left: Math.min(p.x, q.x) - reach, right: Math.max(p.x, q.x) + reach, top: Math.min(p.y, q.y) - reach, bottom: Math.max(p.y, q.y) + reach });
        if (list.every(entry => !blockedAt(segmentPoints(...entry.segment), obstacles))) { list.forEach(entry => entry.straight()); return; }
        const cost = bend => list.reduce((sum, entry) => sum + blockedAt(curveOf(entry, bend, p, q).points, obstacles), 0);
        let found = null, fallback = 0, least = Infinity;
        for (let size = BEND_STEP; size <= limit && found === null; size += BEND_STEP) for (const sign of [1, -1]) {
          const penalty = cost(sign * size);
          if (!penalty) { if (found === null) found = sign * size; }
          else if (penalty < least) { least = penalty; fallback = sign * size; }
        }
        // Just far enough: the smallest bend between the last step that did not clear and the first that did.
        if (found !== null) {
          const sign = Math.sign(found); let low = Math.abs(found) - BEND_STEP, high = Math.abs(found);
          for (let i = 0; i < 5; i++) { const middle = (low + high) / 2; if (cost(sign * middle)) low = middle; else high = middle; }
          found = sign * high;
        }
        list.forEach(entry => entry.curve(curveOf(entry, found ?? fallback, p, q).d));
      });
    }
    // Each group's lead joined to each member by a straight line (dotted, no arrow, no text). A left click picks one
    // (Delete then takes that member out of the group); a right-click, `i` or Enter opens the lead's details.
    function updateTeamLines() {
      const shown = new Set();
      structureTeams().forEach(team => team.members.forEach(member => {
        const a = positions.get(team.lead), b = positions.get(member);
        if (!a || !b || member === team.lead || !nodes.has(team.lead) || !nodes.has(member)) return;
        const key = team.id + '\n' + member;
        shown.add(key);
        let view = teamLines.get(key);
        if (!view) {
          const group = svg('g', { class: 'ag-team-line', 'data-team-line': key, role: 'button', tabindex: 0 });
          const hit = svg('line', { class: 'ag-team-hit' }), line = svg('line', { class: 'ag-team-edge', 'data-team-edge': team.lead + '->' + member });
          group.append(hit, line); teamLayer.append(group);
          view = { group, hit, line }; teamLines.set(key, view);
        }
        Object.assign(view, { team, member });
        view.group.dataset.teamId = team.id; view.group.dataset.member = member; view.group.dataset.lead = team.lead;
        view.group.setAttribute('aria-label', member + ' reports to ' + team.lead + ', group');
        if (teamAction) view.group.setAttribute('aria-keyshortcuts', 'Enter I Shift+F10 ContextMenu Delete'); else view.group.removeAttribute('aria-keyshortcuts');
        const distance = Math.hypot(b.x - a.x, b.y - a.y) || 1, ux = (b.x - a.x) / distance, uy = (b.y - a.y) / distance;
        const coordinates = { x1: a.x + ux * (a.r + 5), y1: a.y + uy * (a.r + 5), x2: b.x - ux * (b.r + 5), y2: b.y - uy * (b.r + 5) };
        bendQueue.push({ from: team.lead, to: member, offset: 0, gaps: [a.r + 5, b.r + 5], segment: [coordinates.x1, coordinates.y1, coordinates.x2, coordinates.y2],
          straight: () => { view.hit = shapeAs(view.hit, 'line'); view.line = shapeAs(view.line, 'line'); [view.hit, view.line].forEach(line => Object.entries(coordinates).forEach(([k, value]) => line.setAttribute(k, value))); },
          curve: d => { view.hit = shapeAs(view.hit, 'path'); view.line = shapeAs(view.line, 'path'); [view.hit, view.line].forEach(line => line.setAttribute('d', d)); } });
      }));
      teamLines.forEach((view, key) => { if (!shown.has(key)) { view.group.remove(); teamLines.delete(key); } });
      if (pickedMember && !teamLines.has(pickedMember)) pickedMember = '';
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
          const hits = [svg('path', { class: 'ag-line-hit' }), svg('path', { class: 'ag-line-hit' })];
          const title = svg('title'); group.append(...hits, forward, back, title); loopLayer.append(group);
          view = { group, forward, back, hits, title, count: null, loop }; loopViews.set(loop.id, view);
        }
        view.loop = loop;
        view.group.dataset.state = loop.state; view.group.dataset.active = String(!!loop.active);
        view.group.dataset.moving = String(!!loop.active && [loop.worker, loop.evaluator].some(name => shownAgents.get(name)?.busy > 0));
        view.group.setAttribute('aria-label', loop.worker + ' and ' + loop.evaluator + ', review loop, ' + loop.rounds + (loop.rounds === 1 ? ' round, ' : ' rounds, ') + named(LOOP_STATES, loop.state));
        view.title.textContent = loop.worker + ' ↔ ' + loop.evaluator;
        const geometry = reviewGeometry(a, b);
        view.geometry = geometry;
        view.forward.setAttribute('d', geometry.forward); view.back.setAttribute('d', geometry.back);
        view.hits[0].setAttribute('d', geometry.forward); view.hits[1].setAttribute('d', geometry.back);
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
    // Flows drawn in the structure: an open step is a straight line with an arrowhead (bent only round a third
    // agent); the closed part of a loop (loopStart) is a dotted ring. Steps of different flows on one pair are offset
    // so none hides another. A flow at rest is still; a new one draws in once. The pending path is drawn dimmed.
    function routes() { return (snapshot?.structure?.flows || []).filter(flow => flow && typeof flow.id === 'string' && Array.isArray(flow.path) && flow.path.length >= 2 && flow.path.every(name => typeof name === 'string')); }
    function routeById(id) { return routes().find(flow => flow.id === id) || null; }
    // Step `index` (path[index] -> path[index + 1]) goes back to an agent already on the path; the hub ending a path adds no return.
    function returnsAt(path, index) {
      const to = path[index + 1];
      return path.slice(0, index + 1).includes(to) && !(index + 1 === path.length - 1 && isHubAgent(to));
    }
    function pairKey(a, b) { return a < b ? a + '\n' + b : b + '\n' + a; }
    function isSelfLoop(path) { return path.length === 2 && path[0] === path[1]; }
    // THE definition of a loop in this view: a path that ends at an agent it already passed through. It returns where
    // the closed part starts (0 for A, B, ..., A; 1 for A, B, C, B, whose lead-in A -> B stays an open step), or -1
    // for a path that does not close. A loop on one agent (A, A) is drawn on its own (selfLoopPath). Every step of
    // the closed part is drawn as one closed, dotted ring through its agents in order; every other step is a
    // straight solid line that bends only to clear a third agent. The agents of a full loop move onto a circle of
    // their own (loopRings).
    function loopStart(path) {
      if (isSelfLoop(path)) return -1;
      const start = path.indexOf(path.at(-1));
      return start < path.length - 2 ? start : -1;
    }
    function lerpPoint(a, b, t) { return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }; }
    function cubicAt(c, t) { const u = 1 - t; return { x: u * u * u * c[0].x + 3 * u * u * t * c[1].x + 3 * u * t * t * c[2].x + t * t * t * c[3].x, y: u * u * u * c[0].y + 3 * u * u * t * c[1].y + 3 * u * t * t * c[2].y + t * t * t * c[3].y }; }
    function splitCubic(c, t) { const a = lerpPoint(c[0], c[1], t), b = lerpPoint(c[1], c[2], t), d = lerpPoint(c[2], c[3], t), e = lerpPoint(a, b, t), f = lerpPoint(b, d, t), g = lerpPoint(e, f, t); return [[c[0], a, e, g], [g, f, d, c[3]]]; }
    // Step i of a ring through `points` (agents in path order, closing back to the first): a cubic whose handles
    // follow the ring's tangents and are sized so that agents on an even circle give a round circle. Two agents make
    // a circle on their distance; the step stops short of both rims, as a straight step does.
    function ringCurve(points, i, scale = 1) {
      const m = points.length, a = points[i], b = points[(i + 1) % m], chord = Math.hypot(b.x - a.x, b.y - a.y) || 1;
      let ta, tb;
      if (m === 2) { ta = { x: -(b.y - a.y) / chord, y: (b.x - a.x) / chord }; tb = { x: -ta.x, y: -ta.y }; }
      else {
        const tangent = j => { const before = points[(j - 1 + m) % m], after = points[(j + 1) % m], d = Math.hypot(after.x - before.x, after.y - before.y) || 1; return { x: (after.x - before.x) / d, y: (after.y - before.y) / d }; };
        ta = tangent(i); tb = tangent((i + 1) % m);
      }
      const h = chord * scale / (3 * Math.cos(Math.PI / (2 * m)) ** 2);
      const curve = [{ x: a.x, y: a.y }, { x: a.x + ta.x * h, y: a.y + ta.y * h }, { x: b.x - tb.x * h, y: b.y - tb.y * h }, { x: b.x, y: b.y }];
      const away = (t, p, r) => Math.hypot(cubicAt(curve, t).x - p.x, cubicAt(curve, t).y - p.y) >= r;
      let low = 0, high = .5;
      for (let k = 0; k < 18; k++) { const middle = (low + high) / 2; if (away(middle, a, a.r + 6)) high = middle; else low = middle; }
      const t0 = high; low = .5; high = 1;
      for (let k = 0; k < 18; k++) { const middle = (low + high) / 2; if (away(middle, b, b.r + 9)) low = middle; else high = middle; }
      const t1 = low, tail = splitCubic(curve, t0)[1], part = splitCubic(tail, (t1 - t0) / (1 - t0 || 1))[0];
      const f = v => Math.round(v * 100) / 100;
      return { d: 'M ' + f(part[0].x) + ' ' + f(part[0].y) + ' C ' + part.slice(1).map(q => f(q.x) + ' ' + f(q.y)).join(' '), points: Array.from({ length: 41 }, (_, k) => { const q = cubicAt(part, k / 40); return [q.x, q.y]; }) };
    }
    // A ring step keeps clear of third agents by bowing further out (or less) when it has to.
    function ringStep(names, points, i) {
      const ends = [names[i], names[(i + 1) % names.length]], first = ringCurve(points, i), obstacles = obstaclesFor(ends);
      if (!obstacles.length || !blockedAt(first.points, obstacles)) return first.d;
      for (const scale of [1.3, 1.6, 2, 2.5, .75, .5]) { const shape = ringCurve(points, i, scale); if (!blockedAt(shape.points, obstacles)) return shape.d; }
      return first.d;
    }
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
    // A forward step: straight, offset sideways in the pair's own frame (bendLines may bend it round an agent).
    function straightStep(a, b, from, to, lane) {
      const p = from < to ? a : b, q = from < to ? b : a;
      const distance = Math.hypot(q.x - p.x, q.y - p.y) || 1, nx = -(q.y - p.y) / distance, ny = (q.x - p.x) / distance;
      const offset = lane.offset, ux = (b.x - a.x) / distance, uy = (b.y - a.y) / distance;
      const segment = [a.x + ux * (a.r + 6) + nx * offset, a.y + uy * (a.r + 6) + ny * offset, b.x - ux * (b.r + 9) + nx * offset, b.y - uy * (b.r + 9) + ny * offset];
      return { d: 'M ' + segment[0] + ' ' + segment[1] + ' L ' + segment[2] + ' ' + segment[3], segment };
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
          if (flowAction) group.setAttribute('aria-keyshortcuts', 'Enter I Shift+F10 ContextMenu Delete Backspace' + (canRun() ? ' G' : ''));
          const title = svg('title'); group.append(title); routeLayer.append(group);
          view = { group, title, steps: [], flow }; routeViews.set(flow.id, view);
        }
        view.flow = flow;
        while (view.steps.length > flow.path.length - 1) { const step = view.steps.pop(); step.hit.remove(); step.line.remove(); }
        while (view.steps.length < flow.path.length - 1) {
          const hit = svg('path', { class: 'ag-route-hit' }), line = svg('path', { class: 'ag-route-step', pathLength: 1, 'marker-end': 'url(#' + uid + '-route)' });
          view.group.insertBefore(hit, view.title); view.group.insertBefore(line, view.title); view.steps.push({ hit, line });
        }
        const start = loopStart(flow.path), ringNames = start >= 0 ? flow.path.slice(start, -1) : null, ringPoints = ringNames && ringNames.map(name => positions.get(name));
        view.group.dataset.loop = start === 0 ? 'full' : start > 0 ? 'part' : isSelfLoop(flow.path) ? 'self' : 'none';
        view.steps.forEach((step, i) => {
          const key = pairKey(flow.path[i], flow.path[i + 1]), sharing = lanes.get(key), lane = laneOf(taken.get(key), sharing.indexOf(flow.id + '#' + i), sharing.length);
          const self = isSelfLoop(flow.path), ring = start >= 0 && i >= start, back = !self && !ring && returnsAt(flow.path, i), set = d => { step.line.setAttribute('d', d); step.hit.setAttribute('d', d); };
          step.line.dataset.step = self ? 'self' : ring ? 'loop' : back ? 'return' : 'forward'; step.line.dataset.index = String(i);
          // A ring's dots are measured in drawing units, the same all round; the other steps draw in by length.
          if (ring) step.line.removeAttribute('pathLength'); else step.line.setAttribute('pathLength', 1);
          if (self) { set(selfLoopPath(points[0], flow.path[0])); return; }
          if (ring) { set(ringStep(ringNames, ringPoints, i - start)); return; }
          const line = straightStep(points[i], points[i + 1], flow.path[i], flow.path[i + 1], lane);
          bendQueue.push({ from: flow.path[i], to: flow.path[i + 1], offset: lane.offset, gaps: [points[i].r + 6, points[i + 1].r + 9], segment: line.segment, straight: () => set(line.d), curve: set });
        });
        view.group.setAttribute('aria-label', 'Flow ' + flow.path.join(' → ')); view.title.textContent = flow.path.join(' → ');
        if (freshRoutes.has(flow.id)) {
          freshRoutes.delete(flow.id); view.group.dataset.fresh = 'true';
          later(() => { delete view.group.dataset.fresh; }, 400);
        }
      });
      routeViews.forEach((view, id) => { if (!shown.has(id)) { view.group.remove(); routeViews.delete(id); } });
      const queue = bendQueue; bendQueue = [];
      bendLines(queue);
      updateTrail();
    }
    function updateTrail() {
      const queue = [];
      trailLayer.replaceChildren();
      trailLayer.dataset.pending = String(trailPending);
      const start = loopStart(trail), ringNames = start >= 0 ? trail.slice(start, -1) : null, ringPoints = ringNames && ringNames.map(name => positions.get(name));
      if (ringPoints?.some(point => !point)) return;
      trail.slice(1).forEach((to, i) => {
        const a = positions.get(trail[i]), b = positions.get(to);
        if (!a || !b) return;
        if (trail[i] === to) { trailLayer.append(svg('path', { class: 'ag-trail-step', 'data-step': 'self', d: selfLoopPath(a, to), 'marker-end': 'url(#' + uid + '-route)' })); return; }
        if (start >= 0 && i >= start) {
          trailLayer.append(svg('path', { class: 'ag-trail-step', 'data-step': 'loop', d: ringStep(ringNames, ringPoints, i - start), 'marker-end': 'url(#' + uid + '-route)' }));
          return;
        }
        const back = returnsAt(trail, i);
        const lane = laneOf(takenBands().get(pairKey(trail[i], to)), 0, 1);
        const line = straightStep(a, b, trail[i], to, lane);
        const step = svg('path', { class: 'ag-trail-step', 'data-step': back ? 'return' : 'forward', d: line.d, 'marker-end': 'url(#' + uid + '-route)' });
        trailLayer.append(step);
        queue.push({ from: trail[i], to, offset: lane.offset, gaps: [a.r + 6, b.r + 9], segment: line.segment, straight: () => step.setAttribute('d', line.d), curve: d => step.setAttribute('d', d) });
      });
      bendLines(queue);
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
          const ring = svg('path', { class: 'ag-work-loop-edge', 'data-work-loop': loop.agent }), hit = svg('path', { class: 'ag-line-hit' });
          const title = svg('title'); group.append(hit, ring, title); loopLayer.append(group);
          view = { group, ring, hit, title, loop }; workLoopViews.set(loop.agent, view);
        }
        view.loop = loop;
        view.group.dataset.loopState = loop.state; view.group.dataset.active = String(!!loop.active);
        view.group.dataset.moving = String(!!loop.active && shownAgents.get(loop.agent)?.busy > 0);
        view.group.dataset.quiet = String(['closed', 'idle'].includes(loop.state) && !shownAgents.get(loop.agent)?.busy);
        view.ring.dataset.loopState = loop.state; view.ring.dataset.active = String(!!loop.active);
        const radius = p.r + 9, open = (Number(shownAgents.get(loop.agent)?.busy) || 0) >= 2 ? .46 : .17, start = -Math.PI / 4 + open, end = -Math.PI / 4 - open;
        view.ring.setAttribute('d', 'M ' + (p.x + radius * Math.cos(start)) + ' ' + (p.y + radius * Math.sin(start)) + ' A ' + radius + ' ' + radius + ' 0 1 1 ' + (p.x + radius * Math.cos(end)) + ' ' + (p.y + radius * Math.sin(end)));
        view.hit.setAttribute('d', view.ring.getAttribute('d'));
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
      teamLayer.dataset.selecting = String(selecting);
      teamLines.forEach((view, key) => {
        const marked = pickedMember === key;
        view.group.dataset.selected = String(marked); view.group.setAttribute('aria-pressed', String(marked));
        view.group.dataset.related = String(selecting && [view.team.lead, view.member].includes(selected.key));
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
      if (type !== 'new-agent') failedDraft = null;
      if (type !== 'new-agent' && draft) { draft = null; drawDraft(); }
      if (type !== 'skill') skillView = null;
      if (!(type === 'agent' && teamDraft?.lead === key)) teamDraft = null;
      if (agent && agent.team !== activeTeam) switchTeam(agent.team);
      selected = { type, key }; selectedOrigin = origin || selectedOrigin; highlightedStep = step || '';
      if (step) openSteps.add(step);
      detailSignature = ''; detail.hidden = false; root.classList.add('ag-details-open'); renderDetail(); updateEdges(); scheduleResize();
      reveal();
      close.focus({ preventScroll: true });
      if (step) Array.from(detailScroll.querySelectorAll('[data-step]')).find(row => row.dataset.step === step)?.scrollIntoView({ block: 'nearest' });
    }
    function closeDetails() {
      failedDraft = null;
      if (draft) { draft = null; drawDraft(); }
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
    // An agent's skills as shown: undo puts a skill back where it was in the list (skillOrder), whatever order the
    // file keeps.
    // The order is kept with the positions (same storage, its own key), so a reload still shows the skill in its place
    // although the back end appends it to the file.
    const SKILL_ORDER_KEY = POSITIONS_KEY + ':skill-order';
    const skillOrder = loadSkillOrder(), skillGhosts = new Map();
    function loadSkillOrder() {
      const kept = new Map();
      try {
        const raw = positionStore()?.getItem(SKILL_ORDER_KEY), saved = raw ? JSON.parse(raw) : null;
        Object.entries(saved && typeof saved === 'object' ? saved : {}).forEach(([name, list]) => {
          if (Array.isArray(list) && list.every(item => typeof item === 'string')) kept.set(name, list.slice());
        });
      } catch (_) { /* nothing usable: the back end's order */ }
      return kept;
    }
    function setSkillOrder(name, list) {
      if (list) skillOrder.set(name, list); else skillOrder.delete(name);
      try {
        const store = positionStore();
        if (!store) return;
        if (skillOrder.size) store.setItem(SKILL_ORDER_KEY, JSON.stringify(Object.fromEntries(skillOrder))); else store.removeItem(SKILL_ORDER_KEY);
      } catch (_) { /* storage full or refused: the order still holds for this session */ }
    }
    function skillsOf(agent) {
      const list = skillOverride.get(agent.name) || agent.skills || [], order = skillOrder.get(agent.name);
      if (!order) return list;
      const rank = name => { const i = order.indexOf(name); return i < 0 ? order.length : i; };
      return list.map((name, i) => [name, i]).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map(([name]) => name);
    }
    // A skill just removed keeps its row (struck, its control off) until the pointer leaves the list or a second
    // passes, so the next row never slides under a second click.
    function ghostSkill(agentName, name, index) {
      const list = skillGhosts.get(agentName) || [];
      list.push({ name, index }); skillGhosts.set(agentName, list);
      later(() => clearGhosts(agentName), 1000);
    }
    function clearGhosts(agentName) { if (skillGhosts.delete(agentName)) redrawDetail(); }
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
        // With actions.skillfile a skill's name opens its text.
        if (skillfileAction) { const open = el('button', 'ag-skill-name ag-skill-open', text(name)); open.type = 'button'; open.dataset.skillOpen = text(name); open.setAttribute('aria-label', 'Open the text of ' + text(name)); item.append(open); }
        else item.append(el('span', 'ag-skill-name', text(name)));
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
        const shown = list.slice(), ghosts = (skillGhosts.get(agent.name) || []).filter(ghost => !list.includes(ghost.name));
        ghosts.forEach(ghost => shown.splice(Math.min(ghost.index, shown.length), 0, ghost.name));
        appendList(holder, shown, name => {
          row(holder, name, editable ? false : undefined);
          if (list.includes(name)) return;
          const gone = holder.lastElementChild; gone.dataset.removed = 'true'; gone.setAttribute('aria-disabled', 'true');
          // Its control is spent: it stays in place, says so and does nothing.
          gone.querySelectorAll('[data-skill-request]').forEach(button => { const spent = el('button', 'ag-text-button ag-skill-control', 'removed'); spent.type = 'button'; spent.disabled = true; spent.setAttribute('aria-label', name + ' removed'); button.replaceWith(spent); });
        });
        if (ghosts.length) holder.addEventListener('pointerleave', () => clearGhosts(agent.name), { once: true });
        if (editable) {
          const missing = everything.map(skill => skill.name).filter(name => !list.includes(name));
          if (missing.length) fold(holder, 'agent-skills-add', 'Add skills', missing, true);
          const every = control('Give it every skill', 'Give ' + agent.name + ' every installed skill', { agent: agent.name, all: true }, 'all');
          const line = el('div', 'ag-skill-all'); line.append(every);
          if (skillfileAction) { const fresh = el('button', 'ag-text-button ag-skill-new', 'New skill'); fresh.type = 'button'; fresh.dataset.skillNew = agent.name; line.append(fresh); }
          if (busy === 'all') { line.dataset.pending = 'true'; line.setAttribute('aria-busy', 'true'); }
          holder.append(line);
        }
      }
      if (!skillAction) return;
      if (!editable && agent.skills_reason) (holder || section(detailScroll, 'Skills')).append(el('p', 'ag-skill-reason', text(agent.skills_reason)));
      const notes = skillNotes.get(agent.name);
      // A refusal is brought into view, never left clipped below the panel's edge.
      if (notes?.length && holder) {
        notes.forEach(note => { const line = el('p', 'ag-skill-note', note.text); line.dataset.kind = note.kind; line.setAttribute('role', note.kind === 'error' ? 'alert' : 'status'); holder.append(line); });
        const error = holder.querySelector('.ag-skill-note[data-kind="error"]');
        if (error) later(() => error.isConnected && error.scrollIntoView({ block: 'nearest' }), 0);
      }
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
      answer = call(skillAction, JSON.parse(JSON.stringify(request)));
      return answer.then(async result => {
        const reply = result && typeof result === 'object' ? result : {};
        if (!alive) return reply;
        skillPending.delete(agent.name);
        const after = Array.isArray(reply.skills) ? reply.skills.filter(name => typeof name === 'string') : null;
        if (after) skillOverride.set(agent.name, after);
        // Where it went and when it applies: the back end's own message, else what the command does.
        const notes = reply.changed === false ? [{ kind: 'done', text: 'No change needed.' }] : [{ kind: 'done', text: (agent.added === true ? 'Changed in ' + agent.name + '\'s agent file. ' : 'Changed in every preset that lists ' + agent.name + ' and in SKILLS.md. ') + (text(reply.message || '') || 'Applies after OpenCode restarts; running sessions keep their skills.') }];
        (Array.isArray(reply.warnings) ? reply.warnings : []).forEach(warning => notes.push({ kind: 'warning', text: text(warning) }));
        skillNotes.set(agent.name, notes);
        if (record && reply.changed !== false) { remember({ op: 'skill', agent: agent.name, request, before, after: after || before }); if (skillOrder.has(agent.name)) setSkillOrder(agent.name, null); }
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
      requests.reduce((chain, next) => chain.then(() => askSkill(next, false)), Promise.resolve()).then(() => { if (alive && !before.includes('*')) { setSkillOrder(agent, before.slice()); redrawDetail(); } }, () => { if (alive) flowHistory.push(last); });
    }
    // ---- Agents, skill text and groups, written through actions.roster, actions.skillfile and actions.team ----
    // Every change is one request; its answer is followed by a fresh snapshot, and undo (Ctrl/Cmd+Z) sends the
    // opposite request. Refusals are shown beside what was asked, in the user's words kept where they typed them.
    function ask(fn, request) {
      return call(fn, JSON.parse(JSON.stringify(request))).then(result => result && typeof result === 'object' ? result : {});
    }
    // ---- No write from a stale view (contract n8 r4) ----
    // Every write that names an existing thing carries `expect`: the rev of it this view is showing (a text field keeps
    // the rev it was drawn with, so a change that arrived while the user typed is not overwritten). A snapshot without
    // rev (an older back end) sends none. A stale refusal (409) brings the view up to date before the refusal is
    // handled, is said plainly, and the user's typing stays where it was.
    const STALE_SAID = 'This was changed somewhere else since you opened it. The view now shows the latest; try again.';
    const EXPECTING = { flow: ['text', 'name', 'command', 'remove', 'prompt'], team: ['members', 'text', 'remove'], roster: ['text', 'rename', 'remove'], skillfile: ['write', 'delete'] };
    function isStale(error) { return !!error && typeof error === 'object' && (error.status === 409 || error.stale === true || /changed since you opened it/i.test(text(error.message || ''))); }
    // A write's reply gives the thing's new rev; until a snapshot fetched after that reply is drawn, it is the one shown.
    let fetchesStarted = 0, renderedFetch = 0;
    const replyRevs = new Map(), writing = new Map();
    function revOf(kind, key) {
      const own = replyRevs.get(kind + ':' + key);
      if (own && renderedFetch <= own.seq) return own.rev;
      if (own) replyRevs.delete(kind + ':' + key);
      const list = kind === 'flow' ? snapshot?.structure?.flows : kind === 'team' ? snapshot?.structure?.teams : kind === 'skill' ? snapshot?.skills : null;
      const item = kind === 'agent' ? agentsByName.get(key) : Array.isArray(list) ? list.find(entry => entry && (kind === 'skill' ? entry.name : entry.id) === key) : null;
      return item && typeof item.rev === 'string' ? item.rev : '';
    }
    function withRev(request, rev) { return rev ? { ...request, expect: rev } : request; }
    // What a write names: [kind, key], or ['', ''] for an add, a read or a restore.
    function thingOf(fn, request) {
      if (!request || typeof request !== 'object') return ['', ''];
      const op = request.op;
      return fn === flowAction && EXPECTING.flow.includes(op) ? ['flow', request.id]
        : fn === teamAction && EXPECTING.team.includes(op) ? ['team', request.id]
        : fn === rosterAction && EXPECTING.roster.includes(op) ? ['agent', request.name]
        : fn === skillfileAction && EXPECTING.skillfile.includes(op) ? ['skill', request.id]
        : fn === skillAction && typeof request.agent === 'string' ? ['agent', request.agent]
        : fn === runAction && typeof request.flow === 'string' ? ['flow', request.flow] : ['', ''];
    }
    // The request as sent: `expect` from what is shown, unless the caller gave the rev its field was drawn with.
    function stamped(fn, request) {
      const [kind, key] = thingOf(fn, request);
      return kind && !('expect' in request) ? withRev(request, revOf(kind, key)) : request;
    }
    // Writes to one thing go one after another, each expecting what the one before it wrote (its reply's rev).
    function call(fn, request) {
      const [kind, key] = thingOf(fn, request), id = kind ? kind + ':' + key : '', before = id ? writing.get(id) : null;
      const send = () => {
        let answer;
        try { answer = Promise.resolve(fn(stamped(fn, request))); } catch (error) { answer = Promise.reject(error); }
        return answer.then(result => {
          const rev = kind === 'flow' ? result?.flow?.rev : kind === 'team' ? result?.team?.rev : kind === 'skill' ? result?.rev : undefined;
          if (id && typeof rev === 'string' && rev) replyRevs.set(id, { rev, seq: fetchesStarted });
          return result;
        });
      };
      const answer = before ? before.then(send, send) : send();
      if (id) { const settled = answer.then(() => {}, () => {}); writing.set(id, settled); settled.then(() => { if (writing.get(id) === settled) writing.delete(id); }); }
      return answer.catch(async error => { if (isStale(error) && alive) { try { await afterChange(); } catch (_) { /* the refusal is what is said */ } } throw error; });
    }
    // After a stale refusal: the field the user typed in (drawn again or not) holds their text and the latest rev.
    function keepTyped(old, selector, value, rev) {
      const field = old?.isConnected ? old : detailScroll.querySelector(selector);
      if (!field) return null;
      if (field.value !== value) field.value = value;
      if (rev) field.dataset.rev = rev; else delete field.dataset.rev;
      return field;
    }
    async function afterChange() { if (!alive) return; if (pending) await pending; await refresh(); }
    function rosterInfo() {
      const value = snapshot?.roster && typeof snapshot.roster === 'object' ? snapshot.roster : {};
      return { editable: value.editable === true, why: text(value.why || ''), models: Array.isArray(value.models) ? value.models.filter(model => typeof model === 'string' && model) : [] };
    }
    function canAddAgent() { return !!rosterAction && rosterInfo().editable; }
    // Whether Delete can turn an agent off, and why not. The orchestrator, the council and its seats never go.
    function removableOf(name) {
      const agent = agentsByName.get(name);
      if (!rosterAction || !agent || !shownAgents.has(name)) return { ok: false, why: '' };
      const fixed = name === hubName || agent.group === 'hub' ? 'The orchestrator cannot be deleted.' : isSeatAgent(agent) ? 'A council seat cannot be deleted.' : visibleAgents.some(other => other.parent === name) ? 'The council cannot be deleted.' : '';
      const value = agent.removable, own = value && typeof value === 'object' ? value : null;
      const said = text((own && (own.why || own.reason)) || agent.removable_reason || agent.removable_why || '');
      const ok = !fixed && rosterInfo().editable && (own ? own.ok === true || own.removable === true || own.value === true : value === true);
      return { ok, why: ok ? '' : said || fixed || (!rosterInfo().editable ? rosterInfo().why : '') || 'This agent cannot be deleted here.' };
    }
    function installedSkill(id) { return (snapshot?.skills || []).find(skill => skill && skill.name === id) || null; }
    function forceDetail() { detailSignature = ''; renderDetail(true); }
    function setNotes(name, kind, said) { agentNotes.set(name, [{ kind, text: said }]); }
    function undoFailed(last, error, name = hubName) { if (!alive) return; flowHistory.push(last); refuseFlow(name, error); }
    // Delete an agent: one request also takes its flows and groups; undo puts all of it back from the record.
    function removeAgent(name) {
      if (formBusy || !removableOf(name).ok) return;
      formBusy = true; agentNotes.delete(name);
      const added = agentsByName.get(name)?.added === true, open = selected?.type === 'agent' && selected.key === name;
      ask(rosterAction, { op: 'remove', name }).then(async reply => {
        formBusy = false;
        if (!alive) return;
        remember({ op: 'agent-remove', name, record: reply.record });
        if (trail.includes(name) && !trailPending) dropTrail();
        // When it applies, as the back end says: at once for an added agent, after a restart for a default one.
        const when = text(reply.message || '') || (added ? '' : 'Applies after OpenCode restarts.');
        announceLine(name + ' deleted. ' + when);
        if (open) {
          if (hubName && agentsByName.has(hubName)) { setNotes(hubName, 'done', name + ' deleted.' + (when ? ' ' + when : '') + ' Ctrl+Z puts it back.'); select('agent', hubName, nodes.get(hubName)); }
          else closeDetails();
        }
        keepFocus();
        await afterChange();
      }, error => {
        formBusy = false;
        if (!alive) return;
        // The agent stays; its details say why.
        setNotes(name, 'error', message(error, 'The agent could not be deleted'));
        if (selected?.type === 'agent' && selected.key === name) forceDetail(); else select('agent', name, nodes.get(name));
      });
    }
    function eligibleMembers(lead, taken = []) { return visibleAgents.map(agent => agent.name).filter(name => name !== lead && ordinaryAgent(name) && !taken.includes(name)).sort(); }
    function formError(form, said) {
      const line = form.querySelector('[data-form-error]');
      if (line) { line.textContent = said; line.hidden = !said; }
    }
    // Skill text: read when opened; Save writes it (refused when the file changed underneath, the user's text kept).
    function openSkill(id, from, origin) {
      if (!skillfileAction) return;
      const view = { id, from, state: 'loading', text: '', description: '', draft: null, error: '', note: '' };
      skillView = view;
      select('skill', id, origin);
      ask(skillfileAction, { op: 'read', id }).then(reply => {
        if (!alive || skillView !== view) return;
        Object.assign(view, { state: 'ready', text: text(reply.text), description: text(reply.description ?? installedSkill(id)?.description ?? ''), rev: typeof reply.rev === 'string' ? reply.rev : revOf('skill', id) });
        if (selected?.type === 'skill') forceDetail();
      }, error => {
        if (!alive || skillView !== view) return;
        Object.assign(view, { state: 'error', error: message(error, 'The skill could not be read') });
        if (selected?.type === 'skill') forceDetail();
      });
    }
    // After a skill save, create or delete the details are drawn again; focus goes back to the control used (or the
    // skill's row), never to the close button, unless the user has since put it somewhere else.
    // A details section's heading without its count ("Skills · 2" is "Skills").
    function headingOf(section) { return (section?.querySelector('h3')?.textContent || '').replace(/\s*·\s*\d+$/, ''); }
    const SKILL_CONTROLS = ['skillSave', 'skillCancel', 'skillDelete', 'skillText', 'skillOpen', 'skillNew'];
    function refocusDetail(target) {
      const now = document.activeElement;
      if (now && now !== document.body && now !== close && now.isConnected) return;
      const node = typeof target === 'function' ? target() : detailScroll.querySelector(target);
      node?.focus({ preventScroll: true });
    }
    function skillRowControl(id) {
      const row = Array.from(detailScroll.querySelectorAll('.ag-skill-row')).find(item => item.dataset.skill === id);
      return row?.querySelector('button:not([disabled])') || null;
    }
    function skillEditable(id) { return !!skillfileAction && installedSkill(id)?.editable === true; }
    function saveSkill() {
      const view = skillView, box = detailScroll.querySelector('[data-skill-text]');
      if (!view || !box || formBusy || !skillEditable(view.id)) return;
      const value = box.value, before = view.text;
      if (value === before) return;
      const used = document.activeElement === box ? '[data-skill-text]' : '[data-skill-save]';
      formBusy = true; view.error = ''; view.note = 'Saving'; forceDetail(); refocusDetail(used);
      ask(skillfileAction, withRev({ op: 'write', id: view.id, text: value }, view.rev || revOf('skill', view.id))).then(async reply => {
        formBusy = false;
        if (!alive) return;
        remember({ op: 'skillfile', id: view.id, undo: reply.record ? { op: 'restore', record: reply.record } : { op: 'write', id: view.id, text: before } });
        Object.assign(view, { text: value, draft: null, note: 'Saved', rev: typeof reply.rev === 'string' ? reply.rev : '' });
        if (skillView === view) { forceDetail(); refocusDetail(used); }
        await afterChange();
        if (skillView === view) refocusDetail(used);
      }, error => {
        formBusy = false;
        if (!alive) return;
        // Refused (the file changed underneath, or read-only): the message, and the user's text still in the box.
        Object.assign(view, { draft: value, note: '', error: message(error, 'Not saved') });
        if (isStale(error)) view.rev = revOf('skill', view.id);
        if (skillView === view) { forceDetail(); refocusDetail(used); }
      });
    }
    function deleteSkill() {
      const view = skillView;
      if (!view || formBusy || !skillEditable(view.id)) return;
      formBusy = true; view.error = ''; forceDetail(); refocusDetail('[data-skill-delete]');
      // Deleted from an agent's details: focus goes to that agent's skill list, where the skill was offered.
      const skillsPlace = () => Array.from(detailScroll.querySelectorAll('.ag-detail-section')).find(item => /^Skills/.test(item.querySelector('h3')?.textContent || ''))?.querySelector('button:not([disabled]), summary') || null;
      ask(skillfileAction, { op: 'delete', id: view.id }).then(async reply => {
        formBusy = false;
        if (!alive) return;
        remember({ op: 'skillfile', id: view.id, undo: { op: 'restore', record: reply.record } });
        const back = skillView === view && view.from && agentsByName.has(view.from);
        if (skillView === view) { skillView = null; if (back) { select('agent', view.from, nodes.get(view.from)); skillsPlace()?.focus({ preventScroll: true }); } else closeDetails(); }
        await afterChange();
        if (back && selected?.type === 'agent' && selected.key === view.from) refocusDetail(skillsPlace);
      }, error => {
        formBusy = false;
        if (!alive) return;
        view.error = message(error, 'The skill could not be deleted');
        if (skillView === view) { forceDetail(); refocusDetail('[data-skill-delete]'); }
      });
    }
    // A new skill, made and given to this agent in one request.
    function submitSkill(form, agentName) {
      if (formBusy || !skillfileAction) return;
      const id = form.querySelector('[data-new="id"]').value.trim(), description = form.querySelector('[data-new="description"]').value.trim(), body = form.querySelector('[data-new="text"]').value;
      if (!id) { formError(form, 'An id is needed.'); return; }
      formBusy = true; formError(form, ''); form.dataset.pending = 'true';
      ask(skillfileAction, { op: 'create', id, description, text: body, assign: agentName }).then(async reply => {
        formBusy = false; delete form.dataset.pending;
        if (!alive) return;
        remember({ op: 'skillfile-create', id, agent: agentName });
        // Where it went is the view's to say, when it applies the back end's.
        skillNotes.set(agentName, [{ kind: 'done', text: 'Made ' + id + ' and gave it to ' + agentName + '. ' + (text(reply.message || '') || 'Applies after OpenCode restarts.') }]);
        await afterChange();
        // Focus goes to the new skill's row in the agent's list.
        if (agentsByName.has(agentName)) { select('agent', agentName, nodes.get(agentName)); skillRowControl(id)?.focus({ preventScroll: true }); }
      }, error => {
        formBusy = false; delete form.dataset.pending;
        if (alive) formError(form, message(error, 'The skill could not be made'));
      });
    }
    // Groups: one request per change, followed by a fresh snapshot; the lead's details show what went wrong.
    function askTeam(request, lead, done) {
      if (!teamAction || formBusy) return Promise.resolve(null);
      formBusy = true; agentNotes.delete(lead);
      return ask(teamAction, request).then(async reply => {
        formBusy = false;
        if (!alive) return reply;
        done(reply);
        await afterChange();
        return reply;
      }, error => {
        formBusy = false;
        if (!alive) return null;
        setNotes(lead, 'error', message(error, 'The group could not be changed'));
        if (selected?.type === 'agent' && selected.key === lead) forceDetail(); else if (agentsByName.has(lead)) select('agent', lead, nodes.get(lead));
        return null;
      });
    }
    function createTeam(lead, members) {
      askTeam({ op: 'add', lead, members }, lead, reply => { teamDraft = null; if (reply.team?.id) remember({ op: 'team-add', id: reply.team.id }); }).then(reply => {
        if (!alive || !reply?.team?.id || !agentsByName.has(lead)) return;
        // A group just made opens its lead's details with the instruction focused and its made-up text selected.
        select('agent', lead, nodes.get(lead));
        const box = detailScroll.querySelector('[data-team-text]');
        if (box && !box.readOnly) { box.focus({ preventScroll: true }); box.select(); box.scrollIntoView({ block: 'nearest' }); }
      });
    }
    function setMembers(team, members) {
      const before = team.members.slice();
      if (!members.length) { removeTeam(team); return; }
      askTeam({ op: 'members', id: team.id, members }, team.lead, () => remember({ op: 'team-members', id: team.id, before, after: members.slice() }));
    }
    function removeMember(key) { const view = teamLines.get(key), team = view && teamById(view.team.id); if (team) setMembers(team, team.members.filter(name => name !== view.member)); }
    function removeTeam(team) {
      const box = detailScroll.querySelector('[data-team-text]'), words = box && box.dataset.teamText === team.id && !box.readOnly ? box.dataset.saved : team.text;
      askTeam({ op: 'remove', id: team.id }, team.lead, () => remember({ op: 'team-remove', id: team.id, lead: team.lead, members: team.members.slice(), text: words }));
    }
    function setTeamState(kind, said) {
      const state = detailScroll.querySelector('[data-team-state]');
      if (!state) return;
      state.dataset.teamState = kind; state.textContent = said || { saved: 'Saved', unsaved: 'Saving', saving: 'Saving' }[kind]; state.title = state.textContent;
    }
    // The instruction is saved when focus leaves it or on Ctrl/Cmd+Enter, as a flow's is; blank makes it again.
    function saveTeamText(box) {
      if (!teamAction || box.readOnly || box.dataset.saving === 'true') return;
      const id = box.dataset.teamText, before = box.dataset.saved, value = box.value, team = teamById(id);
      if (value === before || !team) return;
      const long = overLimit(value);
      if (long) { setTeamState('error', long); return; }
      box.dataset.saving = 'true'; setTeamState('saving');
      ask(teamAction, withRev({ op: 'text', id, text: value }, box.dataset.rev || revOf('team', id))).then(async reply => {
        box.dataset.saving = 'false';
        if (!alive) return;
        box.dataset.saved = typeof reply.team?.text === 'string' ? reply.team.text : value;
        if (box.value === value) box.value = box.dataset.saved;
        box.dataset.rev = typeof reply.team?.rev === 'string' ? reply.team.rev : '';
        remember({ op: 'team-text', id, before, after: box.dataset.saved });
        setTeamState(box.value === box.dataset.saved ? 'saved' : 'unsaved');
        await afterChange();
        // The next save expects what this one wrote (the reply's rev, else the snapshot that followed it).
        if (!box.dataset.rev) { const rev = revOf('team', id); if (rev) box.dataset.rev = rev; }
      }, error => {
        box.dataset.saving = 'false';
        if (!alive) return;
        if (isStale(error)) keepTyped(box, '[data-team-text="' + CSS.escape(id) + '"]', value, revOf('team', id));
        setTeamState('error', message(error, 'Not saved'));
      });
    }
    // Undo for the three back ends: the opposite request.
    function undoOther(last) {
      const done = () => afterChange();
      switch (last.op) {
        case 'agent-remove': ask(rosterAction, { op: 'restore', record: last.record }).then(done, error => undoFailed(last, error)); return true;
        case 'agent-add': ask(rosterAction, { op: 'remove', name: last.name }).then(() => {
          if (last.layout) { const now = copyMoves(last.layout); if (now[last.name] && !last.before[last.name]) { delete now[last.name]; if (Object.keys(now).length) moves[last.layout] = now; else delete moves[last.layout]; saveMoves(); } }
          if (selected?.type === 'agent' && selected.key === last.name) closeDetails();
          return done();
        }, error => undoFailed(last, error, last.name)); return true;
        // The rename's record gives the old name back byte for byte (refused, nothing changed, when anything it wrote
        // was changed since or the old name now belongs to another agent).
        case 'agent-rename': ask(rosterAction, { op: 'restore', record: last.record }).then(() => {
          if (!alive) return;
          movePlace(last.to, last.from);
          if (selected?.type === 'agent' && selected.key === last.to) { agentNotes.delete(last.to); setNotes(last.from, 'done', 'Renamed ' + last.to + ' back to ' + last.from + '.'); }
          return done().then(() => { if (alive && selected?.type === 'agent' && selected.key === last.to && agentsByName.has(last.from)) select('agent', last.from, nodes.get(last.from)); });
        }, error => undoFailed(last, error, agentsByName.has(last.to) ? last.to : last.from)); return true;
        case 'skillfile': ask(skillfileAction, last.undo).then(() => { if (skillView?.id === last.id) { skillView = null; if (selected?.type === 'skill') closeDetails(); } return done(); }, error => undoFailed(last, error)); return true;
        case 'skillfile-create':
          (skillAction ? askSkill({ agent: last.agent, skill: last.id, on: false }, false) : Promise.resolve())
            .then(() => ask(skillfileAction, { op: 'delete', id: last.id })).then(done, error => undoFailed(last, error, last.agent));
          return true;
        case 'team-add': ask(teamAction, { op: 'remove', id: last.id }).then(done, error => undoFailed(last, error)); return true;
        case 'team-members': ask(teamAction, { op: 'members', id: last.id, members: last.before }).then(done, error => undoFailed(last, error)); return true;
        case 'team-text': ask(teamAction, { op: 'text', id: last.id, text: last.before }).then(done, error => undoFailed(last, error)); return true;
        case 'team-remove':
          ask(teamAction, { op: 'add', lead: last.lead, members: last.members, text: last.text }).then(reply => {
            // A group put back may get a new id: later steps that name it follow it.
            if (reply.team?.id) flowHistory.forEach(entry => { if (entry.op?.startsWith('team-') && entry.id === last.id) entry.id = reply.team.id; });
            return done();
          }, error => undoFailed(last, error, last.lead));
          return true;
        default: return false;
      }
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
        else attention.append(el('p', 'ag-run-empty ag-attention-key', 'The red “! ' + attentionAgents().size + '” above the graph counts the agents here (a step failed, is blocked or stuck, or work was sent back); each has a red dot on its circle.'));
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
    // Lines under an agent's name after a change to it, then its own controls: Add agent on the orchestrator (or why
    // the roster cannot be written), Delete agent (or why it cannot go).
    function agentActions(agent) {
      (agentNotes.get(agent.name) || []).forEach(note => { const line = el('p', 'ag-agent-note', note.text); line.dataset.kind = note.kind; line.setAttribute('role', note.kind === 'error' ? 'alert' : 'status'); detailScroll.append(line); });
      if (!rosterAction) return;
      if (renamable(agent)) {
        const box = input('rename', agent.name); box.dataset.rename = agent.name; box.setAttribute('aria-label', 'Name');
        box.setAttribute('aria-keyshortcuts', 'Enter Escape Alt+ArrowUp Alt+ArrowDown Alt+ArrowLeft Alt+ArrowRight');
        const label = el('label', 'ag-field ag-rename'); label.append(el('span', 'ag-form-label', 'Name'), box); detailScroll.append(label);
      }
      const row = el('div', 'ag-agent-actions'), button = (label, data, keys) => { const item = el('button', 'ag-text-button', label); item.type = 'button'; item.setAttribute(data, agent.name); if (keys) item.setAttribute('aria-keyshortcuts', keys); return item; };
      // Agents are added with the + on the canvas; when the roster cannot be written, the orchestrator says why.
      if (agent.name === hubName && !canAddAgent() && rosterInfo().why) row.append(el('p', 'ag-form-reason ag-roster-reason', rosterInfo().why));
      const removable = removableOf(agent.name);
      if (removable.ok) row.append(button('Delete agent', 'data-agent-remove', 'Delete'));
      else if (removable.why && !(agent.name === hubName && !rosterInfo().editable && removable.why === rosterInfo().why)) row.append(el('p', 'ag-form-reason ag-remove-reason', removable.why));
      if (row.childNodes.length) detailScroll.append(row);
    }
    // An agent's group: whom it reports to; for a lead, its members (each with remove), an add-member control, the
    // instruction (edited as a flow's is) and Remove group; otherwise a way to make it a lead and choose members.
    function teamSection(agent) {
      const led = teamLedBy(agent.name), above = structureTeams().filter(team => team.members.includes(agent.name) && team.lead !== agent.name && shownAgents.has(team.lead));
      if (above.length) {
        const holder = section(detailScroll, 'Reports to'); holder.dataset.reportsTo = '';
        above.forEach(team => { const link = el('button', 'ag-family-link', team.lead); link.type = 'button'; link.dataset.flowPeer = team.lead; holder.append(link); });
      }
      if (led) {
        const holder = section(detailScroll, 'Group · ' + led.members.length); holder.dataset.teamSection = led.id;
        led.members.forEach(name => {
          const row = el('div', 'ag-team-member'), link = el('button', 'ag-family-link', name); link.type = 'button'; link.dataset.flowPeer = name; row.append(link);
          if (teamAction) { const drop = el('button', 'ag-text-button', 'remove'); drop.type = 'button'; drop.dataset.teamDrop = name; drop.setAttribute('aria-label', 'Take ' + name + ' out of the group'); row.append(drop); }
          holder.append(row);
        });
        if (teamAction) {
          const others = eligibleMembers(agent.name, led.members);
          if (others.length) {
            const line = el('div', 'ag-team-add'), choice = el('select', 'ag-input'), add = el('button', 'ag-text-button', 'Add member');
            choice.dataset.teamAdd = led.id; choice.setAttribute('aria-label', 'Agent to add to the group');
            others.forEach(name => { const option = el('option', '', name); option.value = name; choice.append(option); });
            add.type = 'button'; add.dataset.teamAddButton = led.id; line.append(choice, add); holder.append(line);
          }
        }
        const box = el('textarea', 'ag-route-text ag-team-text');
        box.value = led.text; box.dataset.teamText = led.id; box.dataset.saved = led.text; if (led.rev) box.dataset.rev = text(led.rev); box.rows = 5; box.spellcheck = false; box.setAttribute('aria-label', 'Group instruction');
        holder.append(el('h4', 'ag-form-label', 'Instruction'), box);
        if (teamAction) {
          const actions = el('div', 'ag-route-actions'), state = el('p', 'ag-route-state', 'Saved'), remove = el('button', 'ag-text-button', 'Remove group');
          state.dataset.teamState = 'saved'; state.setAttribute('aria-live', 'polite'); remove.type = 'button'; remove.dataset.teamRemove = led.id;
          actions.append(state, remove); holder.append(actions);
        } else box.readOnly = true;
      } else if (teamAction && ordinaryAgent(agent.name)) {
        const holder = section(detailScroll, 'Group');
        if (teamDraft?.lead === agent.name) {
          const list = el('div', 'ag-team-pick'), others = eligibleMembers(agent.name);
          others.forEach(name => { const label = el('label', 'ag-check'), box = el('input'); box.type = 'checkbox'; box.value = name; box.dataset.teamPick = ''; box.checked = teamDraft.members.includes(name); label.append(box, el('span', '', name)); list.append(label); });
          if (!others.length) list.append(el('p', 'ag-form-reason', 'No agent can join it.'));
          const actions = el('div', 'ag-route-actions'), create = el('button', 'ag-text-button', 'Create group'), cancel = el('button', 'ag-text-button', 'Cancel');
          create.type = 'button'; create.dataset.teamCreate = agent.name; cancel.type = 'button'; cancel.dataset.teamCancel = '';
          actions.append(create, cancel); holder.append(el('p', 'ag-form-reason', 'Members report to ' + agent.name + '.'), list, actions);
        } else { const start = el('button', 'ag-text-button', 'Make it a group lead'); start.type = 'button'; start.dataset.teamStart = agent.name; holder.append(start); }
      }
    }
    function field(form, label, control) { const item = el('label', 'ag-field'); item.append(el('span', 'ag-form-label', label), control); form.append(item); return control; }
    function input(key, value = '') { const item = el('input', 'ag-input'); item.type = 'text'; item.dataset.new = key; item.value = value; item.spellcheck = false; item.autocomplete = 'off'; return item; }
    function formEnd(form, label) {
      const actions = el('div', 'ag-form-actions'), submit = el('button', 'ag-form-submit', label), error = el('p', 'ag-form-error');
      submit.type = 'submit'; error.dataset.formError = ''; error.setAttribute('role', 'alert'); error.hidden = true;
      actions.append(submit); form.append(actions, error);
    }
    // ---- The +: a new agent from a blank, a copy or the verifier, dropped where the user wants it ----
    let draft = null, failedDraft = null;
    function freeName(base, from) { for (let i = from; ; i++) { const name = base + '-' + i; if (!agentsByName.has(name)) return name; } }
    function updatePlus() {
      const can = canAddAgent();
      plus.hidden = !can;
      if (!can) { closeAddMenu(false); if (draft) dropDraft(); }
    }
    function openAddMenu(fromKey) {
      if (!canAddAgent()) return;
      const item = (label, kind, source) => { const button = el('button', 'ag-add-item', label); button.type = 'button'; button.setAttribute('role', 'menuitem'); button.tabIndex = -1; button.dataset.addKind = kind; if (source) button.dataset.addSource = source; return button; };
      const copies = visibleAgents.filter(agent => ordinaryAgent(agent.name)).map(agent => agent.name).sort();
      const parts = [item('Blank agent', 'blank'), item('Verifier', 'verifier')];
      if (copies.length) { const head = el('div', 'ag-add-head', 'Copy of'); head.setAttribute('role', 'presentation'); parts.push(head, ...copies.map(name => item(name, 'copy', name))); }
      addMenu.replaceChildren(...parts);
      addMenu.hidden = false; plus.setAttribute('aria-expanded', 'true');
      if (fromKey) addMenu.querySelector('[role="menuitem"]')?.focus({ preventScroll: true });
    }
    function closeAddMenu(refocus) {
      if (addMenu.hidden) return;
      addMenu.hidden = true; plus.setAttribute('aria-expanded', 'false');
      if (refocus) plus.focus({ preventScroll: true });
    }
    // A spot where a new circle and its name sit clear of every agent's circle and name (n103): the one asked for, or
    // the nearest free. At least as far from each agent as the layout keeps agents apart, so adding it moves no other.
    function freeSpot(x, y, name = draft?.name || 'agent', room = 30) {
      const others = Array.from(positions, ([key, p]) => ({ p, shape: shapeAt(key, p.x, p.y, p.r, !!p.labelUp, p.r === 29) }));
      const clear = (px, py) => {
        const mine = shapeAt(name, px, py, 23, false, false);
        return others.every(({ p, shape }) => Math.hypot(p.x - px, p.y - py) >= Math.max(p.r + 23 + room, p.parent ? 0 : (p.reach || p.r) + 23 + 18) + .5 && !clashOf(mine, shape));
      };
      if (clear(x, y)) return { x, y };
      for (let ring = 1; ring <= 60; ring++) {
        const distance = ring * 6, count = Math.max(8, Math.round(distance / 4));
        for (let i = 0; i < count; i++) { const angle = i * Math.PI * 2 / count, px = x + distance * Math.cos(angle), py = y + distance * Math.sin(angle); if (clear(px, py)) return { x: px, y: py }; }
      }
      return { x, y };
    }
    function drawDraft() {
      if (!draft) { draftLayer.replaceChildren(); return; }
      if (!draftLayer.firstChild) {
        const node = svg('g', { class: 'ag-agent ag-draft', 'data-draft': '' });
        node.append(svg('circle', { class: 'ag-core', r: 23 }), svg('text', { class: 'ag-agent-name', 'text-anchor': 'middle', y: 43 }));
        draftLayer.append(node);
      }
      const node = draftLayer.firstChild;
      node.setAttribute('transform', 'translate(' + draft.x + ' ' + draft.y + ')');
      node.dataset.following = String(draft.following);
      node.querySelector('text').textContent = draft.name;
    }
    // Choosing a starting point: by pointer the circle follows it until a click or a release drops it; by keyboard
    // it is placed at once at a free spot near the middle (Alt+arrows move it while it is being named).
    function startDraft(kind, source, event) {
      closeAddMenu(false);
      if (draft) dropDraft();
      const name = kind === 'verifier' ? freeName('verifier', 2) : kind === 'copy' ? freeName(source, 2) : freeName('agent', 1);
      draft = { kind, source: source || '', name, x: 0, y: 0, following: !!event, held: !!event && event.type === 'pointerdown', startX: event?.clientX ?? 0, startY: event?.clientY ?? 0 };
      if (event) { const point = stagePoint(event.clientX, event.clientY); draft.x = point.x; draft.y = point.y; drawDraft(); }
      else {
        const rect = viewport.getBoundingClientRect(), middle = stagePoint(rect.left + rect.width / 2, rect.top + rect.height / 2), hub = positions.get(hubName) || middle;
        // By keyboard: with room round it for Alt+arrows (two presses any way stay clear of every agent).
        const spot = freeSpot(hub.x + 140, hub.y - 40, name, 14 + 2 * NUDGE_PX); Object.assign(draft, spot); placeDraft();
      }
    }
    function followDraft(event) {
      if (!draft?.following) return;
      const point = stagePoint(event.clientX, event.clientY); draft.x = point.x; draft.y = point.y; drawDraft();
    }
    // Dropped: it settles beside any agent it would cover and is added at once (`roster add`) under its default
    // name; its details then open with the name selected, to be typed over.
    function placeDraft() {
      if (!draft || draft.pending) return;
      Object.assign(draft, freeSpot(draft.x, draft.y), { following: false, held: false, placed: true, pending: true, error: '' });
      drawDraft(); createDraft();
    }
    function createDraft() {
      const made = draft, request = { op: 'add', name: made.name };
      failedDraft = null;
      if (made.kind === 'verifier') request.template = 'verifier';
      else if (made.kind === 'copy') request.copy_of = made.source;
      else {
        // A blank agent says so: a name alone is not enough for every back end (it may ask for a description).
        request.template = 'blank';
        if (rosterInfo().models[0]) request.model = rosterInfo().models[0];
      }
      formBusy = true;
      ask(rosterAction, request).then(async reply => {
        formBusy = false;
        if (!alive) return;
        const name = text(reply.agent?.name || reply.name || '') || made.name;
        // Its place is kept: where it was dropped.
        const layout = layoutKey(), before = copyMoves(layout);
        (moves[layout] ||= {})[name] = { x: made.x, y: made.y }; saveMoves();
        remember({ op: 'agent-add', name, layout, before });
        // When it applies is the back end's to say (an added agent is live at once).
        const said = text(reply.message || '');
        setNotes(name, 'done', name + ' added.' + (said ? ' ' + said : ''));
        announceLine(name + ' added.' + (said ? ' ' + said : ''));
        await afterChange();
        if (draft === made) { draft = null; drawDraft(); }
        if (agentsByName.has(name)) { select('agent', name, nodes.get(name)); focusRename(); }
        else if (selected?.type === 'new-agent') closeDetails();
      }, error => {
        formBusy = false;
        if (!alive || draft !== made) return;
        // Refused: the circle goes at once (nothing was added); the reason stays in the details, with Try again.
        Object.assign(made, { pending: false, error: message(error, 'The agent could not be added') });
        draft = null; failedDraft = made; drawDraft();
        announceLine(made.error); select('new-agent', '', plus);
      });
    }
    function focusRename() {
      const box = detailScroll.querySelector('[data-rename]');
      if (box) { box.focus({ preventScroll: true }); box.select(); }
    }
    function dropDraft() {
      if (draft?.pending) return;
      draft = null; failedDraft = null; drawDraft();
      if (selected?.type === 'new-agent') closeDetails();
    }
    // Only when adding failed: the reason, and the choice to try again or let it go.
    function renderNewAgent() {
      eyebrow.textContent = 'New agent';
      const shown = draft || failedDraft;
      if (!shown) { detailScroll.append(el('p', 'ag-form-reason', 'Choose a starting point with the + button.')); return; }
      detailScroll.append(el('h2', 'ag-detail-title', shown.name));
      if (shown.error) { const line = el('p', 'ag-form-error', shown.error); line.setAttribute('role', 'alert'); detailScroll.append(line); }
      const row = el('div', 'ag-agent-actions');
      const again = el('button', 'ag-text-button', 'Try again'); again.type = 'button'; again.dataset.draftRetry = '';
      const cancel = el('button', 'ag-text-button', 'Cancel'); cancel.type = 'button'; cancel.dataset.draftCancel = '';
      row.append(again, cancel); detailScroll.append(row);
    }
    // An agent the user added can be renamed in its details, on flows and in groups too: one roster request
    // { op: "rename", name, to } (one step, one undo record; flows and groups keep their ids). The back end refuses what
    // it cannot rename, and a refusal changes nothing.
    function renamable(agent) { return !!agent && agent.added === true && canAddAgent(); }
    // Moves the agent's place in every layout from one name to the other.
    function movePlace(from, to) {
      let changed = false;
      Object.keys(moves).forEach(layout => { const here = moves[layout]; if (here[from]) { here[to] = here[from]; delete here[from]; changed = true; } });
      if (changed) saveMoves();
    }
    function renameAgent(from, to, box) {
      if (formBusy || !to || to === from) return;
      if (agentsByName.has(to)) { setNotes(from, 'error', to + ' is already an agent.'); forceDetail(); focusRename(); return; }
      formBusy = true; if (box) box.dataset.saving = 'true';
      ask(rosterAction, { op: 'rename', name: from, to }).then(async reply => {
        formBusy = false;
        if (!alive) return;
        movePlace(from, to);
        const last = flowHistory.at(-1);
        // Renaming an agent just added is part of adding it: undo deletes it under its new name.
        if (last?.op === 'agent-add' && last.name === from) { last.name = to; if (last.before) delete last.before[to]; }
        else remember({ op: 'agent-rename', from, to, record: reply.record });
        agentNotes.delete(from); setNotes(to, 'done', 'Renamed ' + from + ' to ' + to + '.');
        announceLine('Renamed ' + from + ' to ' + to + '.');
        await afterChange();
        if (agentsByName.has(to)) select('agent', to, nodes.get(to));
        keepFocus();
      }, error => {
        formBusy = false;
        if (!alive) return;
        const said = message(error, 'The agent could not be renamed');
        setNotes(from, 'error', said); announceLine(said);
        if (selected?.type === 'agent' && selected.key === from) { forceDetail(); if (isStale(error)) keepTyped(null, '[data-rename]', to, ''); focusRename(); }
      });
    }
    // Undo keeps working after the element that had focus is removed (a deleted line, a removed skill row): while
    // the last thing the user touched was inside the view, focus that would fall to the page comes back to the
    // canvas, and Ctrl/Cmd+Z pressed on the page itself still reaches the view.
    let lastInside = false;
    function keepFocus() {
      const settle = () => { if (alive && lastInside && (!document.activeElement || document.activeElement === document.body || !document.activeElement.isConnected)) viewport.focus({ preventScroll: true }); };
      settle(); later(settle, 0);
    }
    function announceLine(said) { trailStatus.textContent = ''; later(() => { if (!trail.length) trailStatus.textContent = said; }, 30); }
    function renderNewSkill(agentName) {
      eyebrow.textContent = 'New skill';
      detailScroll.append(el('h2', 'ag-detail-title', 'New skill for ' + agentName));
      const form = el('form', 'ag-form'); form.dataset.form = 'new-skill:' + agentName; form.noValidate = true;
      field(form, 'Id', input('id'));
      field(form, 'Description', input('description'));
      const body = el('textarea', 'ag-route-text ag-skill-text'); body.dataset.new = 'text'; body.rows = 10; body.spellcheck = false;
      field(form, 'Text', body);
      formEnd(form, 'Create skill');
      detailScroll.append(form);
    }
    function renderSkill(id) {
      const view = skillView && skillView.id === id ? skillView : null, info = installedSkill(id), editable = skillEditable(id);
      eyebrow.textContent = 'Skill';
      detailScroll.append(el('h2', 'ag-detail-title', id));
      if (view?.from && agentsByName.has(view.from)) { const back = el('button', 'ag-family-link', '← ' + view.from); back.type = 'button'; back.dataset.flowPeer = view.from; detailScroll.append(back); }
      const about = view?.description || info?.description;
      if (about) detailScroll.append(el('p', 'ag-detail-about', text(about)));
      const form = el('div', 'ag-form'); form.dataset.form = 'skill:' + id;
      if (!view || view.state === 'loading') form.append(el('p', 'ag-form-reason', 'Loading'));
      else if (view.state === 'ready') {
        const box = el('textarea', 'ag-route-text ag-skill-text');
        box.value = view.draft ?? view.text; box.dataset.skillText = id; box.rows = 14; box.spellcheck = false; box.readOnly = !editable; box.setAttribute('aria-label', 'Skill text');
        form.append(box);
        if (editable) {
          const actions = el('div', 'ag-route-actions'), state = el('p', 'ag-route-state', view.note || (view.draft != null && view.draft !== view.text ? 'Changed' : 'Saved'));
          const save = el('button', 'ag-text-button', 'Save'), cancel = el('button', 'ag-text-button', 'Cancel'), remove = el('button', 'ag-text-button', 'Delete skill');
          state.dataset.skillState = ''; state.setAttribute('aria-live', 'polite');
          // Busy: aria-disabled, not disabled, so the button used keeps focus while its request is out.
          [[save, 'skillSave'], [cancel, 'skillCancel'], [remove, 'skillDelete']].forEach(([item, key]) => { item.type = 'button'; item.dataset[key] = id; if (formBusy) item.setAttribute('aria-disabled', 'true'); });
          const buttons = el('span', 'ag-form-buttons'); buttons.append(save, cancel, remove);
          actions.append(state, buttons); form.append(actions);
        } else form.append(el('p', 'ag-form-reason ag-skill-readonly', text(info?.why) || (skillfileAction ? 'This skill is read-only here.' : 'Skill text cannot be changed here.')));
      }
      if (view?.error) { const error = el('p', 'ag-form-error', view.error); error.setAttribute('role', 'alert'); form.append(error); }
      detailScroll.append(form);
    }
    function renderDetail(force = false) {
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
      // Nor a path's goal or name while it has focus (they are redrawn in place when their answer comes).
      const holding = selected.type === 'flow' && detailScroll.contains(document.activeElement) ? document.activeElement.closest('[data-run-goal], [data-route-name]') : null;
      if (!force && holding && routeById(selected.key)) return;
      // Nor is a group's instruction, or a form being filled in (it is rebuilt when its answer comes).
      const teamBox = selected.type === 'agent' ? detailScroll.querySelector('[data-team-text]') : null;
      if (teamBox && teamById(teamBox.dataset.teamText) && (document.activeElement === teamBox || teamBox.value !== teamBox.dataset.saved)) return;
      if (!force && detailScroll.querySelector('[data-form]')?.dataset.form === selected.type + ':' + selected.key) return;
      // Nor the name of an agent being renamed.
      const renaming = selected.type === 'agent' ? detailScroll.querySelector('[data-rename]') : null;
      if (!force && renaming && renaming.dataset.rename === selected.key && agentsByName.has(selected.key) && (document.activeElement === renaming || renaming.value !== selected.key)) return;
      detailSignature = signature;
      const scroll = detailScroll.scrollTop;
      const focused = document.activeElement;
      const focusedStep = detailScroll.contains(focused) ? focused.closest('[data-step]')?.dataset.step : null;
      // A skill control with focus gets it back after the rebuild (the same control of the same skill).
      const focusedSkill = detailScroll.contains(focused) ? SKILL_CONTROLS.find(key => key in focused.dataset) : null, focusedValue = focusedSkill ? focused.dataset[focusedSkill] : null;
      const focusedSection = detailScroll.contains(focused) ? headingOf(focused.closest('.ag-detail-section')) : '';
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
          const named = pathNameSection(flow);
          if (named) detailScroll.append(named);
          const item = el('section', 'ag-detail-section ag-route-detail'), box = el('textarea', 'ag-route-text');
          box.value = flow.text; box.dataset.routeText = flow.id; box.dataset.saved = flow.text; if (flow.rev) box.dataset.rev = text(flow.rev);
          box.rows = 6; box.spellcheck = false; box.setAttribute('aria-label', 'Instruction');
          item.append(el('h3', '', 'Instruction'), box);
          if (flowAction) {
            const actions = el('div', 'ag-route-actions'), state = el('p', 'ag-route-state', 'Saved'), remove = el('button', 'ag-text-button ag-route-remove', 'Remove');
            state.dataset.routeState = 'saved'; state.setAttribute('aria-live', 'polite');
            remove.type = 'button'; remove.dataset.routeRemove = flow.id; remove.setAttribute('aria-label', 'Remove this flow');
            actions.append(state, remove); item.append(actions);
          } else box.readOnly = true;
          detailScroll.append(item);
          [pathRunSection(flow), costSection(flow), commandSection(flow)].forEach(part => { if (part) detailScroll.append(part); });
        }
      } else if (selected.type === 'new-agent') renderNewAgent();
      else if (selected.type === 'new-skill') renderNewSkill(selected.key);
      else if (selected.type === 'skill') renderSkill(selected.key);
      else if (selected.type === 'work-loop') {
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
        // The red dot on its circle (and the "! N" above the graph): what it means for this agent.
        const trouble = attentionAgents().get(agent.name);
        if (trouble) { const line = el('p', 'ag-agent-note ag-attention-note', 'Needs attention: ' + trouble + '. This is the red dot on its circle, counted in the “! N” above the graph.'); line.dataset.kind = 'error'; detailScroll.append(line); }
        agentActions(agent);
        skillSection(agent);
        teamSection(agent);
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
      if (focusedSkill && !detailScroll.contains(document.activeElement)) Array.from(detailScroll.querySelectorAll('button, textarea')).find(item => item.dataset[focusedSkill] === focusedValue)?.focus({ preventScroll: true });
      // Its control gone (a skill deleted, a row taken away): the first control of the same section.
      if (focusedSection && !detailScroll.contains(document.activeElement)) Array.from(detailScroll.querySelectorAll('.ag-detail-section')).find(item => headingOf(item) === focusedSection)?.querySelector('button:not([disabled]), summary')?.focus({ preventScroll: true });
      keepFocus();
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
      nodes.forEach((node, key) => { node.dataset.armed = String(!trailPending && trail.at(-1) === key); node.dataset.trail = String(trail.includes(key)); node.dataset.pendingEnd = String(trailPending && trail.at(-1) === key); });
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
      trail = [step]; flowErrors.delete(step); setTitle(step); if (agentNotes.get(step)?.[0]?.kind === 'error') agentNotes.delete(step); trailChanged();
    }
    function dropTrail() { if (trailPending) return; trail = []; trailChanged(); }
    function backStep() { if (trailPending) return; trail.pop(); trailChanged(); }
    function addStep(name) {
      const step = standIn(name);
      if (!step || trailPending || !trail.length) return;
      // A second click on a lone armed agent cancels it; a double-click there then makes its loop on itself.
      if (step === trail.at(-1)) { loneCancel = trail.length === 1 ? step : ''; finishTrail(); return; }
      // The hub can only end a path: once it is the newest step, only finishing it, Backspace or Escape is left.
      if (trail.length > 1 && isHubAgent(trail.at(-1))) { refuseFlow(step, trail.at(-1) + ' ends this flow: press Enter or click it again to add the flow', false); return; }
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
      const member = element.closest('[data-team-line]');
      if (member) return { type: 'team-line', key: member.dataset.teamLine, origin: member };
      const work = element.closest('[data-work-loop-id]');
      if (work) return { type: 'work-loop', key: work.dataset.workLoopId, origin: work };
      const loop = element.closest('[data-loop-id]');
      if (loop) return { type: 'loop', key: loop.dataset.loopId, origin: loop };
      return null;
    }
    // The line under the pointer. Every flow, loop and group line has a band LINE_BAND screen px wide that takes the
    // click, the right-click and the hover; where bands overlap, the line nearest the pointer wins. Returns the
    // line's group (a flow, a group line, a review or work loop) or null.
    const LINE_OWNER = '[data-route], [data-team-line], [data-loop-id], [data-work-loop-id]';
    function lineAt(clientX, clientY, fallback) {
      const owners = new Map();
      (document.elementsFromPoint?.(clientX, clientY) || []).forEach(element => {
        if (!element.matches?.('.ag-route-hit, .ag-team-hit, .ag-line-hit') || !viewport.contains(element)) return;
        const owner = element.closest(LINE_OWNER);
        if (owner) owners.set(element, owner);
      });
      if (!owners.size) { const owner = fallback?.closest?.(LINE_OWNER); return owner && !fallback.closest('[data-agent]') ? owner : null; }
      let best = null, least = Infinity;
      owners.forEach((owner, element) => {
        const distance = distanceTo(element, clientX, clientY);
        if (distance < least) { least = distance; best = owner; }
      });
      return best;
    }
    function distanceTo(element, clientX, clientY) {
      const matrix = element.getScreenCTM();
      if (!matrix) return Infinity;
      let least = Infinity;
      if (element.tagName.toLowerCase() === 'line') {
        const a = new DOMPoint(+element.getAttribute('x1'), +element.getAttribute('y1')).matrixTransform(matrix), b = new DOMPoint(+element.getAttribute('x2'), +element.getAttribute('y2')).matrixTransform(matrix);
        const dx = b.x - a.x, dy = b.y - a.y, t = clamp(((clientX - a.x) * dx + (clientY - a.y) * dy) / (dx * dx + dy * dy || 1), 0, 1);
        return Math.hypot(a.x + dx * t - clientX, a.y + dy * t - clientY);
      }
      const length = element.getTotalLength?.() || 0, count = Math.max(8, Math.min(120, Math.ceil(length * (matrix.a || 1) / 3)));
      for (let i = 0; i <= count; i++) { const q = element.getPointAtLength(length * i / count).matrixTransform(matrix); least = Math.min(least, Math.hypot(q.x - clientX, q.y - clientY)); }
      return least;
    }
    // Hovering any part of a line marks the whole of it (a flow's every step) and fades the others; only while the
    // pointer is there.
    let hoverLine = null;
    function hoverOn(owner) {
      if (owner === hoverLine) return;
      if (hoverLine) delete hoverLine.dataset.hover;
      hoverLine = owner && owner.isConnected ? owner : null;
      if (hoverLine) hoverLine.dataset.hover = 'true';
      stage.dataset.hovering = String(!!hoverLine);
    }
    // Opening details touches nothing else: a pending path stays pending.
    function openInfo(target) {
      if (target.type === 'agent') activateAgent(target.key, target.origin);
      else if (target.type === 'team-line') { const view = teamLines.get(target.key); if (view) select('agent', view.team.lead, target.origin); }
      else select(target.type, target.key, target.origin);
    }
    function pickFlow(id) { if (pickedFlow === id && !pickedMember) return; pickedFlow = id; pickedMember = ''; highlight(); }
    function pickMember(key) { if (pickedMember === key && !pickedFlow) return; pickedMember = key; pickedFlow = ''; highlight(); }
    // What Delete is about, one target per press: the focused node, flow, group line or loop; else the picked flow
    // or group line; else the lone armed agent (a path of exactly one step); else what the open details show (a flow
    // or an agent). Backspace asks the same question but never deletes an agent (see the keydown handler).
    function deleteTarget(element) {
      const focused = infoTarget(element);
      if (focused) return focused;
      if (pickedFlow && routeById(pickedFlow)) return { type: 'flow', key: pickedFlow };
      if (pickedMember && teamLines.has(pickedMember)) return { type: 'team-line', key: pickedMember };
      if (trail.length === 1 && !trailPending) return { type: 'agent', key: trail[0] };
      if (selected?.type === 'flow' && routeById(selected.key)) return { type: 'flow', key: selected.key };
      if (selected?.type === 'agent' && shownAgents.has(selected.key)) return { type: 'agent', key: selected.key };
      return null;
    }
    // How each kind of target is deleted, or null when it cannot be: a drawn flow or loop on itself (actions.flow),
    // an agent the roster lets go (actions.roster; never the orchestrator, the council or a seat), a member's line
    // in a group (actions.team). The hub's and council's lines and everything from a live task graph are not the
    // user's data.
    function deleter(target) {
      switch (target?.type) {
        case 'flow': return flowAction && routeById(target.key) ? () => removeFlow(target.key) : null;
        case 'agent': return removableOf(target.key).ok ? () => removeAgent(target.key) : null;
        case 'team-line': return teamAction && teamLines.has(target.key) ? () => removeMember(target.key) : null;
        default: return null; // a review or work loop, the learning ring
      }
    }
    function infoKey(event) { return (event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey) || (event.key.toLowerCase() === 'i' && !event.shiftKey)) && !event.ctrlKey && !event.metaKey && !event.altKey; }
    function message(error, fallback) { if (isStale(error)) return STALE_SAID; return plainWords(text(error && typeof error === 'object' && 'message' in error ? error.message : error).replace(/\s+/g, ' ').trim()) || fallback; }
    // Back-end reasons that speak of its files and fields, said in the user's terms (anything else is shown as it is).
    const PLAIN = [
      [/^unknown agent ([^:\s]+): there is no \S+\.md in the structure folder\.?$/i, (all, name) => name + ' is one of OpenCode\'s own agents, not one of this toolkit\'s, so it cannot be on a path or in a group.'],
      [/^description is required \(or start from a template or a copy of an agent\)\.?$/i, () => 'A blank agent needs a description here: start from the verifier or from a copy of an agent instead.'],
      [/^'?([a-z][a-z0-9_-]*)'? (?:is already an agent file in the agents folder|already has a file in the structure folder)\b.*$/i, (all, name) => 'There is already an agent called ' + name + '.'],
      [/^model is required \(expected provider\/model\)\.?$/i, () => 'Choose a model for the new agent.'],
    ];
    function plainWords(said) { const found = PLAIN.find(([pattern]) => pattern.test(said)); return found ? said.replace(found[0], found[1]) : said; }
    // A refused change: the agent shakes once (its red ring goes with the shake), and the reason is a line in its
    // details (opened with `show`), announced in the live region and kept as the agent's tooltip.
    function refuseFlow(name, error, show = true) {
      if (!name) return;
      const said = message(error, 'The flow could not be changed');
      flowErrors.set(name, said); setTitle(name);
      const node = nodes.get(name);
      if (node) { node.dataset.shake = 'true'; later(() => { delete node.dataset.shake; }, 400); }
      announceLine(said);
      if (!show || !agentsByName.has(name)) return;
      setNotes(name, 'error', said);
      if (selected?.type === 'agent' && selected.key === name) forceDetail(); else select('agent', name, node);
    }
    // Ask the host for a change; `before` runs on its answer, before the next snapshot is drawn.
    function askFlow(request, before) {
      busyFlows++;
      return call(flowAction, request).then(async result => {
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
      }).then(reply => {
        if (!alive) return;
        // The pending path gives way to the flow in the same frame the flow is drawn: no gap between them.
        trailPending = false; trail = []; trailChanged();
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
        // The first agent keeps the reason as its tooltip too (the newest shakes and shows it in its details).
        if (path[0] && path[0] !== path.at(-1) && agentsByName.has(path[0])) { flowErrors.set(path[0], message(error, 'The flow could not be changed')); setTitle(path[0]); }
        refuseFlow(path.at(-1), error);
      });
    }
    function setRouteState(kind, said) {
      const state = detailScroll.querySelector('[data-route-state]');
      if (!state) return;
      state.dataset.routeState = kind;
      // Typing saves by itself after a short pause, so the box never asks the user to do anything.
      state.textContent = said || { saved: 'Saved', unsaved: 'Saving', saving: 'Saving' }[kind];
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
      askFlow(withRev({ op: 'text', id, text: value }, box.dataset.rev || revOf('flow', id)), reply => {
        // A blank text comes back as the one made from the path.
        box.dataset.saved = typeof reply.flow?.text === 'string' ? reply.flow.text : value;
        if (box.value === value) box.value = box.dataset.saved;
        box.dataset.rev = typeof reply.flow?.rev === 'string' ? reply.flow.rev : '';
      }).then(() => {
        box.dataset.saving = 'false';
        // The next save expects what this one wrote (the reply's rev, else the snapshot that followed it).
        const rev = revOf('flow', id); if (rev && !box.dataset.rev) box.dataset.rev = rev;
        remember({ op: 'text', id, before, after: box.dataset.saved });
        setRouteState(box.value === box.dataset.saved ? 'saved' : 'unsaved');
        next();
      }, error => {
        box.dataset.saving = 'false';
        if (isStale(error)) keepTyped(box, '[data-route-text="' + CSS.escape(id) + '"]', value, revOf('flow', id));
        setRouteState('error', message(error, 'Not saved'));
        next();
      });
    }
    // ---- A path's name, run, cost and command (contract n96 r1; the user's word for a flow is path) ----
    function chars(value) { return [...value].length; }
    function pathLine(cls, said, kind) {
      const line = el('p', cls, said);
      if (kind) line.dataset.kind = kind;
      if (kind === 'error') { line.classList.add('ag-form-error'); line.setAttribute('role', 'alert'); }
      return line;
    }
    // Name: a field saved with Enter or on leaving it ({ op: "name" }; empty clears it), undoable.
    function pathNameSection(flow) {
      const name = text(flow.name || '');
      if (!flowAction && !name) return null;
      const item = el('section', 'ag-detail-section ag-path-name'), field = el('input', 'ag-input');
      field.type = 'text'; field.value = name; field.dataset.routeName = flow.id; field.dataset.saved = name; if (flow.rev) field.dataset.rev = text(flow.rev);
      field.spellcheck = false; field.autocomplete = 'off'; field.setAttribute('aria-label', 'Name of this path');
      if (!flowAction) field.readOnly = true; else field.setAttribute('aria-keyshortcuts', 'Enter Escape');
      item.append(el('h3', '', 'Name'), field);
      const error = pathErrors.get('name:' + flow.id);
      if (error) item.append(pathLine('ag-path-name-error', error, 'error'));
      return item;
    }
    function saveName(field) {
      if (!flowAction || field.readOnly || field.dataset.saving === 'true') return;
      const id = field.dataset.routeName, before = field.dataset.saved, value = field.value.trim();
      if (value === before) { field.value = before; return; }
      field.dataset.saving = 'true';
      askFlow(withRev({ op: 'name', id, name: value }, field.dataset.rev || revOf('flow', id)), reply => {
        field.dataset.saved = typeof reply.flow?.name === 'string' ? reply.flow.name : value;
      }).then(() => {
        field.dataset.saving = 'false';
        pathErrors.delete('name:' + id);
        remember({ op: 'name', id, before, after: field.dataset.saved });
        announceLine(field.dataset.saved ? 'Named ' + field.dataset.saved + '.' : 'Name cleared.');
        redrawPath(id, '[data-route-name]');
      }, error => {
        field.dataset.saving = 'false';
        // Refused like any other change: the reason as the field's error line and in the live region; the typed name stays.
        const said = message(error, 'The name was not changed');
        pathErrors.set('name:' + id, said); announceLine(said);
        const shown = isStale(error) ? keepTyped(field, '[data-route-name="' + CSS.escape(id) + '"]', value, revOf('flow', id)) || field : field;
        const item = shown.closest('.ag-path-name');
        item?.querySelector('.ag-path-name-error')?.remove();
        item?.append(pathLine('ag-path-name-error', said, 'error'));
      });
    }
    // Rebuild the open path's details, keeping focus on the control that had it.
    function redrawPath(id, keep, held) {
      if (!alive || selected?.type !== 'flow' || selected.key !== id) return;
      const had = held || (keep && detailScroll.contains(document.activeElement) && document.activeElement.matches(keep));
      forceDetail();
      if (had) detailScroll.querySelector(keep)?.focus({ preventScroll: true });
    }
    // Run: a goal (required, at most 4,000 characters) and Run (actions.run) or Copy run message ({ op: "prompt" }).
    function canRun() { return !!(runAction || flowAction); }
    function goalCount(value) {
      const length = chars(value);
      if (length > RUN_GOAL_CHARS) return { kind: 'error', said: 'Too long: ' + length.toLocaleString('en-US') + ' of ' + RUN_GOAL_CHARS.toLocaleString('en-US') + ' characters' };
      if (length >= RUN_GOAL_WARN) return { kind: 'near', said: length.toLocaleString('en-US') + ' of ' + RUN_GOAL_CHARS.toLocaleString('en-US') };
      return null;
    }
    function pathRunSection(flow) {
      if (!canRun()) return null;
      const state = runStates.get(flow.id) || {}, item = el('section', 'ag-detail-section ag-path-run');
      item.dataset.runFor = flow.id;
      const goal = el('textarea', 'ag-route-text ag-run-goal');
      goal.rows = 3; goal.spellcheck = false; goal.value = goals.get(flow.id) || ''; goal.dataset.runGoal = flow.id;
      goal.setAttribute('aria-label', 'Goal for this run'); goal.setAttribute('aria-required', 'true');
      goal.setAttribute('aria-keyshortcuts', 'Control+Enter Meta+Enter');
      item.append(el('h3', '', 'Run'), goal);
      const row = el('div', 'ag-route-actions'), count = el('p', 'ag-run-count'), go = el('button', 'ag-text-button ag-run-go', runAction ? (state.busy ? 'Sending…' : 'Run') : (state.busy ? 'Copying…' : 'Copy run message'));
      count.dataset.runCount = flow.id; count.setAttribute('aria-live', 'polite');
      const counted = goalCount(goal.value);
      if (counted) { count.textContent = counted.said; count.dataset.kind = counted.kind; }
      go.type = 'button'; go.dataset.runGo = flow.id; go.setAttribute('aria-keyshortcuts', 'Control+Enter Meta+Enter');
      go.setAttribute('aria-label', runAction ? 'Run this path with the goal' : 'Copy the run message for this path with the goal');
      if (state.busy) { go.disabled = true; go.setAttribute('aria-busy', 'true'); }
      row.append(count, go); item.append(row);
      if (state.note) item.append(pathLine('ag-run-note', state.note.text, state.note.kind));
      if (state.copy) {
        const copy = el('textarea', 'ag-route-text ag-run-copy');
        copy.readOnly = true; copy.value = state.copy; copy.rows = 8; copy.dataset.runCopy = flow.id; copy.setAttribute('aria-label', 'Run message to copy');
        item.append(copy);
      }
      return item;
    }
    // Redraw the run part in place (the rest of the details, and a goal being typed, stay as they are).
    function setRun(id, state, focus) {
      runStates.set(id, state);
      if (selected?.type !== 'flow' || selected.key !== id) return;
      const old = detailScroll.querySelector('.ag-path-run'), flow = routeById(id);
      if (!old || !flow) return;
      const typing = document.activeElement?.matches?.('[data-run-goal]') && old.contains(document.activeElement);
      const at = typing ? [document.activeElement.selectionStart, document.activeElement.selectionEnd] : null;
      const fresh = pathRunSection(flow);
      old.replaceWith(fresh);
      if (typing) { const goal = fresh.querySelector('[data-run-goal]'); goal.focus({ preventScroll: true }); goal.setSelectionRange(at[0], at[1]); }
      if (focus) { const target = fresh.querySelector(focus); if (target) { target.focus({ preventScroll: true }); if (target.select) target.select(); } }
      else if (!typing && !detailScroll.contains(document.activeElement)) fresh.querySelector('[data-run-go]:not([disabled])')?.focus({ preventScroll: true });
      fresh.querySelector('.ag-run-note[data-kind="error"]')?.scrollIntoView?.({ block: 'nearest' });
    }
    function runPath(id) {
      const flow = routeById(id), state = runStates.get(id) || {};
      if (!flow || state.busy || !canRun()) return;
      const box = [...detailScroll.querySelectorAll('[data-run-goal]')].find(item => item.dataset.runGoal === id);
      const goal = box ? box.value : goals.get(id) || '';
      goals.set(id, goal);
      if (!goal.trim()) { setRun(id, { note: { kind: 'error', text: 'A goal is required.' } }, '[data-run-goal]'); announceLine('A goal is required.'); return; }
      const counted = goalCount(goal);
      if (counted?.kind === 'error') { setRun(id, { note: { kind: 'error', text: counted.said } }); announceLine(counted.said); return; }
      setRun(id, { busy: true });
      const fail = (error, fallback) => {
        if (!alive) return;
        const said = message(error, fallback);
        setRun(id, { note: { kind: 'error', text: said } }); announceLine(said);
      };
      if (runAction) {
        let answer;
        answer = call(runAction, { flow: id, goal });
        answer.then(result => {
          if (!alive) return;
          const reply = result && typeof result === 'object' ? result : {};
          if (reply.sent === false) { fail(reply.message, 'Not sent'); return; }
          // Sent: the goal has gone; the host's own words win.
          goals.delete(id);
          const said = text(reply.message || '') || 'Sent to the orchestrator' + (text(reply.session || '') ? ' (session ' + text(reply.session) + ').' : '.');
          setRun(id, { note: { kind: 'done', text: said } }); announceLine(said);
          const box = [...detailScroll.querySelectorAll('[data-run-goal]')].find(item => item.dataset.runGoal === id);
          if (box) box.value = '';
        }, error => fail(error, 'Not sent'));
        return;
      }
      let answer;
      answer = call(flowAction, { op: 'prompt', id, goal });
      answer.then(result => {
        if (!alive) return;
        const said = typeof result?.text === 'string' ? result.text : '';
        if (!said) { fail('', 'No run message came back'); return; }
        const shown = () => { setRun(id, { note: { kind: 'notice', text: 'The clipboard was refused: the run message is below, selected. Copy it and paste it into the orchestrator\'s chat.' }, copy: said }, '[data-run-copy]'); announceLine('The clipboard was refused: the run message is shown, selected.'); };
        let copying;
        try { copying = navigator.clipboard && navigator.clipboard.writeText ? Promise.resolve(navigator.clipboard.writeText(said)) : Promise.reject(new Error('no clipboard')); } catch (error) { copying = Promise.reject(error); }
        copying.then(() => {
          if (!alive) return;
          const done = 'Copied the run message: paste it into the orchestrator\'s chat.';
          setRun(id, { note: { kind: 'done', text: done } }); announceLine(done);
        }, () => { if (alive) shown(); });
      }, error => fail(error, 'The run message could not be made'));
    }
    // Open a path's details at its goal box (the key G on a focused or picked path).
    function openRun(id) {
      const flow = routeById(id);
      if (!flow || !canRun()) return;
      select('flow', id, routeViews.get(id)?.group);
      const goal = [...detailScroll.querySelectorAll('[data-run-goal]')].find(item => item.dataset.runGoal === id);
      if (goal) { goal.focus({ preventScroll: true }); goal.scrollIntoView?.({ block: 'nearest' }); }
    }
    // Cost: the path's stats as one block; null stats "Not run yet", a null field a dash (never a zero).
    function costSection(flow) {
      if (!('stats' in flow)) return null;
      const item = el('section', 'ag-detail-section ag-path-cost'), stats = flow.stats && typeof flow.stats === 'object' ? flow.stats : null;
      item.append(el('h3', '', 'Cost'));
      if (!stats) { item.append(el('p', 'ag-path-none', 'Not run yet')); return item; }
      const known = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
      const count = value => known(value) ? String(Math.round(Number(value))) : '—';
      const round = value => known(value) ? String(Math.round(Number(value) * 10) / 10) : '—';
      const money = value => known(value) ? cost(value) : '—';
      const when = value => {
        if (value === null || value === undefined || value === '') return '—';
        let seconds = Number(value);
        if (!Number.isFinite(seconds)) { const parsed = Date.parse(String(value)); if (!Number.isFinite(parsed)) return '—'; seconds = parsed / 1000; } else if (seconds > 1e12) seconds /= 1000;
        const said = ago(seconds);
        return said === 'now' ? 'just now' : said + ' ago';
      };
      cells(item, [['Runs', count(stats.runs)], ['Passed', count(stats.passed)],
        ['Rounds', round(stats.rounds?.avg) + ' avg · ' + count(stats.rounds?.max) + ' most'],
        ['Cost', money(stats.cost?.total) + ' total · ' + money(stats.cost?.avg) + ' avg'], ['Last run', when(stats.last)]]);
      return item;
    }
    // Command: a switch "Use as a command" that needs a name; on, it shows /NAME and when it applies, as reported.
    // The back end reports it as `live` (a new command works without a restart) and `applies` (its sentence on it):
    // in the command reply, else structure.commands_live / commands_applies in the snapshot. What it says is put in
    // plain words: a command written now is not in OpenCode until OpenCode reloads (/reload, when the sentence names
    // it) or restarts; a sentence that says neither is shown as sent.
    function noteApplies(id, reply, on) {
      if (reply && (typeof reply.live === 'boolean' || typeof reply.applies === 'string')) commandApplies.set(id, { live: reply.live, applies: reply.applies, on });
    }
    function appliesLines(flow, on = true) {
      const reported = commandApplies.get(flow.id), structure = snapshot?.structure || {}, fresh = !!reported && reported.on === on;
      const live = fresh && typeof reported.live === 'boolean' ? reported.live : on && typeof structure.commands_live === 'boolean' ? structure.commands_live : null;
      const said = text(fresh && typeof reported.applies === 'string' ? reported.applies : on && typeof structure.commands_applies === 'string' ? structure.commands_applies : '');
      const name = '/' + text(flow.command || flow.name || ''), reload = /\/reload\b/.test(said) ? ' (type /reload)' : '';
      if (live === true) return on ? ['Live now.'] : [];
      if (live === false || (live === null && /reload|restart/i.test(said))) {
        if (said && !/reload|restart/i.test(said)) return [plainWords(said)];
        if (!on) return [name + ' stays in OpenCode until OpenCode reloads' + reload + ' or restarts.'];
        return [fresh ? 'Not in OpenCode yet: ' + name + ' works once OpenCode reloads' + reload + ' or restarts.' : 'A new command works once OpenCode reloads' + reload + ' or restarts.'];
      }
      return said ? [plainWords(said)] : [];
    }
    function appliesLine(flow, on = true) { return appliesLines(flow, on)[0] || ''; }
    function commandSection(flow) {
      const name = text(flow.name || ''), command = text(flow.command || '');
      if (!flowAction && !command) return null;
      const item = el('section', 'ag-detail-section ag-path-command'), toggle = el('button', 'ag-switch', 'Use as a command');
      toggle.type = 'button'; toggle.setAttribute('role', 'switch'); toggle.setAttribute('aria-checked', String(!!command)); toggle.dataset.routeCommand = flow.id;
      item.append(el('h3', '', 'Command'), toggle);
      if (!flowAction) toggle.disabled = true;
      else if (!name && !command) {
        // Not available without a name: the switch says why (and stays reachable, so the reason is read out).
        const reason = el('p', 'ag-form-reason ag-command-reason', 'Give this path a name to use it as a command.');
        reason.id = uid + '-command-reason'; toggle.setAttribute('aria-disabled', 'true'); toggle.setAttribute('aria-describedby', reason.id);
        item.append(reason);
      }
      if (command) {
        item.append(el('p', 'ag-command-name', '/' + command));
        const first = appliesLine(flow);
        if (first) item.append(el('p', 'ag-command-applies', first));
      } else {
        // Just turned off: OpenCode keeps the command until it reloads, as the back end said.
        const gone = commandApplies.get(flow.id)?.on === false ? appliesLine(flow, false) : '';
        if (gone) item.append(el('p', 'ag-command-applies', gone));
      }
      const error = pathErrors.get('command:' + flow.id);
      if (error) item.append(pathLine('ag-command-error', error, 'error'));
      return item;
    }
    function setCommand(id, on, undoing) {
      const flow = routeById(id);
      if (!flowAction || !flow || (on && !text(flow.name || ''))) return Promise.resolve();
      // The switch keeps focus across the redraw (the refresh in between rebuilds the details).
      const held = detailScroll.contains(document.activeElement) && document.activeElement.matches('[data-route-command]');
      return askFlow({ op: 'command', id, on }, reply => {
        noteApplies(id, reply, on);
      }).then(() => {
        pathErrors.delete('command:' + id);
        if (!undoing) remember({ op: 'command', id, before: !on });
        const now = routeById(id);
        announceLine(on ? '/' + text(now?.command || now?.name || flow.name) + ' is a command. ' + appliesLine(now || flow) : ('No longer a command. ' + appliesLine({ ...flow, ...(now || {}), command: flow.command || flow.name }, false)).trim());
        redrawPath(id, '[data-route-command]', held);
      }, error => {
        const said = message(error, 'The command was not changed');
        pathErrors.set('command:' + id, said); announceLine(said);
        redrawPath(id, '[data-route-command]', held);
        if (undoing) throw error;
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
        remember({ op: 'remove', id, path: flow.path, text: undoText, name: text(flow.name || ''), command: !!flow.command });
        if (selected?.type === 'flow' && selected.key === id) closeDetails();
      }, error => {
        if (!alive) return;
        // The flow stays; its details say why, as a line.
        refuseFlow(flow.path[0], error, false);
        if (routeById(id) && !(selected?.type === 'flow' && selected.key === id)) select('flow', id, routeViews.get(id)?.group);
        setRouteState('error', message(error, 'Not removed'));
      });
    }
    function undoFlow() {
      const last = flowHistory.pop();
      if (!last) return;
      if (last.op === 'skill') { undoSkill(last); return; }
      if (last.op === 'move') { applyMoves(last.layout, last.before, false); return; }
      if (undoOther(last)) return;
      if (last.op === 'name' || last.op === 'command') {
        const request = last.op === 'name' ? { op: 'name', id: last.id, name: last.before } : { op: 'command', id: last.id, on: last.before };
        askFlow(request, reply => { if (last.op === 'command') noteApplies(last.id, reply); })
          .then(() => { pathErrors.delete(last.op + ':' + last.id); redrawPath(last.id); }, error => {
            if (!alive) return;
            flowHistory.push(last);
            const said = message(error, 'Not undone');
            pathErrors.set(last.op + ':' + last.id, said); announceLine(said); redrawPath(last.id);
          });
        return;
      }
      const request = last.op === 'add' ? { op: 'remove', id: last.id } : last.op === 'remove' ? { op: 'add', path: last.path, text: last.text } : { op: 'text', id: last.id, text: last.before };
      askFlow(request, reply => {
        // A flow put back gets a new file, so later steps that name it follow it there.
        if (last.op === 'remove' && reply.flow?.id) flowHistory.forEach(entry => { if (entry.id === last.id) entry.id = reply.flow.id; });
        if (last.op !== 'add' && selected?.type === 'flow' && selected.key === last.id && reply.flow?.id) selected.key = reply.flow.id;
      }).then(async reply => {
        if (alive && last.op === 'add' && selected?.type === 'flow' && selected.key === last.id) closeDetails();
        // A path put back gets its name and its command again (add carries neither).
        const id = reply?.flow?.id;
        if (alive && last.op === 'remove' && id && last.name) {
          try {
            await askFlow({ op: 'name', id, name: last.name });
            if (last.command) await askFlow({ op: 'command', id, on: true }, answer => noteApplies(id, answer));
          } catch (error) {
            if (!alive) return;
            const said = message(error, 'The name or the command did not come back');
            pathErrors.set('name:' + id, said); announceLine(said);
          }
          redrawPath(id);
        }
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

    listen(plus, 'click', event => { if (addMenu.hidden) openAddMenu(event.detail === 0); else closeAddMenu(false); });
    // A press on a starting point takes the new circle at once: drag it out and release to drop it, or click and
    // then click where it goes.
    listen(addMenu, 'pointerdown', event => {
      const item = event.target.closest('[data-add-kind]');
      if (!item || event.button !== 0) return;
      event.preventDefault();
      startDraft(item.dataset.addKind, item.dataset.addSource, event);
    });
    listen(addMenu, 'click', event => {
      const item = event.target.closest('[data-add-kind]');
      if (item && event.detail === 0) startDraft(item.dataset.addKind, item.dataset.addSource, null);
    });
    listen(addMenu, 'keydown', event => {
      const items = [...addMenu.querySelectorAll('[role="menuitem"]')], index = items.indexOf(document.activeElement);
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); items[(index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus({ preventScroll: true }); }
      else if (event.key === 'Home' || event.key === 'End') { event.preventDefault(); items[event.key === 'Home' ? 0 : items.length - 1]?.focus({ preventScroll: true }); }
      else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeAddMenu(true); }
      else if (event.key === 'Tab') closeAddMenu(false);
    });
    listen(document, 'pointerdown', event => { if (!addMenu.hidden && !addMenu.contains(event.target) && !plus.contains(event.target)) closeAddMenu(false); }, true);
    listen(document, 'pointermove', followDraft);
    listen(document, 'pointerup', event => {
      if (!draft?.held) return;
      const moved = Math.hypot(event.clientX - draft.startX, event.clientY - draft.startY) > MOVE_PX, rect = viewport.getBoundingClientRect();
      const inside = event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom;
      if (moved && inside) { followDraft(event); placeDraft(); } else draft.held = false;
    });
    // While the new circle follows the pointer, a press on the canvas drops it there and does nothing else.
    listen(viewport, 'pointerdown', event => {
      if (!draft?.following) return;
      event.preventDefault(); event.stopImmediatePropagation(); swallowClick = true;
      followDraft(event); placeDraft();
    }, true);
    listen(document, 'pointerdown', event => { lastInside = root.contains(event.target); }, true);
    listen(document, 'focusin', event => { lastInside = root.contains(event.target); }, true);
    listen(document, 'keydown', event => {
      if (!alive || !lastInside || root.contains(event.target)) return;
      const page = event.target === document.body || event.target === document.documentElement || event.target === document;
      if (!page || event.defaultPrevented) return;
      if ((event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && event.key.toLowerCase() === 'z' && flowHistory.length) { event.preventDefault(); undoFlow(); viewport.focus({ preventScroll: true }); }
      else if (event.key === 'Escape' && draft) { event.preventDefault(); dropDraft(); }
    });
    listen(close, 'click', closeDetails);
    // The sheet's handle: one gesture, one meaning. A drag on it only resizes the sheet (never pans, picks, moves or
    // closes); Arrow keys resize it a step, Page keys a larger one, Home and End go to the smallest and largest.
    listen(sheetHandle, 'pointerdown', event => {
      event.stopPropagation();
      if (event.button !== 0 || !root.classList.contains('ag-compact')) return;
      // Pressed, it takes the keys (preventing the default would otherwise keep focus where it was).
      event.preventDefault(); sheetHandle.focus({ preventScroll: true });
      try { sheetHandle.setPointerCapture(event.pointerId); } catch (_) { /* a synthetic pointer */ }
      sheetDrag = { id: event.pointerId, y: event.clientY, from: sheetHeight() };
    });
    listen(sheetHandle, 'pointermove', event => {
      event.stopPropagation();
      if (!sheetDrag || event.pointerId !== sheetDrag.id) return;
      event.preventDefault(); setSheet(sheetDrag.from + sheetDrag.y - event.clientY, false);
    });
    const endSheetDrag = event => {
      event.stopPropagation();
      if (!sheetDrag || event.pointerId !== sheetDrag.id) return;
      sheetDrag = null; setSheet(sheetHeight(), true);
    };
    listen(sheetHandle, 'pointerup', endSheetDrag); listen(sheetHandle, 'pointercancel', endSheetDrag);
    listen(sheetHandle, 'click', event => event.stopPropagation());
    listen(sheetHandle, 'dblclick', event => event.stopPropagation());
    listen(sheetHandle, 'keydown', event => {
      if (!root.classList.contains('ag-compact') || event.altKey || event.ctrlKey || event.metaKey) return;
      const { min, max } = sheetStops(), now = sheetHeight(), steps = { ArrowUp: 24, ArrowDown: -24, PageUp: 96, PageDown: -96 };
      const next = event.key in steps ? now + steps[event.key] : event.key === 'Home' ? min : event.key === 'End' ? max : null;
      if (next === null) return;
      event.preventDefault(); event.stopPropagation(); setSheet(next, true);
    });
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
      if (skillButton) {
        if (skillButton.disabled) return;
        const request = JSON.parse(skillButton.dataset.skillRequest), owner = agentsByName.get(request.agent);
        if (owner && request.on === false && request.skill && !skillPending.has(owner.name) && skillsOf(owner).includes(request.skill)) ghostSkill(owner.name, request.skill, skillsOf(owner).indexOf(request.skill));
        askSkill(request).catch(() => { if (alive) clearGhosts(request.agent); });
        return;
      }
      const removeRoute = event.target.closest('[data-route-remove]');
      if (removeRoute) { removeFlow(removeRoute.dataset.routeRemove); return; }
      const runGo = event.target.closest('[data-run-go]');
      if (runGo) { if (!runGo.disabled) runPath(runGo.dataset.runGo); return; }
      const commandSwitch = event.target.closest('[data-route-command]');
      if (commandSwitch) { if (!commandSwitch.disabled && commandSwitch.getAttribute('aria-disabled') !== 'true') setCommand(commandSwitch.dataset.routeCommand, commandSwitch.getAttribute('aria-checked') !== 'true'); return; }
      const control = event.target.closest('button');
      if (control && !control.disabled) {
        const data = control.dataset, current = selected?.type === 'agent' ? selected.key : '';
        if ('draftCancel' in data) { dropDraft(); plus.focus({ preventScroll: true }); return; }
        if ('draftRetry' in data) {
          // Try again puts the circle back where it was and asks again.
          if (!draft && failedDraft) { draft = failedDraft; failedDraft = null; }
          if (draft && !draft.pending) { draft.pending = true; draft.error = ''; drawDraft(); createDraft(); }
          return;
        }
        if ('agentRemove' in data) { removeAgent(data.agentRemove); return; }
        if ('skillOpen' in data) { openSkill(data.skillOpen, current, control); return; }
        if ('skillNew' in data) { select('new-skill', data.skillNew, control); return; }
        if ('skillSave' in data) { saveSkill(); return; }
        if ('skillCancel' in data) { if (skillView && !formBusy) { Object.assign(skillView, { draft: null, error: '', note: '' }); forceDetail(); } return; }
        if ('skillDelete' in data) { deleteSkill(); return; }
        if ('teamStart' in data) { teamDraft = { lead: data.teamStart, members: [] }; forceDetail(); detailScroll.querySelector('[data-team-pick]')?.focus({ preventScroll: true }); return; }
        if ('teamCancel' in data) { teamDraft = null; forceDetail(); return; }
        if ('teamCreate' in data) { const members = [...detailScroll.querySelectorAll('[data-team-pick]:checked')].map(box => box.value); if (members.length) createTeam(data.teamCreate, members); return; }
        const team = teamLedBy(current);
        if ('teamDrop' in data && team) { setMembers(team, team.members.filter(name => name !== data.teamDrop)); return; }
        if ('teamAddButton' in data && team) { const choice = detailScroll.querySelector('[data-team-add]'); if (choice?.value) setMembers(team, [...team.members, choice.value]); return; }
        if ('teamRemove' in data && team) { removeTeam(team); return; }
      }
      const link = event.target.closest('[data-flow-peer]');
      if (link) { select('agent', link.dataset.flowPeer, nodes.get(link.dataset.flowPeer)); detailScroll.scrollTop = 0; }
    });
    // The instruction is saved when focus leaves it or on Ctrl/Cmd+Enter; its state shows beside it.
    listen(detailScroll, 'submit', event => {
      event.preventDefault();
      const form = event.target.closest('[data-form]');
      if (form?.dataset.form.startsWith('new-skill:')) submitSkill(form, form.dataset.form.slice(10));
    });
    listen(detailScroll, 'change', event => {
      const basis = event.target.closest('[data-new="basis"]');
      if (basis) { const model = basis.form?.querySelector('[data-new="model"]'); if (model) { model.closest('label').hidden = basis.value !== 'typed'; if (basis.value === 'typed') model.focus({ preventScroll: true }); } }
      const pick = event.target.closest('[data-team-pick]');
      if (pick && teamDraft) teamDraft.members = [...detailScroll.querySelectorAll('[data-team-pick]:checked')].map(box => box.value);
    });
    listen(detailScroll, 'input', event => {
      const skill = event.target.closest('[data-skill-text]');
      if (skill && skillView?.id === skill.dataset.skillText) { skillView.draft = skill.value; skillView.note = ''; const state = detailScroll.querySelector('[data-skill-state]'); if (state) state.textContent = skill.value === skillView.text ? 'Saved' : 'Changed'; }
      const team = event.target.closest('[data-team-text]');
      if (team && team.dataset.saving !== 'true') { const long = overLimit(team.value); setTeamState(long ? 'error' : team.value === team.dataset.saved ? 'saved' : 'unsaved', long); }
      if (team) savePaused(team, saveTeamText);
      const route = event.target.closest('[data-route-text]');
      if (route) savePaused(route, saveRouteText);
      const goal = event.target.closest('[data-run-goal]');
      if (goal) {
        goals.set(goal.dataset.runGoal, goal.value);
        const count = goal.closest('.ag-path-run')?.querySelector('[data-run-count]'), counted = goalCount(goal.value);
        if (count) { count.textContent = counted ? counted.said : ''; if (counted) count.dataset.kind = counted.kind; else delete count.dataset.kind; }
        const state = runStates.get(goal.dataset.runGoal);
        if (state?.note?.kind === 'error' && goal.value.trim() && !(counted?.kind === 'error')) { runStates.set(goal.dataset.runGoal, {}); goal.closest('.ag-path-run')?.querySelector('.ag-run-note')?.remove(); }
      }
    });
    // An instruction is saved after a short pause in typing (as well as on leaving the box, Escape and Ctrl/Cmd+Enter),
    // and whatever is still unsaved goes when the page is hidden or closed.
    const pauses = new WeakMap(), TYPE_PAUSE_MS = 700;
    function savePaused(box, save) {
      clearTimeout(pauses.get(box));
      const timer = later(() => { if (alive && box.isConnected && box.value !== box.dataset.saved) save(box); }, TYPE_PAUSE_MS);
      pauses.set(box, timer);
    }
    function flushTyping() {
      detailScroll.querySelectorAll('[data-route-text]').forEach(box => { if (box.value !== box.dataset.saved) saveRouteText(box); });
      detailScroll.querySelectorAll('[data-team-text]').forEach(box => { if (box.value !== box.dataset.saved) saveTeamText(box); });
    }
    listen(window, 'pagehide', flushTyping);
    listen(document, 'visibilitychange', () => { if (document.hidden) flushTyping(); });
    listen(detailScroll, 'input', event => { const box = event.target.closest('[data-route-text]'); if (box && box.dataset.saving !== 'true') { const long = overLimit(box.value); if (long) setRouteState('error', long); else setRouteState(box.value === box.dataset.saved ? 'saved' : 'unsaved'); } });
    listen(detailScroll, 'focusout', event => { const named = event.target.closest('[data-route-name]'); if (named) saveName(named); const box = event.target.closest('[data-route-text]'); if (box) saveRouteText(box); const team = event.target.closest('[data-team-text]'); if (team) saveTeamText(team); });
    listen(detailScroll, 'change', event => { const rename = event.target.closest?.('[data-rename]'); if (rename && rename.value.trim() && rename.value.trim() !== rename.dataset.rename) renameAgent(rename.dataset.rename, rename.value.trim(), rename); });
    listen(detailScroll, 'keydown', event => {
      const rename = event.target.closest('[data-rename]');
      if (rename) {
        const name = rename.dataset.rename, way = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
        // Alt+arrows move the agent being named (by keyboard it was placed at a free spot).
        if (way && event.altKey && !event.ctrlKey && !event.metaKey) {
          event.preventDefault(); const p = positions.get(name); if (!p) return;
          stopLayout(); const before = copyMoves(); moveNode(name, p.x + way[0] * NUDGE_PX, p.y + way[1] * NUDGE_PX); finishMove(before, { agent: name, by: 'key' }); return;
        }
        if (event.key === 'Enter') { event.preventDefault(); renameAgent(name, rename.value.trim(), rename); return; }
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); rename.value = name; rename.blur(); viewport.focus({ preventScroll: true }); return; }
      }
      const named = event.target.closest('[data-route-name]');
      if (named && event.key === 'Enter') { event.preventDefault(); saveName(named); return; }
      if (named && event.key === 'Escape' && named.value !== named.dataset.saved) { event.preventDefault(); event.stopPropagation(); named.value = named.dataset.saved; return; }
      // Enter in the goal box is a new line; Ctrl/Cmd+Enter runs (or copies).
      const goal = event.target.closest('[data-run-goal]');
      if (goal && event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); runPath(goal.dataset.runGoal); return; }
      const box = event.target.closest('[data-route-text]');
      if (box && event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); saveRouteText(box); }
      const team = event.target.closest('[data-team-text]');
      if (team && event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); saveTeamText(team); }
      if (event.target.closest('[data-skill-text]') && event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); saveSkill(); }
    });
    listen(root, 'keydown', event => {
      if (event.key === 'Escape' && draft?.following) { event.preventDefault(); dropDraft(); return; }
      if (event.key === 'Escape' && (press?.moved || drag?.moved)) { event.preventDefault(); cancelDrag(); return; }
      const typing = !!event.target.closest('textarea, input, select');
      if (!typing && !event.defaultPrevented && (event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && event.key.toLowerCase() === 'z') {
        if (flowHistory.length) { event.preventDefault(); undoFlow(); }
        return;
      }
      if ((flowAction || skillAction || rosterAction || teamAction || skillfileAction) && !typing && !event.defaultPrevented) {
        const plain = !event.ctrlKey && !event.metaKey && !event.altKey;
        // Delete and Backspace edit text in a field (excluded above) and do nothing on the details panel's controls.
        const inPanel = !!event.target.closest('.ag-detail');
        if (event.key === 'Escape' && (pickedFlow || pickedMember) && !trail.length && !press && !inPanel) { pickFlow(''); }
        if (event.key === 'Escape' && (trail.length || press)) { event.preventDefault(); if (press?.moved) finishMove(press.before); cancelPress(); dropTrail(); return; }
        // Backspace drops the newest step of a pending path; with none it deletes a flow or a group line, never an agent.
        if (event.key === 'Backspace' && plain && trail.length && !inPanel) { event.preventDefault(); backStep(); return; }
        if (event.key === 'Enter' && plain && trail.length && !event.target.closest('.ag-detail, button, [data-agent]')) { event.preventDefault(); finishTrail(); return; }
        // Delete with a path of two or more steps pending does nothing (Backspace and Escape edit the path).
        if ((event.key === 'Delete' && trail.length <= 1 || event.key === 'Backspace' && !trail.length) && plain && !inPanel) {
          const target = deleteTarget(event.target), remove = event.key === 'Backspace' && target?.type === 'agent' ? null : deleter(target);
          if (remove) { event.preventDefault(); remove(); }
          // An agent that stays (the orchestrator, the council, a seat, one the roster keeps) says why every time: it
          // shakes, the reason is its tooltip and is announced, whether or not its details are open.
          else if (event.key === 'Delete' && target?.type === 'agent') { const why = removableOf(target.key).why; if (why) refuseFlow(target.key, why, false); }
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
      camera.touched = true; camera.own = true; clampPan(); transform();
    });
    listen(viewport, 'wheel', event => {
      event.preventDefault(); const rect = viewport.getBoundingClientRect();
      if (arrangement === 'compact' && !event.ctrlKey && !event.metaKey) {
        camera.y -= event.deltaY; camera.touched = true; camera.own = true; clampPan(); transform(); return;
      }
      zoomAt(camera.scale * Math.exp(-clamp(event.deltaY, -160, 160) * .007), event.clientX - rect.left, event.clientY - rect.top);
    }, { passive: false });
    listen(nodeLayer, 'click', event => {
      const ring = event.target.closest('[data-learning]');
      if (ring) { select('learning', '', ring); return; }
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
      const line = !event.target.closest?.('[data-agent]') && event.clientX !== undefined ? lineAt(event.clientX, event.clientY, event.target) : null;
      const target = infoTarget(line || event.target);
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
      const work = event.target.closest('[data-work-loop-id]');
      if (work && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); select('work-loop', work.dataset.workLoopId, work); return; }
      const loop = event.target.closest('[data-loop-id]');
      if (loop && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); select('loop', loop.dataset.loopId, loop); return; }
      const route = event.target.closest('[data-route]');
      if (route && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); select('flow', route.dataset.route, route); return; }
      const member = event.target.closest('[data-team-line]');
      if (member && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); openInfo(infoTarget(member)); return; }
      // `g` opens a focused or picked path's details at its goal box, to run it.
      if (event.key.toLowerCase() === 'g' && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey && canRun()) {
        const id = route ? route.dataset.route : event.target === viewport ? pickedFlow : '';
        if (id && routeById(id)) { event.preventDefault(); openRun(id); return; }
      }
      // `n` opens the +'s menu (where the roster can be written).
      if ((event.target === viewport || event.target.closest('[data-agent]')) && event.key.toLowerCase() === 'n' && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey && canAddAgent()) { event.preventDefault(); openAddMenu(true); return; }
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
        // The user's own pan and zoom stay (n8 major 6): only an automatic view is fitted again.
        if (arrangement === 'compact' || camera.own) { clampPan(); transform(); } else fitView(false);
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
      event.preventDefault(); camera.touched = true; camera.own = true; clampPan(); transform();
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
        const loop = lineAt(event.clientX, event.clientY, event.target);
        drag = { id: event.pointerId, route: loop?.dataset.route || '', member: loop?.dataset.teamLine || '', startX: event.clientX, startY: event.clientY, x: camera.x, y: camera.y, moved: false };
        suppressClick = false;
        if (loop) loop.focus({ preventScroll: true });
      }
      viewport.setPointerCapture(event.pointerId);
    });
    listen(viewport, 'pointermove', event => {
      if (!pointers.size && event.pointerType !== 'touch') hoverOn(event.target.closest?.('[data-agent]') ? null : lineAt(event.clientX, event.clientY, event.target));
      if (flowAction && trail.length && !trailPending && !press) { const point = stagePoint(event.clientX, event.clientY); showWire(trail.at(-1), point.x, point.y); aim(agentAt(event.clientX, event.clientY)); }
      if (!pointers.has(event.pointerId)) return;
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (hold?.id === event.pointerId && !hold.fired && Math.hypot(event.clientX - hold.x, event.clientY - hold.y) > MOVE_PX) clearHold();
      if (pinch && pointers.size >= 2) {
        const [a, b] = Array.from(pointers.values()), rect = viewport.getBoundingClientRect();
        camera.scale = clamp(pinch.scale * Math.hypot(a.x - b.x, a.y - b.y) / Math.max(1, pinch.distance), NAME_FLOOR, 3);
        camera.x = (a.x + b.x) / 2 - rect.left - pinch.x * camera.scale;
        camera.y = (a.y + b.y) / 2 - rect.top - pinch.y * camera.scale;
        camera.touched = true; camera.own = true; transform(); return;
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
      // One threshold for every drag: the background pans, like an agent moves, only past MOVE_PX.
      if (!drag.moved && Math.hypot(dx, dy) <= MOVE_PX) return;
      drag.moved = true; camera.touched = true; camera.own = true; suppressClick = true; viewport.dataset.dragging = 'true';
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
        else if (drag.member && teamLines.has(drag.member)) pickMember(drag.member);
        else pickFlow(drag.route && routeById(drag.route) ? drag.route : '');
      }
      if (drag && !drag.moved) suppressClick = true;
      pointers.delete(event.pointerId); pinch = null; drag = null; viewport.dataset.dragging = 'false';
      if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
    }
    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(name => listen(viewport, name, pointerUp));
    listen(viewport, 'pointerleave', () => hoverOn(null));
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
      if (!name && !event.target.closest?.('[data-route], [data-team-line], [data-loop-id], [data-work-loop-id], [data-learning], button')) { event.preventDefault(); resetMoves(); return; }
      if (!flowAction || trailPending) return;
      const step = name && standIn(name);
      if (!step) return;
      if ((!trail.length && loneCancel === step) || (trail.length === 1 && trail[0] === step)) { event.preventDefault(); loopOnItself(step); }
      loneCancel = '';
    });

    function render(data) {
      const previousTeam = activeTeam;
      snapshot = { ...data, agents: prepareRoster(data) };
      const stamp = JSON.stringify([data.agents, data.graph, data.warnings, data.totals, data.escalation, data.root?.wear, data.structure, data.skill_warnings, data.skills, data.roster]);
      if (stamp !== renderStamp) { renderStamp = stamp; renderRevision++; }
      // The host's answer stands for an agent's skills only until a snapshot shows the same list.
      skillOverride.forEach((list, name) => { const agent = (data.agents || []).find(item => item?.name === name); if (!agent || JSON.stringify(agent.skills) === JSON.stringify(list)) skillOverride.delete(name); });
      indexSteps();
      if (activeTeam !== previousTeam) { rememberTeam(previousTeam); restoreTeam(activeTeam); }
      if (trail.some(name => !shownAgents.has(name))) dropTrail();
      const graph = data.graph;
      runDetails.dataset.notices = String(data.warnings.length + (graph ? graph.problems.length : 0) > 0 || !!data.learning?.due);
      renderActivity(); updatePlus();
      const previousLayout = layoutSignature;
      if (root.clientWidth > 0 && viewport.clientWidth > 0) {
        addPositions(visibleAgents);
        const labelsChanged = renderNodes(visibleAgents); updateEdges();
        if (!camera.initialized) fitView(false);
        else if (previousLayout !== layoutSignature) settleCamera();
        // Names measured again just after a loop formed or an agent came, nothing else moved: the view stays (n103).
        else if (labelsChanged && !camera.touched) { if (layoutChange.steady) { revealNames(layoutChange.changed); clampPan(true); transform(); } else fitView(false); }
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
      keepFocus();
      // On a page of its own (options.autofocus) the graph takes the keys once it is drawn, so N works without a click;
      // never from something the user is already in, and never in a host's panel (no autofocus there).
      if (options.autofocus === true && !autofocused && visibleAgents.length) {
        autofocused = true;
        const current = document.activeElement;
        if (!current || current === document.body || current === document.documentElement) viewport.focus({ preventScroll: true });
      }
    }
    let autofocused = false;
    const paneSize = { width: 0, height: 0 };
    function scheduleResize() {
      if (!alive || resizeFrame !== null) return;
      resizeFrame = requestAnimationFrame(() => {
        resizeFrame = null;
        if (!alive || root.clientWidth <= 0 || root.clientHeight <= 0) return;
        root.classList.toggle('ag-compact', root.clientWidth <= 650);
        updateDetailSpace();
        fitFigures();
        const rect = viewport.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return;
        const next = root.clientWidth <= 650 ? 'compact' : 'wide';
        const resized = next !== arrangement || Math.abs(rect.width - camera.width) > .1 || Math.abs(rect.height - camera.height) > .1;
        if (resized) cancelInteraction();
        root.classList.toggle('ag-canvas-compact', next === 'compact');
        const switched = next !== arrangement;
        // The pane itself changed size (not only the space the details leave inside it).
        const paneResized = Math.abs(root.clientWidth - paneSize.width) > .5 || Math.abs(root.clientHeight - paneSize.height) > .5;
        paneSize.width = root.clientWidth; paneSize.height = root.clientHeight;
        if (snapshot && next !== arrangement) {
          rememberTeam(activeTeam);
          arrangement = next;
          restoreTeam(activeTeam);
        } else arrangement = next;
        if (snapshot) { reseat(); addPositions(visibleAgents); renderNodes(visibleAgents); updateEdges(); }
        // The user's own pan and zoom survive anything that did not change the space.
        // When the details open or close the graph refits into the space left, unless the user panned or zoomed since
        // (then their view stays). A change of the pane's own size, or of arrangement, refits as before.
        if ((resized || !camera.initialized) && !(camera.own && camera.initialized && !switched && !paneResized)) { fitView(false); reveal(); } else { clampPan(); transform(); }
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
      let number = 0;
      const request = Promise.resolve().then(() => {
        if (alive && requestGeneration === generation) { number = ++fetchesStarted; return options.fetchSnapshot(); }
      }).then(data => {
        if (!alive || requestGeneration !== generation) return;
        if (!data || data.error || data.version !== 1 || !Array.isArray(data.agents)) {
          const error = new Error(data?.error ? text(data.error) : 'Snapshot is not available');
          error.documentError = true;
          throw error;
        }
        renderedFetch = number; render(data); notice.hidden = true;
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
      freshRoutes.clear(); flowErrors.clear(); flowHistory.length = 0; routeViews.clear(); teamLines.clear(); agentNotes.clear(); skillView = null; teamDraft = null; pickedMember = ''; pickedFlow = ''; formBusy = false; trailStatus.textContent = ''; delete root.dataset.agTrail;
      selected = null; selectedOrigin = null; initialSelection = ''; highlightedStep = ''; detailSignature = '';
      teamChoice = null; activeTeam = 'main'; teamChoices = []; layoutSignature = ''; renderStamp = '';
      arrangement = root.clientWidth > 0 && root.clientWidth <= 650 ? 'compact' : 'wide';
      visibleAgents = []; hubName = ''; openSteps.clear(); openFolds.clear(); teamViews.clear(); positions.clear(); nodes.clear(); lines.clear(); loopViews.clear(); workLoopViews.clear();
      agentsByName.clear(); stepsByAgent.clear(); stepsById.clear(); nodeOwners.clear(); shownAgents.clear();
      nodeLayer.replaceChildren(); spokeLayer.replaceChildren(); escalationLayer.replaceChildren(); flowLayer.replaceChildren(); loopLayer.replaceChildren(); routeLayer.replaceChildren(); teamLayer.replaceChildren(); trailLayer.replaceChildren();
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
