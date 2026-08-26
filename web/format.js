/* Formatting helpers shared by the pages. Loaded as a native ES module: no bundler, no build
   step, no package.json. `<script type="module" src="format.js">` is all the browser needs.

   This is the ONLY shared web module, and that is deliberate. A scan of web/ found six function
   names defined in more than one file (render, draw, paint, connect, esc, applyPreset) and ZERO
   with identical bodies -- the pages genuinely do different things that happen to share generic
   names. Extracting a player or an api module would have been inventing abstraction rather than
   removing duplication.

   What WAS repeated is in here: mm:ss was implemented three times (twice inline in index.html,
   once as `fmt` in app.js, with `fmtTime` wrapping `fmt` for no reason), `esc` existed three times
   with a real difference -- two guarded null and one did not, so the odd one could render the
   literal text "null" into the page -- and `ago`/`until` were one function written twice pointing
   opposite directions, only one of which guarded a missing timestamp.

   Every page that needs any of these imports them. `cast.html` is the one page that does not, and
   that is not an oversight: it writes through `textContent` throughout, so it needs no escaping. */

/** Seconds as m:ss, the form every duration on the site uses.
 *
 * Floors rather than rounds: a 59.6s episode reading as "1:00" then stopping at 0:59 looks like
 * a bug in the player. Handles null/undefined as 0, because a duration can be missing from an
 * episode whose render was interrupted. */
export function mmss(seconds) {
  const s = Math.floor(Number(seconds) || 0);
  return (s / 60 | 0) + ':' + String(s % 60).padStart(2, '0');
}

/** Escape text for interpolation into HTML.
 *
 * Guards null and undefined on purpose. The previous copy in index.html did `String(s)`, which
 * turns null into the four characters "null" and renders them on the page. Not currently
 * reachable there (the trending payload uses a computed hn_url that is always set), but the two
 * copies had drifted and only one of them was right. */
export function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Seconds since `when`, negative if it is still ahead, or null if there is no usable timestamp.
 *
 * That null is the whole reason `ago` and `until` are worth sharing. The status board calls them
 * on `st.last_completed.at` and on the next scheduled run, and BOTH are absent before the first
 * cron read; `new Date(undefined)` is an Invalid Date whose arithmetic yields NaN, so the two
 * inline copies rendered the literal strings "NaNd ago" and "in NaNm" on a fresh deploy. */
function _deltaSeconds(when) {
  if (!when) return null;
  const then = when instanceof Date ? when : new Date(when);
  const t = then.getTime();
  if (Number.isNaN(t)) return null;
  return (Date.now() - t) / 1000;
}

/** A magnitude both directions share: "4m", "2h", "3d". */
function _span(seconds) {
  if (seconds < 3600) return Math.round(seconds / 60) + 'm';
  if (seconds < 86400) return Math.round(seconds / 3600) + 'h';
  return Math.round(seconds / 86400) + 'd';
}

/** A short, human relative time in the past: "4m ago", "2h ago", "3d ago".
 *
 * Thresholds match the copy this replaced from one minute up. That copy also had a sub-minute
 * band rendering "Ns ago", dropped because nothing here is timed finely enough to earn it. */
export function ago(when) {
  const d = _deltaSeconds(when);
  return d === null ? '' : _span(Math.max(0, d)) + ' ago';
}

/** The same relative time pointed forward: "in 4m", "in 2h", "in 3d".
 *
 * `ago`'s mirror image, so it lives beside it and shares the guard rather than repeating it. The
 * inline copy stopped at hours; a day band keeps the two directions reading the same way when a
 * cron is more than 24h out. Both clamp at zero, so a slightly skewed clock reads "in 0m" rather
 * than counting backwards. */
export function until(when) {
  const d = _deltaSeconds(when);
  return d === null ? '' : 'in ' + _span(Math.max(0, -d));
}
