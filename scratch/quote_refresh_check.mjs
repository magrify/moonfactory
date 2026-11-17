// Automated proof for the stale Generate-price fix (2026-11-17).
// Extracts the studio's real genLabel + fetchQuote source from
// platform/app.py and runs it against a stubbed DOM and a
// controllable fake quote API:
//   1. two models with different rates, the first quote slow and
//      the second fast (the old code let the slow, stale response
//      land last and overwrite the button) -> the button must end
//      on the SECOND model's price;
//   2. a length change on the same model must reprice;
//   3. a failed quote must clear the button price, never leave
//      the previous model's price sitting on it.
// Run:  node scratch/quote_refresh_check.mjs
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runInThisContext } from 'node:vm';

const src = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '..', 'platform', 'app.py'),
  'utf8');

function extract(name) {
  const start = src.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`function ${name} not found`);
  const asyncStart = src.lastIndexOf('async ', start);
  const from = (asyncStart >= 0 && start - asyncStart < 8) ? asyncStart : start;
  let depth = 0;
  for (let i = src.indexOf('{', start); i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (!depth) return src.slice(from, i + 1); }
  }
  throw new Error(`function ${name} not closed`);
}

// Stub DOM: only the elements genLabel/fetchQuote touch.
const els = {
  quote: { textContent: '' },
  create: { textContent: '' },
  model: { value: 'aaa' },
  duration: { value: '5' },
  resolution: { value: '720p' },
};
const $ = (id) => els[id];
const currentUser = { paid_customer: true };
const isPaidSurface = () => true;

// Fake quote API: price depends on model + duration; latency is
// per model so responses resolve out of order on purpose.
const LATENCY = { aaa: 60, bbb: 5 };
let failNext = false;
const api = async (path, opts) => {
  const body = JSON.parse(opts.body);
  if (failNext) { failNext = false; throw new Error('quote failed'); }
  const cents = (body.model === 'aaa' ? 111 : 222) * (body.duration_seconds / 5);
  await new Promise((r) => setTimeout(r, LATENCY[body.model] || 0));
  return { lane_paused: false, ceiling_ok: true,
           payment: { method: 'balance', charge_cents: cents } };
};

Object.assign(globalThis, { $, api, currentUser, isPaidSurface });
runInThisContext(
  extract('genLabel') + '\nlet quoteSeq = 0;\n' + extract('fetchQuote'));
const fetchQuote = globalThis.fetchQuote;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
const expect = (cond, msg) => {
  console.log((cond ? 'PASS' : 'FAIL') + ' ' + msg);
  if (!cond) failures++;
};

// 1. Switch aaa -> bbb quickly; aaa's slow quote resolves last.
els.model.value = 'aaa'; els.duration.value = '5';
const p1 = fetchQuote();
els.model.value = 'bbb';
const p2 = fetchQuote();
await Promise.all([p1, p2]); await sleep(10);
expect(els.create.textContent === 'Generate · $2.22',
  `after switching models the button shows the new model price (got "${els.create.textContent}")`);

// 2. Length change on the same model reprices immediately.
els.duration.value = '10';
await fetchQuote(); await sleep(10);
expect(els.create.textContent === 'Generate · $4.44',
  `after changing length the button reprices (got "${els.create.textContent}")`);

// 3. A failed quote clears the price instead of leaving a stale one.
failNext = true;
await fetchQuote(); await sleep(10);
expect(els.create.textContent === 'Generate',
  `after a failed quote the button carries no stale price (got "${els.create.textContent}")`);

if (failures) { console.log(`${failures} FAILURES`); process.exit(1); }
console.log('ALL QUOTE CHECKS PASSED');
