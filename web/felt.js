// The felt and the props: the Deepgram 2026 brand materials behind every page.
//
// Loaded in <head> so the colorway is on <html> before first paint. Each week gets one of the
// kit's five felt colorways (pink, teal, amber, green, rose), so the site changes color with the
// show: an episode page wears the colorway of its own release week, every other page wears this
// week's. The props are the kit's cut-out objects (paper rocks, stone, glass, clay, copper) set
// on the felt beside the column. They are decoration only, hidden from assistive tech, and they
// hold still under prefers-reduced-motion.
;(function () {
  var COLORWAYS = 5
  var PROPS = {
    1: ['rock-a3', 'globe-glass', 'soundbar-stone'],
    2: ['column-02', 'globe-aluminium', 'rock-b2'],
    3: ['speaker-clay', 'player-bakelite', 'rock-a6'],
    4: ['bulb-stone', 'megaphone-copper', 'rock-b5'],
    5: ['rock-a1', 'globe-clay', 'block-stone'],
  }

  function weekIndex(date) {
    var days = Math.floor(date.getTime() / 86400000)
    // Day 0 (1970-01-01) was a Thursday; shifting by 4 makes each bucket start on Monday.
    return Math.floor((days + 3) / 7)
  }

  var root = document.documentElement
  var path = location.pathname
  var m = path.match(/^\/e\/(\d{4}-\d{2}-\d{2})/)
  var when = m ? new Date(m[1] + 'T12:00:00Z') : new Date()
  if (isNaN(when.getTime())) when = new Date()
  var style = (((weekIndex(when) % COLORWAYS) + COLORWAYS) % COLORWAYS) + 1
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
