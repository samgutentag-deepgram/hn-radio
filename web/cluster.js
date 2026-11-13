// The cast cluster: the episode's voices as a group bubble, the way a messaging app shows a
// group thread. It sits on the two play buttons (the home page hero and the episode console):
//
//   var c = DGCluster.attach(button, { cast: ['Brooke', 'Drew', 'Miles', 'Cole'] })
//   c.setSpeaker('flux-drew-en')   // or a name; the speaker grows and takes the ring
//   c.setLive(true)                // playing: the speaker's bubble pulses with the audio
//   c.setEnvSource(fn)             // fn(dt) -> 0..1, from DGCluster.envelope(analyser)
//   DGCluster.render(cast)         // a still bubble for a list row
//   { cardsOnly: true }            // either call: leave out voices without a card (home page)
//
// Each voice is its official 2026 voice-card object on its colored tile (web/brand/avatars/).
// A voice without a card gets its initial on a neutral tile. The bubbles are decorative: the
// button keeps its own aria-label, and who is speaking is always in the transcript text.
;(function () {
  var AVATARS = ['alexis', 'bree', 'brittany', 'brooke', 'cliff', 'cole', 'colin', 'gemma', 'haley',
    'hannah', 'heather', 'kai', 'kit', 'meena', 'miles', 'sean', 'sienna']

  // Bubble positions as percentages of the circle, and diameters as a fraction of it. The first
  // slot is the biggest and goes to the host; the rest step down, like a group avatar.
  var LAYOUTS = {
    1: [[50, 50, 0.72]],
    2: [[38, 40, 0.5], [65, 64, 0.42]],
    3: [[40, 38, 0.5], [72, 56, 0.36], [42, 74, 0.32]],
    4: [[37, 37, 0.46], [71, 37, 0.33], [65, 69, 0.4], [32, 69, 0.3]],
  }

  var reduced = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)

  // The audio level that makes the speaking bubble swell. RMS over the time-domain window, not a
  // spectrum bin: frequency magnitudes carry the analyser's own smoothing and depend on fftSize,
  // so two pages with different sizes would breathe at different depths off the same audio.
  // Speech at broadcast level sits around 0.05 to 0.15 RMS and a loud vowel reaches about 0.22,
  // so 0.22 is full scale. The smoothing is quick to rise so a consonant lands and slow to fall so
  // the bubble rides the phrase; the coefficients are re-derived against dt from a 60 Hz
  // reference so it breathes the same on a 120 Hz display.
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
        return level // a dead analyser must not stop the animation
      }
      var sum = 0
      for (var i = 0; i < buf.length; i++) {
        var d = (buf[i] - 128) / 128
        sum += d * d
      }
      var want = Math.min(1, Math.sqrt(sum / buf.length) / ENV_FULL_SCALE)
      var k = want > level ? ENV_ATTACK : ENV_RELEASE
      // Clamped at 4 reference frames so a long stall (a background tab coming back) lands on the
      // new level rather than overshooting past it.
      k = 1 - Math.pow(1 - k, Math.min(4, Math.max(0, dt) * 60))
      level += (want - level) * k
      return level
    }
  }

  function keyOf(v) {
    return String(v || '').toLowerCase().replace(/^flux-/, '').replace(/-[a-z]{2}$/, '').trim()
  }

  function Cluster(el, opts) {
    var self = this
    this.el = el
    this.env = 0
    this.envFn = null
    this.live = false
    this.raf = 0
    this.last = 0

    var seen = {}
    this.cast = (opts.cast || []).map(keyOf).filter(function (k) {
      if (!k || seen[k]) return false
      seen[k] = true
      return true
    })
    // Bubbles that only show voice cards drop any voice without one. The host stays regardless,
    // so the bubble is never empty; they fall back to their initial.
    if (opts.cardsOnly) {
      var host = this.cast[0]
      this.cast = this.cast.filter(function (k, i) { return i === 0 || AVATARS.indexOf(k) !== -1 })
      if (!this.cast.length && host) this.cast = [host]
    }
    this.cast = this.cast.slice(0, 4)
    if (!this.cast.length) this.cast = ['brooke']

    el.classList.add('cluster')

    // Whatever glyph the page put in the button moves into a small badge on the rim.
    var badge = document.createElement('span')
    badge.className = 'cluster-badge'
    while (el.firstChild) badge.appendChild(el.firstChild)

    var ground = document.createElement('span')
    ground.className = 'cluster-ground'
    ground.setAttribute('aria-hidden', 'true')

    var layout = LAYOUTS[this.cast.length]
    this.bubbles = {}
    this.cast.forEach(function (k, i) {
      var spot = layout[i]
      var b = document.createElement('span')
      b.className = 'cluster-bubble'
      b.setAttribute('data-tile', k)
      b.style.left = spot[0] + '%'
      b.style.top = spot[1] + '%'
      b.style.width = b.style.height = (spot[2] * 100) + '%'
      b.style.animationDelay = (-1.3 * i) + 's'
      if (AVATARS.indexOf(k) !== -1) {
        var img = document.createElement('img')
        img.src = '/brand/avatars/' + k + '.webp'
        img.alt = ''
        img.decoding = 'async'
        b.appendChild(img)
      } else {
        b.classList.add('is-initial')
        b.textContent = k.charAt(0).toUpperCase()
      }
      ground.appendChild(b)
      self.bubbles[k] = b
    })

    el.appendChild(ground)
    if (!opts.static) el.appendChild(badge)
    this.setSpeaker(this.cast[0])
  }

  Cluster.prototype.setSpeaker = function (voice) {
    var k = keyOf(voice)
    if (!this.bubbles[k]) return
    if (this.current && this.bubbles[this.current]) {
      this.bubbles[this.current].classList.remove('is-speaking')
      this.bubbles[this.current].style.removeProperty('--env')
    }
    this.current = k
    this.bubbles[k].classList.add('is-speaking')
  }

  Cluster.prototype.setEnvSource = function (fn) {
    this.envFn = fn
    this.sync()
  }

  Cluster.prototype.setLive = function (on) {
    this.live = !!on
    this.el.classList.toggle('is-live', this.live)
    this.sync()
  }

  Cluster.prototype.sync = function () {
    var self = this
    var want = this.live && this.envFn && !reduced
    if (want && !this.raf) {
      this.last = 0
      var step = function (ts) {
        var dt = self.last ? (ts - self.last) / 1000 : 1 / 60
        self.last = ts
        self.env = self.envFn(dt)
        var b = self.bubbles[self.current]
        if (b) b.style.setProperty('--env', self.env.toFixed(3))
        self.raf = (self.live && self.envFn) ? requestAnimationFrame(step) : 0
      }
      this.raf = requestAnimationFrame(step)
    } else if (!want && this.raf) {
      cancelAnimationFrame(this.raf)
      this.raf = 0
      var b = this.bubbles[this.current]
      if (b) b.style.removeProperty('--env')
    }
  }

  window.DGCluster = {
    envelope: envelope,
    // A still group bubble for a list row: no button, no badge, no audio. Decorative.
    render: function (cast, className, opts) {
      var el = document.createElement('span')
      el.setAttribute('aria-hidden', 'true')
      new Cluster(el, { cast: cast, static: true, cardsOnly: !!(opts && opts.cardsOnly) })
      el.classList.add('is-static')
      if (className) el.classList.add(className)
      return el
    },
    attach: function (el, opts) {
      try {
        return new Cluster(el, opts || {})
      } catch (e) {
        if (window.console && console.warn) console.warn('cluster: attach failed', e)
        return null
      }
    },
  }
})()
