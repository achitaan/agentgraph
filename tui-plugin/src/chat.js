/*
 * The chat of one agent as lines for the panel: pure text, no host imports (shared by the plugin and the tests).
 *
 * chatLines(view, width) -> Line[]   all the lines, oldest first, newest last, each at most `width` columns
 *   view: { state: "none" | "loading" | "error" | "feed", feed?, error?, extra? }
 *   feed: the document of `agentgraph chat --json` (items oldest first); extra: items still waiting here (a message
 *   just sent whose feed has not caught up), drawn after the feed's.
 * chatWindow(lines, rows, scroll) -> { lines, scroll, max, rows, total }   the `rows` lines that show, `scroll` lines
 *   up from the newest (0 follows the newest); scroll is clamped to what exists.
 *
 * Kinds: `reasoning` (what it thinks) is muted with a "·" mark; `text` (what it said) is plain; `tool` is its name
 * and its state only; `user` (what it was told) is marked "told", with "(pending)" while it sits in the inbox;
 * `note` is one quiet word. A text that was `cut` and a feed that was `truncated` are said quietly.
 *
 * Every string of the feed is untrusted and cleaned here again before it is drawn (cleanChat): escape sequences
 * and control characters go, a tab becomes spaces, and each line is measured in display columns.
 */
import { charWidth, displayWidth, truncate, ELLIPSIS } from "./render.js";

export const CHAT_MIN_INLINE = 6; // the rows (heading included) the chat needs beside the list; less and it opens in place of it
export const CHAT_MIN_WIDTH = 8;

