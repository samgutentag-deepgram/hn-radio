// HN Radio episode page: the player, the chapter strip, the cost receipt and the transcript.
// Reads the pipeline JSON. No build step: this is a native ES module, loaded with
// <script type="module">, so `import` works without a bundler.
//
// It used to carry a Recast panel as a second tab. Removed 2026-09-16; see the note at the
// bottom of `render` for what that was and what its one surviving dependency is.
import { mmss, usd, count } from './format.js';

(function () {
  // Two ways in, and both are real. `episode.html?id=<id>` is the page the site links to;
  // `/e/<id>` is the same page served by backend/app.py with this episode's OG tags in the head,
  // and it is the short form printed on the share cards for someone retyping it off an image.
  // The path is read here rather than by a redirect so the unfurler that reads those tags and the
  // reader who lands on the page get the same URL.
  var params = new URLSearchParams(location.search);
  var path = location.pathname.match(/^\/e\/([^/]+)\/?$/);
  var id = params.get('id') || (path ? decodeURIComponent(path[1]) : null);
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

  // The cost receipt. Four figures and the caveats, from episode.json's `cost` block.
  //
  // IT DOES NOT COMPUTE ANYTHING. Every number here was worked out by hn_radio/pricing.py and
  // written into episode.json, and that is the whole design: the show SAYS this figure in its
  // outro, so a second implementation of the arithmetic in the browser is a second answer to a
  // question the audio has already answered out loud. The page's job is formatting.
  //
  // Hides itself when the block is missing or has no characters in it. Every episode on the
  // volume is priced by `pricing.backfill` on app startup, so absence means something went wrong
  // reading that episode -- and an empty receipt is honest about that in a way a row of zeros,
  // or an invented estimate, is not.
  function renderCost(ep) {
    var c = ep.cost || {};
    var host = document.getElementById('cost');
    if (!c.characters) return;               // stays [hidden]: no figures to show

    var figures = [
      // Four decimals, not two. Episodes land within a couple of cents of each other, so the
      // cent-rounded figure the outro speaks is exactly the precision that makes two episodes
      // look identical. The receipt is where the digits belong.
      { cls: 'cost-figure-money', value: usd(c.usd, 4), label: 'of Deepgram Flux TTS' },
      { value: count(c.characters), label: 'characters of script' },
      { value: usd(c.rate_usd_per_1k, 4) + '/1k', label: rateLabel(c.plan) },
      { value: count(c.episodes_per_credit),
        label: 'episodes on the ' + usd(c.credit_usd, 0) + ' signup credit' }
    ];

    var grid = document.getElementById('cost-figures');
    figures.forEach(function (f) {
      var cell = document.createElement('div');
      cell.className = 'cost-figure' + (f.cls ? ' ' + f.cls : '');
      var v = document.createElement('span'); v.className = 'cost-value'; v.textContent = f.value;
      var l = document.createElement('span'); l.className = 'cost-label'; l.textContent = f.label;
      cell.appendChild(v); cell.appendChild(l);
      grid.appendChild(cell);
    });

    // Written out rather than assembled from fragments, because the three caveats are the part a
    // reader is owed: this is the TTS line item and not the cost of the show, it is list price
    // with the credit-match promotion left out, and a cache hit on a recast is not a second bill.
    // `hn_radio/pricing.py`'s module docstring is the long version of this paragraph.
    var fine = document.getElementById('cost-fine');
    fine.textContent =
      'Text-to-speech only, at list price: ' + usd(c.rate_usd_per_1k, 4) + ' per 1,000 characters'
      + ' on the ' + rateLabel(c.plan) + ' plan, as published on ' + (c.pricing_as_of || 'the pricing page')
      + '. It excludes the model that writes the script, and it leaves out the Flux credit match,'
      + ' which would halve it until the promotion ends. ';
    if (c.billed_characters != null && c.billed_characters !== c.characters) {
      // Only a custom build or a CLI recast reaches this, and in practice not even those: the
      // per-segment audio cache they reused was deleted when the volume filled up, so a re-render
      // now pays for every line. Kept because it reports what was billed rather than asserting a
      // reuse rate. Saying "this run cost less" without saying WHY would read as an error in the
      // bigger number directly above it.
      fine.textContent +=
        'This particular render billed only ' + count(c.billed_characters) + ' characters ('
        + usd(c.billed_usd, 4) + '): the rest came from the per-segment audio cache, because those'
        + ' lines kept both their words and their voice. ';
    }
    var link = document.createElement('a');
    link.href = c.pricing_url || 'https://deepgram.com/pricing';
    link.target = '_blank'; link.rel = 'noopener';
    link.textContent = 'Deepgram pricing';
    fine.appendChild(link);

    host.hidden = false;
  }

  // The plan's name in the words the pricing page uses. A map and not a capitalize(), because
  // "Payg" is not a thing anyone calls it and an unknown id should fall through as itself rather
  // than be dressed up as a plan that exists.
  function rateLabel(plan) {
    return { payg: 'pay-as-you-go', growth: 'Growth' }[plan] || (plan || 'list');
  }

  function render(ep, segments, voicesDoc, chaptersDoc) {
    document.title = 'Hacker News Radio: ' + ep.title;
    document.getElementById('title').textContent = ep.title;
    // The episode's own art. Shown only once it loads; click for the full-size image.
    var art = document.getElementById('ep-art'), artLink = document.getElementById('ep-art-link');
    if (art && artLink) {
      art.addEventListener('load', function () { artLink.hidden = false; });
      art.alt = 'Episode art for ' + ep.title;
      artLink.href = base + 'art.jpg';
      art.src = base + 'art.jpg';
    }
    document.getElementById('meta').textContent =
      (ep.edition ? ep.edition + ' · ' : '') + Math.round(ep.duration_seconds) + 's · ' + (ep.generated_at || '');

    var player = document.getElementById('player');
    player.src = base + 'episode.mp3';  // chaptered MP3 (podcast-friendly, small)
    renderChaptersAndNotes(ep, chaptersDoc, player);
    renderCost(ep);

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
    // voice_id -> catalog name, so a script line's speaker is named by the voice that read it
    // rather than by a seat this build may no longer have. This is the ONLY thing the episode
    // page still needs voices.json for, now that the recast picker is gone.
    var vname = {};
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
      // The voice card beside the name: the object on its tile, or the initial on a neutral tile
      // for a voice with no official card. data-tile on the row gives it the card's rule color and
      // playhead tint (brand.css). Decorative; the name follows in text.
      if (seg.voice_id && window.DGCluster) {
        var tileKey = window.DGCluster.keyOf(seg.voice_id);
        row.setAttribute('data-tile', tileKey);
        who.insertBefore(window.DGCluster.avatar(seg.voice_id, vname[seg.voice_id] || seg.speaker_key), who.firstChild);
        who.classList.add('has-avatar');
      }
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

    // --- transport + the cast cluster as the playback visual --------------------------------
    // The play button is the episode's cast as a group bubble (web/cluster.js), host first. The
    // speaker is read off the active transcript row, so a handoff is visible as well as audible,
    // and the speaker's bubble swells with the real signal through an amplitude envelope. Paused,
    // it holds still: there is nothing to show. Visualisation must never break playback, so
    // every reference below is guarded and the audio plays if the cluster or Web Audio fails.
    (function () {
      var transport = document.getElementById('transport');
      var mark = document.getElementById('transport-mark');
      var timeEl = document.getElementById('transport-time');
      if (!transport) return;

      var analyser = null;
      var cast = [];
      segments.forEach(function (sg) {
        if (sg.voice_id && cast.indexOf(sg.voice_id) === -1) {
          if (sg.desk === 'anchor' || sg.role === 'anchor' || sg.role === 'host') cast.unshift(sg.voice_id);
          else cast.push(sg.voice_id);
        }
      });
      // Cards only, like the home page: a voice without an official card is left out of the
      // bubble (the host always stays) and gets its letter tile in the transcript instead.
      var cluster = window.DGCluster ? window.DGCluster.attach(transport, { cast: cast, cardsOnly: true }) : null;

      function connect() {
        // Built on the first play, because an AudioContext created before a user gesture starts
        // suspended. MediaElementSource routes playback through the graph, so if the context were
        // left suspended the audio would be silent, not merely un-analysed.
        if (analyser) return;
        var AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return;                       // no Web Audio: transport works, the bubble holds still
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
          if (cluster) cluster.setEnvSource(window.DGCluster.envelope(analyser));
          if (ac.state === 'suspended') ac.resume();
        } catch (e) {
          analyser = null;                     // never let visualisation break playback
        }
      }

      // Who is speaking, read off the ACTIVE TRANSCRIPT ROW: the timeupdate handler above marks it,
      // and handlers fire in registration order, so .seg.active is current by the time this runs.
      var lastRow = null;
      function followSpeaker() {
        if (!cluster) return;
        var row = document.querySelector('.seg.active');
        if (!row || row === lastRow) return;
        lastRow = row;
        cluster.setSpeaker(row.getAttribute('data-voice'));
      }
      // After a seek, forget the last row so the next active one re-seats the speaker.
      player.addEventListener('seeking', function () { lastRow = null; });

      // ---- the scrub bar -----------------------------------------------------------------
      //
      // POSITION ONLY. The cast bubble is the signal, so this answers "where am I" and the bubble
      // answers "what does it sound like".
      var scrub = document.getElementById('transport-scrub');
      var bar = document.getElementById('transport-bar');
      var ticks = document.getElementById('transport-ticks');

      // Chapter boundaries as ticks. This is the reason the console's scrubber is more useful
      // than the hero's: the episode page is the one that knows where the chapters are. Placed
      // once, on metadata, because a percentage needs a duration and `duration` is NaN until
      // then; `loadedmetadata` can fire before or after this code runs, so it is also called
      // directly for a cached file that already has one.
      function placeTicks() {
        if (!ticks || !isFinite(player.duration) || player.duration <= 0) return;
        var chapters = (chaptersDoc && chaptersDoc.chapters) || [];
        ticks.innerHTML = '';
        chapters.forEach(function (c) {
          // Not the one at zero: a tick on the left edge reads as a rendering artifact rather
          // than as a chapter, and nobody needs to be told the episode starts at the start.
          if (!c.startTime) return;
          var t = document.createElement('div');
          t.className = 'transport-tick';
          t.style.left = (c.startTime / player.duration * 100) + '%';
          ticks.appendChild(t);
        });
      }

      function syncTime() {
        timeEl.textContent = mmss(player.currentTime) + ' / ' + mmss(player.duration);
        if (bar && isFinite(player.duration) && player.duration > 0) {
          var frac = Math.min(1, Math.max(0, player.currentTime / player.duration));
          bar.style.width = (frac * 100) + '%';
          if (scrub) scrub.setAttribute('aria-valuenow', Math.round(frac * 100));
        }
      }

      if (scrub) {
        // Seek from a click anywhere on the track, including the ticks, which are
        // `pointer-events: none` precisely so they cannot swallow one.
        function seekToX(clientX) {
          if (!isFinite(player.duration) || player.duration <= 0) return;
          var r = scrub.getBoundingClientRect();
          var frac = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
          player.currentTime = frac * player.duration;
          syncTime();
        }
        scrub.addEventListener('click', function (ev) { seekToX(ev.clientX); });
        // Arrows nudge, Home and End jump. `role="slider"` in the markup is a promise that these
        // work; without them it is a lie to a screen reader.
        scrub.addEventListener('keydown', function (ev) {
          if (!isFinite(player.duration) || player.duration <= 0) return;
          var step = ev.shiftKey ? 30 : 5;
          var to = null;
          if (ev.key === 'ArrowRight' || ev.key === 'ArrowUp') to = player.currentTime + step;
          else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowDown') to = player.currentTime - step;
          else if (ev.key === 'Home') to = 0;
          else if (ev.key === 'End') to = player.duration;
          if (to === null) return;
          ev.preventDefault();
          player.currentTime = Math.min(player.duration, Math.max(0, to));
          syncTime();
        });
      }

      transport.addEventListener('click', function () {
        if (player.paused) { connect(); player.play(); } else { player.pause(); }
      });
      player.addEventListener('play', function () {
        // The glyph is a SHAPE swap, not a colour swap, so the icon can never disagree with the
        // audio. It writes into #transport-mark rather than the button, because the button also
        // holds the cast bubble and innerHTML on it would delete it.
        if (mark) mark.innerHTML = '&#10073;&#10073;';
        transport.setAttribute('aria-label', 'Pause');
        connect();
        if (cluster) cluster.setLive(true);
        followSpeaker();
      });
      player.addEventListener('pause', function () {
        if (mark) mark.innerHTML = '&#9654;';
        transport.setAttribute('aria-label', 'Play');
        if (cluster) cluster.setLive(false);          // holds still; the envelope goes to zero
      });
      player.addEventListener('timeupdate', function () { syncTime(); followSpeaker(); });
      player.addEventListener('loadedmetadata', function () { syncTime(); placeTicks(); });
      // `seeking` as well as `timeupdate`: dragging past the end of the file fires no timeupdate,
      // so the bar would sit where the last one left it.
      player.addEventListener('seeked', syncTime);
      syncTime();
      placeTicks();
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

    // THE RECAST PANEL, THE TAB SWITCHER AND THE VOICE PICKER WERE ALL DELETED HERE on
    // 2026-09-16, about 320 lines of it. Sam: "remove all of the recast your own episode
    // features. I still want the meet the cast page, but I don't need any of the features around
    // recasting."
    //
    // What went with it, so nobody goes looking: the two-role picker (Showrunner and Guest host)
    // over the Flux catalog, the per-row voice samples, the legacy-coverage notice that warned a
    // five-desk episode would come back as two voices, the copy-able CLI fallback for when no
    // backend is running, and the `POST /api/recast` call.
    //
    // The tab switcher went too, and that is why `#panel-play` is no longer hidden or shown by
    // anything: there is one panel now, so it is just the page. If a second panel ever returns,
    // the thing to know is that the switcher keyed off `.tab` and `data-tab`, and that the
    // console swapped its own call-to-action button for a "back to the script" button rather
    // than showing both.
    //
    // The episode page still reads `voices.json`, for one thing only: `voicesDoc.voices` is how a
    // `voice_id` on a script line becomes a name in the transcript (see `vname` above). That is
    // not a recast feature and the file is still published for it.
  }
})();
