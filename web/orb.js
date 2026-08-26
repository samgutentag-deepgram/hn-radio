/* ============================================================================
   THE MOLTEN ORB, ported from talk.deepgram.com.

   Upstream this is <molten-orb>, a Lit element in the app behind that site. This
   is the same geometry and the same motion in dependency-free vanilla JS: no
   framework, no build step, no custom element, one shared animation loop for the
   whole page.

   WHAT IT IS. A 50% circle over a near-black ground with three blurred ellipses
   drifting inside it, each painted a different shade of the speaker's palette.
   Every number below (the ellipse boxes, the blur radii, the sine coefficients,
   the dt clamp) is copied from the reference implementation rather than tuned by
   eye, so this looks like the same object rather than like an homage. Where a
   constant carries a reason, the reason is recorded beside it.

   WHAT WAS ADDED, AND WHY.

   1. ONE TICKER FOR THE PAGE. Upstream every orb owns a requestAnimationFrame
      loop, which is fine when a page has one orb. cast.html has 30 -- counted
      in the browser; this used to say 34, from before the cast was recast. A
      loop per orb means 30 callbacks a frame all doing the same clock
      arithmetic, so there is a single ticker here and orbs register with it.

   2. ORBS ONLY MOVE WHEN MOVING IS WORTH IT. Three blurred, promoted layers per
      orb is real GPU work: 30 orbs is 90 composited blurs. An orb is driven by
      the ticker only while it is INTERSECTING THE VIEWPORT and LIVE (driven by
      audio, hovered, or focused). Otherwise it holds one pose, seeded from its
      own phase so a grid of them does not read as a grid of identical shapes,
      and its layers are not `will-change`-promoted at all. It still drifts, on
      the CSS swirl in point 4 -- "not driven by the ticker" and "not moving" are
      no longer the same thing. See the .is-moving rules in brand.css block 5.

   3. AN ENVELOPE FROM THE REAL SIGNAL. `env` is the reference's own hook: it
      lifts the orb and stretches it vertically. Upstream it comes from the agent
      and user volume of a live call; here it comes from an AnalyserNode reading
      the episode audio, which is what makes the orb a waveform replacement
      rather than an ornament. envelope() below is that measurement, shared by
      both pages so they cannot drift apart.

   4. AN IDLE SWIRL THIS FILE DOES NOT RUN. An orb with nothing playing drifts
      on a slow ambient cycle, and that cycle is CSS: three keyframe animations
      on the standalone `translate` and `rotate` properties, which COMPOSE with
      the `transform` this file writes instead of replacing it. So the swirl
      rides on top of the frozen pose applyMolten leaves, the compositor runs it
      with no main thread involvement, and the rAF loop below still does not
      start for an idle orb. That last part is the whole reason it is not done
      here: letting the ticker run at env = 0 would restyle three blurred layers
      per orb per frame, which on cast.html is 90 of them, and point 2 above
      exists to prevent exactly that. All this file contributes is the
      .is-ambient class, off the `ambient` option, because whether a context
      should idle in motion is that context's call. See brand.css block 5.

   5. A CROSSFADE BETWEEN SPEAKERS. Upstream the shades are set by the caller and
      snap. Here the speaker changes every few seconds, and a hard cut that often
      reads as a glitch, so the four palette values tween. Duration comes from
      --dg-anim-tint, which resolves to the pack's 280ms base and to 0s under
      prefers-reduced-motion.

   ACCESSIBILITY, AND THIS IS NOT NEGOTIABLE. The orb's colour is decorative
   reinforcement and never information. Who is speaking is carried in TEXT at all
   times -- the transcript names every row, the console names the episode, the
   cast cards print the voice id -- and nothing here removes or replaces a name.
   A reader with no colour perception loses nothing but decoration. Under
   prefers-reduced-motion the motion freezes and the pose stays, so the orb
   degrades to a still object rather than disappearing.

   Loaded as a classic script (not a module) because three callers need it: a
   module (app.js), and two inline page scripts (index.html, cast.html).
   ========================================================================= */
