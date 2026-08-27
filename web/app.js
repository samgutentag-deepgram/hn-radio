// HN Radio episode page: Play + Recast. Reads the pipeline JSON; recasts via the FastAPI backend
// (falls back to a copy-able CLI command if no backend is running). No build step: this is a
// native ES module, loaded with <script type="module">, so `import` works without a bundler.
import { mmss } from './format.js';

(function () {
  var params = new URLSearchParams(location.search);
  var id = params.get('id');
  if (!id) { document.getElementById('title').textContent = 'No episode id'; return; }

  var base = '/episodes/' + encodeURIComponent(id) + '/';
  function slotFor(seg) { return seg.desk ? seg.desk : (seg.role === 'commenter' ? 'guest' : null); }

  // Compact chapter strip: one numbered dot per chapter. It shows at a glance how many chapters an
  // episode has and lets you jump, without repeating the titles that already head each block below.
  // Returns nothing. It used to return the dot list "so the player's timeupdate handler can mark
  // the current one", and that handler now reads the dots out of the DOM by their data-start
  // instead (see the note at the timeupdate listener), which is why the array went dead. The
  // caller at the bottom of this file has always discarded the value.
  function renderChaptersAndNotes(ep, chaptersDoc, player) {
    if (ep.summary) {
      var p = document.createElement('p'); p.className = 'summary'; p.textContent = ep.summary;
      document.getElementById('notes').appendChild(p);
    }
    var chapters = (chaptersDoc && chaptersDoc.chapters) || [];
    if (!chapters.length) return;   // no strip to build; falling through renders "0 chapters"

    var chapEl = document.getElementById('chapters');
    var strip = document.createElement('div'); strip.className = 'chapter-strip';
    var label = document.createElement('span'); label.className = 'chapter-strip-label';
    label.textContent = chapters.length + ' chapters';
    strip.appendChild(label);

    chapters.forEach(function (c, i) {
      var dot = document.createElement('button');
      dot.type = 'button';
      dot.className = 'chapter-dot';
      dot.textContent = String(i + 1);
      // The number alone is not a usable name, so the real one goes on the label and the tooltip.
      var name = (i + 1) + '. ' + c.title + ' (' + mmss(c.startTime) + ')';
      dot.title = name;
      dot.setAttribute('aria-label', 'Jump to ' + name);
      dot.addEventListener('click', function () {
        // Seek, but never start playback that was not already running: tapping a chapter to look
        // at it should not blast audio at someone who has the episode paused.
        player.currentTime = c.startTime;
        var head = document.getElementById('ch-' + i);
        if (head) head.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
      dot.setAttribute('data-start', String(c.startTime));
      strip.appendChild(dot);
    });
    chapEl.appendChild(strip);
  }

  Promise.all([
    fetch(base + 'episode.json').then(function (r) { return r.json(); }),
    fetch(base + 'script.json').then(function (r) { return r.json(); }),
    fetch('/episodes/voices.json').then(function (r) { return r.json(); }),
    fetch(base + 'chapters.json').then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
  ]).then(function (res) {
    render(res[0], res[1], res[2], res[3]);
  }).catch(function (e) {
    document.getElementById('title').textContent = 'Could not load episode';
    document.getElementById('meta').textContent = String(e);
  });

  function render(ep, segments, voicesDoc, chaptersDoc) {
    document.title = 'HN Radio: ' + ep.title;
    document.getElementById('title').textContent = ep.title;
    document.getElementById('meta').textContent =
      (ep.edition ? ep.edition + ' · ' : '') + Math.round(ep.duration_seconds) + 's · ' + (ep.generated_at || '');

    var player = document.getElementById('player');
    player.src = base + 'episode.mp3';  // chaptered MP3 (podcast-friendly, small)
    renderChaptersAndNotes(ep, chaptersDoc, player);

    // --- play instrumentation -----------------------------------------------------------------
    // Guarded on window.HNPlays rather than assumed. plays.js is a separate <script> and this is
    // the page's core render path: if that file 404s after a bad deploy, or an ad blocker eats it
    // for having "plays" in the name, the episode still has to render.
    //
    // The count read here does NOT include the view that was just fired -- /api/stats is a
    // snapshot taken at load. Reconciling that would mean either a second request after the POST
    // or an optimistic +1, and both are more machinery than a number under a headline deserves.
    if (window.HNPlays) {
      window.HNPlays.view(id);
      window.HNPlays.attach(id, player);
      window.HNPlays.byEpisode().then(function (rows) {
        var row = rows[id];
        var el = document.getElementById('play-count');
        if (!el || !row || !row.plays) return;
        el.textContent = (row.plays === 1 ? '1 play' : row.plays.toLocaleString() + ' plays')
          + ' on this site';
        el.hidden = false;
      });
    }

    // --- script (play tab), grouped under its chapters ---
    // Previously this rendered every chapter, then every segment, with nothing tying the two
    // together. Segments are now emitted inside the chapter whose time range contains them, so the
    // structure of the episode is visible while reading it.
    var scriptEl = document.getElementById('script');
    var vname = {};  // voice_id -> catalog name, so anchor/desk names reflect the CURRENT voice (incl. after recast)
    voicesDoc.voices.forEach(function (v) { vname[v.id] = v.name; });
    var starts = [];

    function segmentRow(seg) {
      var row = document.createElement('div');
      row.className = 'seg ' + seg.role;
      // Identity for the row, in the order brand.css block 1 resolves it: the VOICE that read
      // the line first, the SEAT as the fallback. The voice is what a listener actually learns to
      // recognise, and an archive episode can name a seat this build no longer has, so both are
      // emitted and the stylesheet's source order decides which one wins.
      // slotFor already folds commenters into 'guest', so every row gets an identity or none.
      var deskSlot = slotFor(seg);
      if (deskSlot) row.setAttribute('data-desk', deskSlot);
      if (seg.voice_id) row.setAttribute('data-voice', seg.voice_id);
      var ctrl = document.createElement('div'); ctrl.className = 'ctrl';
      if (seg.start_seconds != null) {
        var b = document.createElement('button'); b.className = 'icon'; b.textContent = '▶';
        b.title = 'Play from ' + mmss(seg.start_seconds);
        b.addEventListener('click', function () { player.currentTime = seg.start_seconds; player.play(); });
        var ts = document.createElement('span'); ts.className = 'ts'; ts.textContent = mmss(seg.start_seconds);
        ctrl.appendChild(b); ctrl.appendChild(ts);
        starts.push({ el: row, start: seg.start_seconds });
      }
      var body = document.createElement('div'); body.className = 'body';
      var who = document.createElement('div'); who.className = 'who';
      var label = seg.role === 'commenter' ? '@' + seg.speaker_key
        : (seg.role === 'anchor' || seg.role === 'host' ? (vname[seg.voice_id] || seg.speaker_key || 'Anchor')
          : (vname[seg.voice_id] || seg.speaker_key) + (seg.desk ? ' · ' + seg.desk + ' desk' : ''));
      who.textContent = label + ' ';
      var v = document.createElement('span'); v.className = 'voice'; v.textContent = seg.voice_id || '';
      who.appendChild(v);
      if (seg.role === 'commenter' && seg.source_hn_id) {
        var link = document.createElement('a');
        link.href = 'https://news.ycombinator.com/item?id=' + seg.source_hn_id;
        link.target = '_blank'; link.rel = 'noopener'; link.textContent = ' source';
        link.style.marginLeft = '.4rem'; who.appendChild(link);
      }
      var text = document.createElement('div'); text.className = 'text'; text.textContent = seg.text;
      body.appendChild(who); body.appendChild(text);
      row.appendChild(ctrl); row.appendChild(body);
      return row;
    }

    function chapterHeading(c, index) {
      var head = document.createElement('div');
      head.className = 'chapter-head';
      head.id = 'ch-' + index;                       // the top list jumps here
      var b = document.createElement('button');
      b.className = 'icon'; b.type = 'button'; b.textContent = '▶';
      b.title = 'Play from ' + mmss(c.startTime);
      b.addEventListener('click', function () { player.currentTime = c.startTime; player.play(); });
      var ts = document.createElement('span'); ts.className = 'ts'; ts.textContent = mmss(c.startTime);
      var title = document.createElement('span'); title.className = 'chapter-head-title';
      if (c.url) {
        var a = document.createElement('a'); a.href = c.url; a.target = '_blank'; a.rel = 'noopener';
        a.textContent = c.title; title.appendChild(a);
      } else { title.textContent = c.title; }
      head.appendChild(b); head.appendChild(ts); head.appendChild(title);
      return head;
    }

    var chapterList = (chaptersDoc && chaptersDoc.chapters) || [];
    if (!chapterList.length) {
      // No chapters for this episode: fall back to the flat list rather than losing the script.
      segments.forEach(function (seg) { scriptEl.appendChild(segmentRow(seg)); });
    } else {
      // Walk both lists in time order. A segment belongs to the last chapter that started at or
      // before it. Anything before the first chapter (there should be none) still gets rendered,
      // in an unlabelled block, so no line can silently disappear.
      var si = 0;
      var lead = [];
      while (si < segments.length && segments[si].start_seconds != null
             && segments[si].start_seconds < chapterList[0].startTime - 0.01) {
        lead.push(segments[si]); si++;
      }
      if (lead.length) {
        var leadBlock = document.createElement('div'); leadBlock.className = 'chapter-block';
        lead.forEach(function (seg) { leadBlock.appendChild(segmentRow(seg)); });
        scriptEl.appendChild(leadBlock);
      }
      chapterList.forEach(function (c, i) {
        var nextStart = (i + 1 < chapterList.length) ? chapterList[i + 1].startTime : Infinity;
        scriptEl.appendChild(chapterHeading(c, i));
        var block = document.createElement('div'); block.className = 'chapter-block';
        while (si < segments.length) {
          var st = segments[si].start_seconds;
          if (st != null && st >= nextStart - 0.01) break;
          block.appendChild(segmentRow(segments[si]));
          si++;
        }
        scriptEl.appendChild(block);
      });
      // Anything left over (a segment past the final chapter boundary) still belongs on the page.
      if (si < segments.length) {
        var tail = document.createElement('div'); tail.className = 'chapter-block';
        while (si < segments.length) { tail.appendChild(segmentRow(segments[si])); si++; }
        scriptEl.appendChild(tail);
      }
    }

    player.addEventListener('timeupdate', function () {
      var t = player.currentTime, active = null;
      for (var i = 0; i < starts.length; i++) { if (starts[i].start <= t + 0.02) active = starts[i]; else break; }
      starts.forEach(function (s) { s.el.classList.toggle('active', s === active); });
      // Same pass marks the chapter dot, so the strip doubles as a position indicator.
      // Read the dots from the DOM by their data-start rather than from a closed-over array: the
      // strip is rendered by a different function, and keeping the two in sync through a shared
      // array is a needless coupling when the elements already carry the value.
      var allDots = document.querySelectorAll('.chapter-dot');
      var currentDot = null;
      for (var j = 0; j < allDots.length; j++) {
        if (parseFloat(allDots[j].getAttribute('data-start')) <= t + 0.02) currentDot = allDots[j];
        else break;
      }
      for (var k = 0; k < allDots.length; k++) {
        var isCur = allDots[k] === currentDot;
        allDots[k].classList.toggle('active', isCur);
        if (isCur) allDots[k].setAttribute('aria-current', 'true');
        else allDots[k].removeAttribute('aria-current');
      }
    });

    // --- transport + the orb as the playback visual -----------------------------------------
    // This used to be a bar canvas beside the play button, drawn from an AnalyserNode. The bars
    // are gone and the TRANSPORT ORB is the visual now: the same analyser, reduced to an amplitude
    // envelope, drives the molten lava inside the orb, and the orb crossfades to the palette of
    // whoever is speaking. See web/orb.js and brand.css block 5.
    //
    // What has not changed is why the signal is real. For a text-to-speech demo the point is that
    // you are watching the speech itself, so a canned animation would be the same class of lie as
    // a fake progress bar. It follows that the orb goes still while paused: there is genuinely
    // nothing to show. It holds its molten pose rather than emptying out.
    //
    // And the contract this block has always kept is kept harder, because there are now two things
    // that can fail instead of one: if the AudioContext throws, `analyser` goes null; if the orb
    // fails to attach, HNOrb.attach returns null. Either way every reference below is guarded and
    // the audio plays. Visualisation must never break playback.
    (function () {
      var transport = document.getElementById('transport');
      var mark = document.getElementById('transport-mark');
      var timeEl = document.getElementById('transport-time');
      if (!transport) return;

      var analyser = null, env = null;
      var orb = (window.HNOrb && window.HNOrb.attach)
        ? window.HNOrb.attach(transport, { observe: false })
        : null;

      function connect() {
        // Built on the first play, because an AudioContext created before a user gesture starts
        // suspended. MediaElementSource routes playback through the graph, so if the context were
        // left suspended the audio would be silent, not merely un-analysed.
        if (analyser) return;
        var AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return;                       // no Web Audio: transport works, the orb just drifts
        try {
          var ac = new AC();
          var src = ac.createMediaElementSource(player);
          analyser = ac.createAnalyser();
          // 1024 samples is about 43ms at the 24 kHz these episodes are rendered at: long enough
          // for a stable RMS, short enough to still catch a syllable. It replaces the old
          // fftSize 128, which existed to give 28 bars something to read. smoothingTimeConstant is
          // gone with the bars: it only affects getByteFrequencyData, and the envelope reads the
          // time domain and does its own smoothing.
          analyser.fftSize = 1024;
          src.connect(analyser);
          analyser.connect(ac.destination);
          if (window.HNOrb && orb) {
            env = window.HNOrb.envelope(analyser);
            orb.setEnvSource(env);
          }
          if (ac.state === 'suspended') ac.resume();
        } catch (e) {
          analyser = null;                     // never let visualisation break playback
        }
      }

      // Whose palette the orb is wearing. Read off the ACTIVE TRANSCRIPT ROW rather than from a
      // second copy of the segment list: the row already carries data-voice and data-desk, the
      // handler above already marks which row is active, and handlers fire in registration order
      // so .seg.active is current by the time this one runs. brand.css block 1 resolves those two
      // attributes; orb.js reads the resulting values straight off the stylesheet.
      var lastRow = null, snapNext = false;
      function followSpeaker() {
        if (!orb) return;
        var row = document.querySelector('.seg.active');
        if (!row || row === lastRow) return;
        lastRow = row;
        orb.setSpeaker(row.getAttribute('data-voice'), row.getAttribute('data-desk'), snapNext);
        snapNext = false;
      }
      // After a seek the next speaker has nothing to do with the last one, so crossfading between
      // them would be inventing a transition that no audio made.
      player.addEventListener('seeking', function () { snapNext = true; lastRow = null; });

      function syncTime() {
        timeEl.textContent = mmss(player.currentTime) + ' / ' + mmss(player.duration);
      }

      transport.addEventListener('click', function () {
        if (player.paused) { connect(); player.play(); } else { player.pause(); }
      });
      player.addEventListener('play', function () {
        // The glyph is a SHAPE swap, not a colour swap, so the icon can never disagree with the
        // audio. It writes into #transport-mark rather than the button, because the button now also
        // contains the orb's lava layers and innerHTML on it would delete them.
        if (mark) mark.innerHTML = '&#10073;&#10073;';
        transport.setAttribute('aria-label', 'Pause');
        connect();
        if (orb) orb.setLive(true);
        followSpeaker();
      });
      player.addEventListener('pause', function () {
        if (mark) mark.innerHTML = '&#9654;';
        transport.setAttribute('aria-label', 'Play');
        if (orb) orb.setLive(false);          // holds its pose; the envelope goes to zero
      });
      player.addEventListener('timeupdate', function () { syncTime(); followSpeaker(); });
      player.addEventListener('loadedmetadata', syncTime);
      syncTime();
    })();

    // --- follow the words while playing -------------------------------------------------------
    // Scroll the speaking line into view, but yield to the reader: any manual scroll suspends this
    // for a few seconds, otherwise the page would drag them back every time they looked ahead.
    (function () {
      var suspendUntil = 0;
      var lastEl = null;
      ['wheel', 'touchmove', 'keydown'].forEach(function (evt) {
        window.addEventListener(evt, function () { suspendUntil = Date.now() + 5000; },
                                { passive: true });
      });
      player.addEventListener('timeupdate', function () {
        if (player.paused || Date.now() < suspendUntil) return;
        var el = document.querySelector('.seg.active');
        if (!el || el === lastEl) return;
        lastEl = el;
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
    })();

    // --- tabs ---
    var tabs = Array.prototype.slice.call(document.querySelectorAll('.tab'));
    var cta = document.getElementById('recast-cta');
    var backBtn = document.getElementById('back-to-play');
    tabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        var name = tab.getAttribute('data-tab');
        document.getElementById('panel-play').hidden = name !== 'play';
        document.getElementById('panel-recast').hidden = name !== 'recast';
        // Swap the console's own button rather than showing both: on the recast panel the call to
        // action has already been taken, so what you need there is the way back.
        if (cta) cta.hidden = name === 'recast';
        if (backBtn) backBtn.hidden = name !== 'recast';
      });
    });

    // --- recast tab: two roles (Showrunner, Guest host) over the Flux catalog ------------------
    //
    // The show is two-person as of 2026-08-20, so this is two rows, not one per script slot. The
    // ROLE labels come from voices.json rather than a literal here, and the role IDS stay the
    // internal slot names (`anchor`, `cohost`) because that is what `desk=` says in every
    // script.json on disk and what /api/recast validates.
    //
    // Three rules the page has to make VISIBLE, not merely obey:
    //   - Flux only: voices.json now publishes one family, so there is nothing else to offer.
    //   - never the same voice twice: the voice a seat holds is DISABLED in the other seat's
    //     select and relabelled "(taken by the Showrunner)". Disabled + words, never colour.
    //   - a role this episode does not have, and any slot the two roles cannot reach, is stated
    //     in the row and in the coverage note under the table.
    var slotsEl = document.getElementById('slots');    // a <tbody>
    var preview = document.getElementById('preview');
    var statusEl = document.getElementById('recast-status');
    var coverageEl = document.getElementById('recast-coverage');
    var cmdEl = document.getElementById('recast-cmd');

    var ROLES = (voicesDoc.roles || []).length
      ? voicesDoc.roles
      : [{ id: 'anchor', label: 'Showrunner' }, { id: 'cohost', label: 'Guest host' }];

    // Mirrors hn_radio/recast.py's SLOT_LABELS, for naming an absorbed seat in the notice.
    var SLOT_LABEL = {
      anchor: 'Showrunner', cohost: 'Guest host', ai: 'AI desk', maker: 'Maker desk',
      security: 'Security desk', drama: 'Comment theater', guest: 'Quoted comments'
    };
    function slotLabel(slot) { return SLOT_LABEL[slot] || slot; }

    // `vname` only covers voices voices.json still OFFERS, and several episodes on disk lead with
    // a voice that has since been retired, so a lookup there returns nothing for them. The script
    // itself records the name: `speaker_key` on that voice's own (non-quote) lines. Falling back
    // to it means the page says "Priya" where the episode says Priya, instead of printing an id.
    var nameOfVoice = {};
    segments.forEach(function (seg) {
      if (seg.role === 'commenter' || !seg.voice_id || !seg.speaker_key) return;
      if (!(seg.voice_id in nameOfVoice)) nameOfVoice[seg.voice_id] = seg.speaker_key;
    });
    function voiceLabel(vid) { return vname[vid] || nameOfVoice[vid] || vid; }

    // recast.role_of, in the browser. The two must agree: the page decides what to SHOW and the
    // server decides what to render, and a disagreement is a page that promises the wrong result.
    var anchorRole = ROLES[0].id, cohostRole = (ROLES[1] || ROLES[0]).id;
    var anchorVoice = '';
    segments.forEach(function (seg) {
      if (!anchorVoice && slotFor(seg) === anchorRole && seg.voice_id) anchorVoice = seg.voice_id;
    });
    function roleOf(seg) {
      if (slotFor(seg) === anchorRole) return anchorRole;
      if (seg.role === 'commenter' && anchorVoice && seg.voice_id === anchorVoice) return anchorRole;
      return cohostRole;
    }

    // Per role, the (slot, voice) pairs it covers, first appearance first. Same as
    // recast.role_coverage: the first pair is what the "Currently" column shows, and a role that
    // covers more than one is a role taking over seats the old show had, which has to be said.
    var coverage = {};
    ROLES.forEach(function (r) { coverage[r.id] = []; });
    segments.forEach(function (seg) {
      if (!slotFor(seg) || !seg.voice_id) return;
      var list = coverage[roleOf(seg)];
      var key = slotFor(seg) + '\u0000' + seg.voice_id;
      if (list.indexOf(key) === -1) list.push(key);
    });
    ROLES.forEach(function (r) {
      coverage[r.id] = coverage[r.id].map(function (k) {
        var parts = k.split('\u0000');
        return { slot: parts[0], voice: parts[1] };
      });
    });

    var voiceList = voicesDoc.voices || [];
    var famOf = {};
    voiceList.forEach(function (v) { famOf[v.id] = v.family; });
    function familyLabel(fam) {
      var f = (voicesDoc.families || []).filter(function (x) { return x.id === fam; })[0];
      return f ? f.label : (fam || '');
    }
    // Built as real <option> nodes, not an innerHTML string, because the same-voice rule has to
    // toggle `disabled` and rewrite the text on individual options after every change.
    function buildOptions(sel) {
      var made = [];
      voiceList.forEach(function (v) {
        var o = document.createElement('option');
        o.value = v.id;
        // Not every Flux voice has a published description; omit the dash when it is empty.
        o.setAttribute('data-label', v.note ? v.name + ', ' + v.note : v.name);
        o.textContent = o.getAttribute('data-label');
        sel.appendChild(o);
        made.push(o);
      });
      return made;
    }

    var rows = {};
    ROLES.forEach(function (role) {
      var cover = coverage[role.id] || [];
      var current = cover.length ? cover[0].voice : '';
      var tr = document.createElement('tr'); tr.className = 'slot-row';
      var tdRole = document.createElement('td'); tdRole.className = 'c-slot'; tdRole.textContent = role.label;

      var tdCur = document.createElement('td'); tdCur.className = 'c-current';
      var vn = document.createElement('span'); vn.className = 'vn';
      vn.textContent = current ? voiceLabel(current) : 'no lines in this episode';
      tdCur.appendChild(vn);
      if (current) {
        var fam = document.createElement('span'); fam.className = 'fam'; fam.textContent = familyLabel(famOf[current]);
        var curSamp = document.createElement('button'); curSamp.className = 'btn'; curSamp.type = 'button';
        curSamp.textContent = '▶';
        curSamp.title = 'Sample ' + voiceLabel(current) + ', the current voice';
        curSamp.setAttribute('aria-label', curSamp.title);
        curSamp.addEventListener('click', function () { preview.src = '/episodes/samples/' + current + '.wav'; preview.play(); });
        tdCur.appendChild(document.createTextNode(' ')); tdCur.appendChild(fam);
        tdCur.appendChild(document.createTextNode(' ')); tdCur.appendChild(curSamp);
      }

      var tdSel = document.createElement('td'); tdSel.className = 'c-recast';
      var sel = document.createElement('select');
      sel.setAttribute('data-slot', role.id);
      sel.setAttribute('aria-label', 'Recast the ' + role.label);
      var opts = buildOptions(sel);
      tdSel.appendChild(sel);
      var tdSamp = document.createElement('td'); tdSamp.className = 'c-sample';
      var tdMark = document.createElement('td'); tdMark.className = 'c-mark';
      // The row marker is TEXT. `.slot-row.changed` also tints the row, but the tint is redundant
      // with this cell, never the only signal that a row will re-render.
      var mark = document.createElement('span'); mark.className = 'mark';
      tdMark.appendChild(mark);

      if (current) {
        // The current voice may not be OFFERED: it was retired by ear (Priya and Marcus lead
        // several episodes on disk), or the episode was rendered against a catalog this page is
        // not being served. Either way `sel.value = <not an option>` is a SILENT no-op that
        // leaves option 0 selected, so the page would have shown a voice the episode does not
        // use and called it current.
        //
        // Added back as a disabled option, worded for what the page can actually verify: it is
        // not in the catalog being offered. Saying "retired" would claim more than voices.json
        // can support.
        if (!opts.some(function (o) { return o.value === current; })) {
          var gone = document.createElement('option');
          gone.value = current;
          gone.disabled = true;
          gone.setAttribute('data-gone', '1');
          gone.setAttribute('data-label',
            voiceLabel(current) + ', this episode\'s voice, not in the catalog now');
          gone.textContent = gone.getAttribute('data-label');
          sel.insertBefore(gone, sel.firstChild);
          opts.unshift(gone);
        }
        sel.value = current;
        var samp = document.createElement('button'); samp.className = 'btn'; samp.type = 'button';
        samp.textContent = '▶';
        samp.title = 'Sample the selected voice';
        samp.setAttribute('aria-label', 'Sample the voice selected for the ' + role.label);
        samp.addEventListener('click', function () { preview.src = '/episodes/samples/' + sel.value + '.wav'; preview.play(); });
        tdSamp.appendChild(samp);
        sel.addEventListener('change', refresh);
      } else {
        // No lines to recast, so no choice to offer. Disable rather than hide: a missing row
        // reads as a page that forgot the role, a disabled one with a reason reads as an answer.
        sel.disabled = true;
        opts.forEach(function (o) { o.disabled = true; });
        mark.textContent = 'no lines to recast';
      }

      tr.appendChild(tdRole); tr.appendChild(tdCur); tr.appendChild(tdSel);
      tr.appendChild(tdSamp); tr.appendChild(tdMark);
      slotsEl.appendChild(tr);
      rows[role.id] = { tr: tr, sel: sel, opts: opts, mark: mark, current: current || '' };
    });

    var live = ROLES.filter(function (r) { return rows[r.id].current; });

    function changed() {
      return live.filter(function (r) { return rows[r.id].sel.value !== rows[r.id].current; });
    }
    function mapping() {
      var m = {};
      changed().forEach(function (r) { m[r.id] = rows[r.id].sel.value; });
      return m;
    }

    /** Mark every voice held by another seat as taken, and say which seat holds it.
     *
     * `disabled` is what actually prevents the choice (a disabled <option> cannot be selected by
     * mouse or keyboard); the "(taken by X)" suffix is what tells a reader why. Neither is a
     * colour, which is the requirement: the state has to survive being read in greyscale. */
    function markTaken() {
      live.forEach(function (r) {
        var others = {};
        live.forEach(function (o) { if (o.id !== r.id) others[rows[o.id].sel.value] = o.label; });
        rows[r.id].opts.forEach(function (o) {
          var holder = others[o.value];
          var taken = !!holder && o.value !== rows[r.id].sel.value;
          // A retired voice stays disabled whatever else is going on: it is on the row because
          // the episode uses it, not because it is a choice.
          o.disabled = taken || o.getAttribute('data-gone') === '1';
          o.textContent = o.getAttribute('data-label') + (taken ? ' (taken by the ' + holder + ')' : '');
        });
      });
    }

    function refresh() {
      markTaken();
      live.forEach(function (r) {
        var row = rows[r.id];
        var isChanged = row.sel.value !== row.current;
        row.tr.classList.toggle('changed', isChanged);
        row.mark.textContent = isChanged ? 'will re-render' : 'unchanged';
      });
      var ch = changed();
      // A collision can only happen if the episode's own script already had one voice on both
      // seats, which the cast invariant forbids. Handled anyway, because the endpoint refuses it
      // and a button that fails on submit is exactly what markTaken exists to avoid.
      var values = live.map(function (r) { return rows[r.id].sel.value; });
      var clash = values.length > 1 && values[0] === values[1];
      cmdEl.textContent = ch.length
        ? 'python -m hn_radio.recast ' + id + ' \\\n    ' + ch.map(function (r) { return '--' + r.id + ' ' + rows[r.id].sel.value; }).join(' \\\n    ')
        : '# pick a different voice for the Showrunner or the Guest host';
      var runBtn = document.getElementById('recast-run');
      if (runBtn) runBtn.disabled = clash;
      statusEl.textContent = clash
        ? 'The Showrunner and the Guest host cannot use the same voice. Change one of them.'
        : (ch.length ? (ch.length === 1 ? '1 role will change' : ch.length + ' roles will change') : '');
    }

    // Say what a role is taking over, when it is taking over more than its own seat. This is the
    // whole archive right now: every episode on disk predates the two-person format, so the Guest
    // host absorbs the themed desks and the quoted comments. Recasting reduces the old episode to
    // two voices, which is complete but is a change to its SHAPE, so it is stated before the click
    // rather than discovered by listening to the result.
    // recast.role_takeovers, in the browser. A role covering two slots is not by itself a
    // takeover: in the current show the host reads some of the quotes, so her coverage is
    // `anchor` plus `guest` ON HER OWN VOICE. What counts is a seat the two-person show does not
    // have -- a themed desk, or a quote that had its own separate voice.
    var takeovers = ROLES.filter(function (r) {
      var pairs = coverage[r.id] || [];
      if (!pairs.length) return false;
      var lead = pairs[0].voice;
      return pairs.some(function (c) { return c.slot !== r.id && c.voice !== lead; });
    })
      .map(function (r) {
        // Grouped by slot, so two commenters do not read as "Quoted comments (Brooke),
        // Quoted comments (Marcelo)". One seat, the voices that sat in it.
        var order = [], bySlot = {};
        coverage[r.id].forEach(function (c) {
          if (!(c.slot in bySlot)) { bySlot[c.slot] = []; order.push(c.slot); }
          bySlot[c.slot].push(voiceLabel(c.voice));
        });
        return r.label + ' takes over ' + order.map(function (slot) {
          // A quoted comment's voice is not a character anyone knows by name, and its
          // `speaker_key` is the commenter's HN username, so there is no name to print and the
          // raw id is noise. Count them instead; the desks get named.
          if (slot === 'guest') {
            var n = bySlot[slot].length;
            return slotLabel(slot) + ' (' + n + (n === 1 ? ' voice' : ' voices') + ')';
          }
          return slotLabel(slot) + ' (' + bySlot[slot].join(', ') + ')';
        }).join(', ');
      });
    if (takeovers.length) {
      coverageEl.hidden = false;
      coverageEl.textContent = 'This episode was made before the show went two-person, so one role '
        + 'covers several of its old seats: ' + takeovers.join('; ')
        + '. Recasting reduces it to two voices, and every line is re-rendered.';
    }

    function runRecast(m) {
      if (!m || !Object.keys(m).length) { statusEl.textContent = 'No changes to render. Pick a different voice for one of the two roles first.'; return; }
      var runBtn = document.getElementById('recast-run');
      if (runBtn) runBtn.disabled = true;
      statusEl.textContent = 'Rendering with Deepgram… (~30–60s; only changed voices re-render)';
      fetch('/api/recast', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ episode_id: id, mapping: m })
      }).then(function (r) {
        // A refusal that carries a `detail` is a STATED reason -- 400 for the cast itself (same
        // voice, not a Flux voice, retired), 429 from backend/limits.py when a render is already
        // running or the caller is out of quota. Show it. The old code showed "no backend
        // reachable" for every non-200, which blamed the server for a bad ask and told a
        // rate-limited visitor the app was down.
        //
        // Flagged on the error rather than sniffed from its message: a rejected fetch arrives as
        // an Error too, and matching on text put a raw "NetworkError" in front of the reader.
        if (!r.ok) {
          return r.json().catch(function () { return {}; }).then(function (b) {
            var err = new Error(b.detail || 'The server refused that cast (HTTP ' + r.status + ').');
            err.stated = true;
            throw err;
          });
        }
        return r.json();
      })
        .then(function (out) { statusEl.textContent = 'Done. Opening the recast…'; location.href = 'episode.html?id=' + encodeURIComponent(out.id); })
        .catch(function (e) {
          if (runBtn) runBtn.disabled = false;
          statusEl.textContent = (e && e.stated)
            ? e.message
            : 'No backend reachable. Run the command below instead.';
        });
    }

    document.getElementById('recast-reset').addEventListener('click', function () {
      live.forEach(function (r) { rows[r.id].sel.value = rows[r.id].current; }); refresh();
    });
    document.getElementById('recast-stop').addEventListener('click', function () { preview.pause(); player.pause(); });
    document.getElementById('recast-run').addEventListener('click', function () { runRecast(mapping()); });
    refresh();
  }
})();