const CSI = /\u001b\[[0-?]*[ -/]*[@-~]/g;
const OSC = /\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)?/g;
const C1_CSI = /\u009b[0-?]*[ -/]*[@-~]/g;
const ESCAPE = /\u001b[@-_]?/g;
const BIDI = /[\u061c\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]/g;
const LONE_SURROGATE = /[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/g;
const CONTROL = /[\u0000-\u0009\u000b-\u001f\u007f-\u009f\u2028\u2029]/g;

// Untrusted text -> printable text where "\n" is the only control character left.
export function cleanChat(value) {
  if (value === undefined || value === null) return "";
  return String(value)
    .replace(/\r\n?/g, "\n")
    .replace(/\t/g, "  ")
    .replace(OSC, "")
    .replace(CSI, "")
    .replace(C1_CSI, "")
    .replace(ESCAPE, "")
    .replace(LONE_SURROGATE, "")
    .replace(BIDI, "")
    .replace(CONTROL, "")
    .replace(/[ \u00a0]+\n/g, "\n");
}

// A name or a state: one short word, cleaned.
const word = (value) => cleanChat(value).replace(/\s+/g, " ").trim();

const sp = (text, tone) => ({ text, tone });

// One source line cut into pieces of at most `width` columns, at spaces where it can, inside a word where it must.
function wrapOne(line, width) {
  const out = [];
  let cur = "";
  let curW = 0;
  const flush = () => {
    if (cur) out.push(cur);
    cur = "";
    curW = 0;
  };
  const addWord = (w) => {
    let wordW = displayWidth(w);
    if (wordW <= width) {
      if (!cur) {
        cur = w;
        curW = wordW;
      } else if (curW + 1 + wordW <= width) {
        cur += " " + w;
        curW += 1 + wordW;
      } else {
        flush();
        cur = w;
        curW = wordW;
      }
      return;
    }
    // longer than a line: split it by columns
    flush();
    let piece = "";
    let pieceW = 0;
    for (const ch of w) {
      const cw = charWidth(ch);
      if (pieceW + cw > width && piece) {
        out.push(piece);
        piece = "";
        pieceW = 0;
      }
      piece += ch;
      pieceW += cw;
    }
    cur = piece;
    curW = pieceW;
  };
  for (const w of line.split(/\s+/)) if (w) addWord(w);
  flush();
  return out;
}

// Text -> lines of at most `firstWidth` (the first) and `width` columns (the rest); a blank source line stays one blank line.
export function wrapText(text, width, firstWidth = width) {
  const out = [];
  let blank = false;
  for (const source of cleanChat(text).replace(/^\n+|\n+$/g, "").split("\n")) {
    const pieces = wrapOne(source, out.length ? width : firstWidth);
    if (!pieces.length) {
      if (!blank && out.length) out.push("");
      blank = true;
      continue;
    }
    blank = false;
    // the first piece of a later source line already used `width`; only the very first line has `firstWidth`
    out.push(...pieces);
  }
  while (out.length && out[out.length - 1] === "") out.pop();
  return out;
}

const STATE_WORDS = Object.freeze({ completed: "done", error: "failed", running: "running", streaming: "running", pending: "waiting" });

function toolLines(item, width) {
  const name = word(item.name) || "tool";
  const raw = word(item.status);
  const state = raw ? STATE_WORDS[raw] || raw : "";
  const tone = state === "failed" ? "danger" : state === "running" ? "accent" : "muted";
  const stateText = state ? " " + truncate(state, 12) : "";
  const room = Math.max(1, width - 2 - displayWidth(stateText));
  const line = [sp("▸ ", "muted"), sp(truncate(name, room), "muted")];
  if (stateText) line.push(sp(stateText, tone));
  return [line];
}

function textLines(item, width, { tone, mark = "", indent = "", label = null }) {
  const markW = displayWidth(mark);
  const labelW = label ? displayWidth(label.map((s) => s.text).join("")) : 0;
  const lead = labelW + markW;
  const hang = displayWidth(indent) || markW;
  const firstW = Math.max(1, width - lead);
  const restW = Math.max(1, width - hang);
  const rows = wrapText(item.text, restW, firstW);
  if (!rows.length) return [];
  const out = rows.map((text, i) => {
    const line = [];
    if (i === 0) {
      if (label) line.push(...label);
      if (mark) line.push(sp(mark, "muted"));
    } else if (hang) {
      line.push(sp(" ".repeat(hang), "plain"));
    }
    if (text) line.push(sp(text, tone));
    return line;
  });
  if (item.cut === true) out.push([sp(" ".repeat(Math.min(hang, Math.max(0, width - 5))) + "(cut)", "muted")]);
  return out;
}

function kindOf(item) {
  const kind = typeof item.kind === "string" ? item.kind : "";
  if (kind === "text" && item.user === true) return "user";
  return kind;
}

export function itemLines(item, width) {
  const kind = kindOf(item);
  if (kind === "reasoning") return textLines(item, width, { tone: "muted", mark: "· ", indent: "  " });
  if (kind === "text") return textLines(item, width, { tone: "plain" });
  if (kind === "user") {
    const label = [sp(item.pending === true ? "told (pending) " : "told ", "accent")];
    return textLines(item, width, { tone: "plain", label, indent: "  " });
  }
  if (kind === "tool") return toolLines(item, width);
  if (kind === "note") return [[sp("· note", "muted")]];
  return [];
}

const quiet = (text, width) => [sp(truncate(word(text), width), "muted")];

// All the lines of a view, oldest first.
export function chatLines(view, rawWidth) {
  const width = Math.max(CHAT_MIN_WIDTH, Math.floor(Number(rawWidth) || CHAT_MIN_WIDTH));
  const v = view && typeof view === "object" ? view : { state: "loading" };
  if (v.state === "none") return [quiet("No session yet", width)];
  if (v.state === "loading") return [quiet("Reading the chat…", width)];
  if (v.state === "error") return [[sp(truncate(word(v.error) || "The chat could not be read.", width), "danger")]];
  const feed = v.feed && typeof v.feed === "object" ? v.feed : {};
  const items = [...(Array.isArray(feed.items) ? feed.items : []), ...(Array.isArray(v.extra) ? v.extra : [])].filter((i) => i && typeof i === "object");
  const lines = [];
  const anyCut = items.some((i) => i.cut === true);
  if (feed.more === true) lines.push(quiet("… older chat left out", width));
  else if (feed.truncated === true && !anyCut) lines.push(quiet("… some text cut", width));
  for (const item of items) lines.push(...itemLines(item, width));
  if (!items.length) lines.push(quiet("Nothing said yet", width));
  if (typeof feed.warning === "string" && word(feed.warning)) lines.push(quiet(feed.warning, width));
  return lines.map((line) => (line.length ? line : [sp("", "plain")]));
}

export function chatWindow(lines, rawRows, scroll = 0) {
  const rows = Math.max(0, Math.floor(Number(rawRows) || 0));
  const total = lines.length;
  const max = Math.max(0, total - rows);
  const at = Math.max(0, Math.min(max, Math.floor(Number(scroll) || 0)));
  const end = total - at;
  return { lines: rows ? lines.slice(Math.max(0, end - rows), end) : [], scroll: at, max, rows, total };
}

// The rule above the chat: "chat · name" (or "transcript · name" when it cannot be talked to), a rule, and how far
// back it is scrolled ("↓12": twelve lines below).
export function chatHeading({ name, live, scroll = 0 }, width) {
  const title = (live ? "chat" : "transcript") + " · ";
  const back = scroll > 0 ? ` ↓${scroll}` : "";
  const room = width - displayWidth(title) - displayWidth(back) - 2;
  const who = room >= 1 ? truncate(word(name) || "?", room) : "";
  const text = who ? title + who : (live ? "chat" : "transcript");
  const fill = Math.max(0, width - displayWidth(text) - displayWidth(back));
  const out = [sp(text, "muted")];
  if (fill > 0) out.push(sp(" " + "─".repeat(Math.max(0, fill - 1)), "muted"));
  if (back) out.push(sp(back, "accent"));
  return out;
}

export { ELLIPSIS };
