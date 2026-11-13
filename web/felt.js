// The felt and the props: the Deepgram 2026 brand materials behind every page.
//
// Loaded in <head> so the colorway is on <html> before first paint. Each DAY gets one of the kit's
// five felt colorways (pink, teal, amber, green, rose), so the site changes color with the show:
// an episode page wears the colorway of its own air date, every other page wears today's.
// Per day rather than per week (the changelog show's rule) because HN Radio airs twice a day and
// its home page is a grid of days: the Morning and Afternoon Editions of one date share a
// colorway, and the art and the page for an episode always agree. scripts/make_art.py uses the
// same rule. The props are the kit's cut-out objects set on the felt beside the column. They are
// decoration only, hidden from assistive tech, and they hold still under prefers-reduced-motion.
;(function () {
  var COLORWAYS = 5
  var PROPS = {
    1: ['rock-a3', 'globe-glass', 'soundbar-stone'],
    2: ['column-02', 'globe-aluminium', 'rock-b2'],
    3: ['speaker-clay', 'player-bakelite', 'rock-a6'],
    4: ['bulb-stone', 'megaphone-copper', 'rock-b5'],
    5: ['rock-a1', 'globe-clay', 'block-stone'],
  }

  function dayIndex(date) {
    return Math.floor(date.getTime() / 86400000)
  }

  var root = document.documentElement
  var path = location.pathname
  // An episode page arrives two ways: /e/<id> and episode.html?id=<id>. Ids are YYYY-MM-DD with
  // an optional -am / -pm.
  var m = path.match(/^\/e\/(\d{4}-\d{2}-\d{2})/) ||
    (/episode\.html$/.test(path) && (location.search.match(/[?&]id=(\d{4}-\d{2}-\d{2})/) || null))
  // "Today" is the show's day, Pacific, the time zone the editions are scheduled in.
  var today = null
  try { today = new Date().toLocaleDateString('en-CA', { timeZone: 'America/Los_Angeles' }) } catch (e) {}
  var when = new Date((m ? m[1] : (today || new Date().toISOString().slice(0, 10))) + 'T00:00:00Z')
  if (isNaN(when.getTime())) when = new Date()
  var style = (((dayIndex(when) % COLORWAYS) + COLORWAYS) % COLORWAYS) + 1
  var image = m ? 'd' : 'a'

  root.setAttribute('data-felt', String(style))
  root.style.setProperty('--felt-image', 'url("/brand/felt/' + image + style + '.webp")')

  function place() {
    if (document.querySelector('.props')) return
    var wrap = document.createElement('div')
    wrap.className = 'props'
    wrap.setAttribute('aria-hidden', 'true')
    ;(PROPS[style] || PROPS[1]).forEach(function (name, i) {
      var img = document.createElement('img')
      img.className = 'prop prop-' + (i + 1)
      img.src = '/brand/props/' + name + '.webp'
      img.alt = ''
      img.decoding = 'async'
      wrap.appendChild(img)
    })
    document.body.insertBefore(wrap, document.body.firstChild)
  }
  if (document.body) place()
  else document.addEventListener('DOMContentLoaded', place)
})()