(function () {
  'use strict'

  var reduced = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)

  /* ---- geometry, verbatim from the reference stylesheet ------------------
     The lava layer is a fixed 144px box scaled to the orb, so these stay
     integers at every size and the shapes keep their proportions. */
  var LAVA_BOX = 144
  var ELLIPSES = [
    { left: 4, top: 72, w: 135, h: 75, blur: 9 },
    { left: 9, top: 60, w: 124, h: 69, blur: 8 },
    { left: 40, top: 66, w: 66, h: 37, blur: 7 }
  ]
  // Above this pixel size the reference swaps to a wider, softer inset glow.
  var LARGE_AT = 240
  // The palette properties an orb reads, in the order the widget stacks the
  // lava layers: light on the bottom ellipse, mid, then deep on the smallest.
  var SHADE_VARS = ['--v-l', '--v-m', '--v-d']

  /* ========================================================================
     THE PALETTE PROBE.

     Read the speaker's shades OFF THE STYLESHEET rather than keeping a second
     copy of brand.css block 1 in JS. There is precedent and it is a scar:
     index.html used to carry a `DESK_COLOR` literal, it drifted out of sync
     with the stylesheet, and it was replaced with exactly this probe.

     The probe is an offscreen span that takes the same data-voice / data-desk
     attributes a transcript row takes, so getComputedStyle hands back precisely
     the values that row would paint with, including block 1's deliberate
     voice-beats-seat source ordering. Cached per (theme, voice, seat) because a
     getComputedStyle read forces style resolution and this used to be called
     once a frame.
     ==================================================================== */
  var probe = null
  var paletteCache = {}

  function ensureProbe() {
    if (probe) return probe
    probe = document.createElement('span')
    probe.setAttribute('aria-hidden', 'true')
    probe.style.cssText = 'position:absolute;left:-9999px;top:0;width:0;height:0'
    document.body.appendChild(probe)
    return probe
  }

  /** The four values an orb needs for a voice: three shades and the glow triple.
   *  Any of them may come back empty, which is honest: a voice with no entry in
   *  block 1 leaves --v-* undeclared, and the caller's CSS fallback is then the
   *  right answer. */
  function paletteFor(voice, desk) {
    var theme = document.documentElement.getAttribute('data-theme') || ''
    var key = theme + '|' + (voice || '') + '|' + (desk || '')
    if (paletteCache[key]) return paletteCache[key]
    var p = ensureProbe()
    if (voice) p.setAttribute('data-voice', voice); else p.removeAttribute('data-voice')
    if (desk) p.setAttribute('data-desk', desk); else p.removeAttribute('data-desk')
    var cs = getComputedStyle(p)
    var out = {
      l: cs.getPropertyValue('--v-l').trim(),
      m: cs.getPropertyValue('--v-m').trim(),
      d: cs.getPropertyValue('--v-d').trim(),
      g: cs.getPropertyValue('--v-g').trim()
    }
    paletteCache[key] = out
    return out
  }

  /* ---- colour arithmetic, only enough of it to tween ---------------------
     Block 1 declares the shades as hex and the glow as a bare "r,g,b" triple,
     which is the whole set of forms that can arrive here. */
  function parseRgb(s) {
    if (!s) return null
    s = String(s).trim()
    if (s.charAt(0) === '#') {
      var h = s.slice(1)
      if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2]
      if (h.length !== 6) return null
      var n = parseInt(h, 16)
      if (isNaN(n)) return null
      return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
    }
    var parts = s.replace(/^rgba?\(/, '').replace(/\)$/, '').split(/[\s,/]+/)
    if (parts.length < 3) return null
    var r = parseFloat(parts[0]), g = parseFloat(parts[1]), b = parseFloat(parts[2])
    if (isNaN(r) || isNaN(g) || isNaN(b)) return null
    return [r, g, b]
  }

  function lerpRgb(a, b, k) {
    return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k]
  }

  function cssRgb(c) {
    return 'rgb(' + Math.round(c[0]) + ',' + Math.round(c[1]) + ',' + Math.round(c[2]) + ')'
  }

  function cssTriple(c) {
    return Math.round(c[0]) + ',' + Math.round(c[1]) + ',' + Math.round(c[2])
  }

  /** Crossfade length, read from the stylesheet so the pack owns the number.
   *  --dg-anim-tint resolves to --dg-motion-base (280ms), and to 0s under
   *  prefers-reduced-motion, which is the correct degradation: a reader who
   *  asked for no animation gets the new colour immediately instead of a fade. */
  function tintMs() {
    var v = getComputedStyle(document.documentElement).getPropertyValue('--dg-anim-tint').trim()
    if (!v) return 280
    var n = parseFloat(v)
    if (isNaN(n)) return 280
    return v.indexOf('ms') >= 0 ? n : n * 1000
  }

  /* ========================================================================
     THE ENVELOPE.

     A real amplitude measure, not a spectrum bin. RMS over the TIME-DOMAIN
     window is the honest one: getByteFrequencyData already carries the
     analyser's own smoothing and its magnitudes depend on the FFT size, so two
     pages with different fftSize would breathe at different depths off the same
     audio. RMS over samples does not care.

     NORMALISATION. rms is 0..1 on a signal already normalised to -1..1. Speech
     at broadcast level sits around 0.05 to 0.15 RMS; a loud vowel reaches about
     0.22. So 0.22 is full scale: normal speech then uses most of the range and a
     shout pins it, rather than an amplitude of 1.0 (a full-scale square wave)
     being full scale and every syllable living in the bottom tenth.

     SMOOTHING. An asymmetric one-pole filter: quick to rise so a consonant
     actually lands, slow to fall so the orb rides the phrase instead of
     strobing between syllables. The coefficients are re-derived against dt each
     frame from a 60 Hz reference, so the shape of the breath is the same on a
     120 Hz display as on a 60 Hz one.
     ==================================================================== */
  var ENV_FULL_SCALE = 0.22
  var ENV_ATTACK = 0.4
  var ENV_RELEASE = 0.12

  function envelope(analyser) {
    var buf = new Uint8Array(analyser.fftSize)
    var level = 0
    return function (dt) {
      try {
        analyser.getByteTimeDomainData(buf)
      } catch (e) {
        return level                        // a dead analyser must not stop the orb
      }
      var sum = 0
      for (var i = 0; i < buf.length; i++) {
        var d = (buf[i] - 128) / 128
        sum += d * d
      }
      var want = Math.min(1, Math.sqrt(sum / buf.length) / ENV_FULL_SCALE)
      var k = want > level ? ENV_ATTACK : ENV_RELEASE
      // Clamped at 4 reference frames so a long stall (a background tab coming
      // back) lands on the new level rather than overshooting past it.
      k = 1 - Math.pow(1 - k, Math.min(4, Math.max(0, dt) * 60))
      level += (want - level) * k
      return level
    }
  }

  /* ========================================================================
     THE ORB.
     ==================================================================== */

  var orbs = []
  var raf = 0
  var lastFrame = 0

  function Orb(el, opts) {
    opts = opts || {}
    this.el = el
    // Upstream's `phase` property. Offsetting each orb's clock is what keeps a
    // wall of orbs from moving in lockstep, and it also gives every idle orb a
    // different frozen pose.
    this.phase = typeof opts.phase === 'number' ? opts.phase : 0
    this.t = 0
    this.env = 0
    this.envSource = opts.envSource || null
    // TWO independent reasons to be live, and they must not share a flag. They used to: one
    // boolean, set both by setLive() and by the hover/focus handlers below. Pressing the transport
    // focused the button (live), and the very next blur set it straight back to false, so the orb
    // stopped moving one interaction into playback while the audio kept going. Found in the
    // browser, which is the only place it was ever going to show up.
    this.liveAudio = false
    this.liveHover = false
    this.visible = true
    this.moving = false
    this.scale = 1
    this.tint = null                        // { from, to, start, ms } while crossfading
    this.shades = null                      // current rgb triples, once tinted

    var lava = document.createElement('span')
    lava.className = 'orb-lava'
    lava.setAttribute('aria-hidden', 'true')
    this.ells = ELLIPSES.map(function (_, i) {
      var e = document.createElement('span')
      e.className = 'orb-ell orb-ell' + i
      lava.appendChild(e)
      return e
    })
    // First child, so anything the orb already contains (a play glyph, a pause
    // pair) keeps painting on top of the lava without a z-index anywhere.
    el.insertBefore(lava, el.firstChild)
    this.lava = lava
    el.classList.add('is-molten')
    // The idle swirl, opt-OUT. Default on, because "an orb at rest is still
    // alive" is what the object is supposed to say everywhere it appears; a
    // caller with a reason (a page holding a wall of them, a context where the
    // orb is incidental) turns it off. Purely a class: everything the swirl
    // actually does lives in brand.css block 5 and runs on the compositor.
    if (opts.ambient !== false) el.classList.add('is-ambient')

    var self = this
    this.measure()
    if (window.ResizeObserver) {
      this.ro = new ResizeObserver(function () { self.measure() })
      this.ro.observe(el)
    } else {
      // The sizes here are rem-based, so a root font-size change is the only
      // realistic resize. A window listener covers it well enough.
      this.onResize = function () { self.measure() }
      window.addEventListener('resize', this.onResize)
    }

    // Only pay for an observer where one can help: a page with a single orb
    // (the hero, the console) can never win anything from it.
    if (window.IntersectionObserver && opts.observe !== false) {
      this.visible = false
      this.io = new IntersectionObserver(function (entries) {
        self.visible = entries[entries.length - 1].isIntersecting
        self.sync()
      }, { rootMargin: '64px' })
      this.io.observe(el)
    }

    // Hover and focus wake an orb up. This is the cast page's whole idle story:
    // one orb moves, the one under the pointer, and the other 33 hold a pose.
    if (opts.wakeOnHover !== false) {
      var wake = function () { self.liveHover = true; self.sync() },
          rest = function () { self.liveHover = false; if (!self.isLive()) self.env = 0; self.sync() }
      el.addEventListener('pointerenter', wake)
      el.addEventListener('pointerleave', rest)
      el.addEventListener('focusin', wake)
      el.addEventListener('focusout', rest)
      // The orb is sometimes the button itself, in which case focusin never
      // fires on it. focus/blur do not bubble, so both pairs are needed.
      el.addEventListener('focus', wake)
      el.addEventListener('blur', rest)
    }

    // Draw the frozen pose immediately, so an orb that never becomes live is
    // still a molten shape rather than an empty dark disc.
    this.applyMolten(this.phase, 0)
    this.sync()
  }

  /** Pixel size drives two things the stylesheet cannot compute: the lava
   *  scale (a unitless ratio against a px box, which calc() cannot produce)
   *  and the reference's large-orb shadow swap. */
  Orb.prototype.measure = function () {
    var w = this.el.offsetWidth || this.el.getBoundingClientRect().width
    if (!w) return
    this.scale = w / LAVA_BOX
    this.el.style.setProperty('--orb-lava-scale', String(this.scale))
    this.el.classList.toggle('is-large', w >= LARGE_AT)
  }

  /** The AUDIO reason to be live: this orb is showing something that is playing. */
  Orb.prototype.setLive = function (on) {
    this.liveAudio = !!on
    if (!this.isLive()) this.env = 0
    this.sync()
  }

  Orb.prototype.isLive = function () {
    return this.liveAudio || this.liveHover
  }

  Orb.prototype.setEnvSource = function (fn) {
    this.envSource = fn || null
  }

  /** Point the orb at a speaker. Reads the palette off the stylesheet and
   *  crossfades to it. `snap` skips the fade, for a seek, where the new speaker
   *  has nothing to do with the old one and easing between them is a lie. */
  Orb.prototype.setSpeaker = function (voice, desk, snap) {
    var p = paletteFor(voice, desk)
    var want = [parseRgb(p.l), parseRgb(p.m), parseRgb(p.d), parseRgb(p.g)]
    // A voice with no palette entry: leave the element's own custom properties
    // alone so the CSS fallback in block 5 keeps its say.
    if (!want[0] || !want[1] || !want[2] || !want[3]) return
    if (this.tintKey === voice + '|' + desk && this.shades) return
    this.tintKey = voice + '|' + desk
    var ms = snap ? 0 : tintMs()
    if (!this.shades || ms <= 0) {
      this.shades = want
      this.tint = null
      this.paintShades(want)
      return
    }
    this.tint = { from: this.shades.slice(), to: want, start: now(), ms: ms }
    this.sync()
  }

  Orb.prototype.paintShades = function (c) {
    for (var i = 0; i < 3; i++) this.el.style.setProperty(SHADE_VARS[i], cssRgb(c[i]))
    this.el.style.setProperty('--v-g', cssTriple(c[3]))
  }

  Orb.prototype.stepTint = function () {
    if (!this.tint) return
    var k = (now() - this.tint.start) / this.tint.ms
    if (k >= 1) {
      this.shades = this.tint.to
      this.tint = null
      this.paintShades(this.shades)
      return
    }
    if (k < 0) k = 0
    var cur = []
    for (var i = 0; i < 4; i++) cur.push(lerpRgb(this.tint.from[i], this.tint.to[i], k))
    this.shades = cur
    this.paintShades(cur)
  }

  /** True while this orb has a reason to be redrawn. Everything the ticker
   *  skips costs nothing at all, which is the point. */
  Orb.prototype.wants = function () {
    if (this.tint) return true
    if (!this.visible) return false
    if (reduced) return false               // a frozen pose needs no frames
    return this.isLive()
  }

  /** Keep the promoted-layer hint and the idle breathe in step with whether
   *  this orb is actually moving, and wake the ticker if it needs to run. */
  Orb.prototype.sync = function () {
    var m = this.visible && this.isLive() && !reduced
    if (m !== this.moving) {
      this.moving = m
      this.el.classList.toggle('is-moving', m)
      // Coming to rest, leave a settled pose rather than whatever half-formed
      // shape the last frame happened to hold.
      if (!m) this.applyMolten(this.t + this.phase, 0)
    }
    if (this.wants()) start()
  }

  /** applyMolten, from the reference, unchanged in every coefficient.
   *  `e` is the clock, `t` the audio envelope in 0..1. */
  Orb.prototype.applyMolten = function (e, t) {
    for (var r = 0; r < this.ells.length; r++) {
      var n = this.ells[r]
      // Under reduced motion every ellipse is posed at 0: the reference's own
      // behaviour, and the reason it degrades to a still orb rather than to
      // nothing. The 0.09 offset per ellipse is what stops the three layers
      // from sliding as one rigid body.
      var i = reduced ? 0 : e - r * 0.09
      var a = Math.sin(i * 0.311) * 20 + Math.sin(i * 0.829) * 10 + Math.cos(i * 0.173) * 6
      // -26 lifts the ellipses into the circle; env lifts them further, so a
      // loud syllable pushes the lava up the orb.
      var o = Math.cos(i * 0.257) * 18 + Math.sin(i * 0.691) * 9 + Math.sin(i * 0.143) * 6 - 26 - t * 10
      var s = Math.sin(i * 0.19) * 130 + Math.sin(i * 0.53) * 35
      var c = 1.15 + 0.35 * Math.sin(i * 0.47) + t * 0.1
      // The vertical scale takes almost four times the envelope of the
      // horizontal, which is what reads as the orb stretching when it speaks.
      var l = 1.15 + 0.35 * Math.sin(i * 0.613 + 2.1) + t * 0.38
      // Four independent sines driving the eight border-radius terms. This is
      // the part that reads as molten rather than as a spinning blob: the
      // outline is never an ellipse for two consecutive frames.
      var u = 50 + 22 * Math.sin(i * 0.41)
      var d = 50 + 22 * Math.sin(i * 0.57 + 1.4)
      var f = 50 + 22 * Math.sin(i * 0.33 + 2.9)
      var p = 50 + 22 * Math.sin(i * 0.71 + 4.2)
      n.style.borderRadius = u + '% ' + (100 - u) + '% ' + d + '% ' + (100 - d) + '% / ' +
                             f + '% ' + p + '% ' + (100 - p) + '% ' + (100 - f) + '%'
      n.style.transform = 'translate(' + a.toFixed(2) + 'px,' + o.toFixed(2) + 'px) rotate(' +
                          s.toFixed(2) + 'deg) scale(' + c.toFixed(3) + ',' + l.toFixed(3) + ')'
    }
  }

  // `Orb.prototype.destroy` was deleted 2026-08-22. Zero callers, no page ever removes an orb,
  // and as written it was a BROKEN teardown rather than a working one held in reserve:
  //
  //   - it never cleared `this.el._hnOrb`, so a later `attach()` short-circuited and handed back
  //     the gutted orb -- already spliced out of the tick array, with its `orb-lava` span removed;
  //   - it never removed the six wake/rest listeners the constructor adds.
  //
  // So keeping it "for a future dynamic list" would have meant keeping a trap. Re-derive it when
  // something actually needs teardown, and fix both gaps in the same change that lands the first
  // caller.

  function now() {
    return (window.performance && performance.now) ? performance.now() : Date.now()
  }

  /* ---- the one ticker ---------------------------------------------------- */
  function tick(ts) {
    raf = 0
    // The reference's clamp. A tab that was backgrounded for a minute must not
    // advance the clock by a minute and teleport the shape.
    var dt = Math.min(0.05, (ts - (lastFrame || ts)) / 1000)
    lastFrame = ts
    var busy = false
    for (var i = 0; i < orbs.length; i++) {
      var o = orbs[i]
      if (!o.wants()) continue
      busy = true
      o.stepTint()
      if (o.moving) {
        o.t += dt
        o.env = o.envSource ? o.envSource(dt) : 0
        o.applyMolten(o.t + o.phase, o.env)
      }
    }
    if (busy) raf = requestAnimationFrame(tick)
    else lastFrame = 0
  }

  function start() {
    if (!raf) {
      lastFrame = 0
      raf = requestAnimationFrame(tick)
    }
  }

  /** Turn an element into an orb. Idempotent: a second call on the same element
   *  returns the orb already attached to it. */
  function attach(el, opts) {
    if (!el) return null
    if (el._hnOrb) return el._hnOrb
    var o
    try {
      o = new Orb(el, opts)
    } catch (e) {
      // Same contract the analyser setup in app.js has kept from the start:
      // visualisation must never break playback. A failed orb leaves the
      // element as the plain CSS orb it already was.
      if (window.console && console.warn) console.warn('orb: attach failed', e)
      return null
    }
    el._hnOrb = o
    orbs.push(o)
    return o
  }

  function attachAll(selector, opts) {
    var out = []
    var nodes = document.querySelectorAll(selector)
    for (var i = 0; i < nodes.length; i++) {
      // A phase per orb, off the index, so a grid of them is a grid of
      // different shapes. Irrational-ish step so it does not cycle.
      var o = attach(nodes[i], Object.assign({}, opts, { phase: i * 3.7 }))
      if (o) out.push(o)
    }
    return out
  }

  // Only what a page calls. `paletteFor` and `reduced` were exported here until 2026-08-22 and
  // never had a caller in any page, in any commit -- git log -S finds none. Both stay module
  // locals: paletteFor runs at setSpeaker, reduced gates the frame loop.
  //
  // `reduced` was also a stale-value trap worth not re-exporting: it is a load-time matchMedia
  // snapshot, so a page reading HNOrb.reduced would get whatever was true when the script parsed,
  // not what is true now.
  window.HNOrb = {
    attach: attach,
    attachAll: attachAll,
    envelope: envelope
  }
})()
