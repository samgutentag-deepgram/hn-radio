/* Play instrumentation, browser side. Publishes window.HNPlays.
 *
 * Classic script, not a module, because three pages need it and only one of them (episode.html)
 * loads a module. Same reason orb.js is a classic script.
 *
 * WHAT IT COUNTS. Plays that happen on this site. A podcast client that pulled feed.xml and
 * played the MP3 never runs this file, so every number it feeds is "on the dashboard" and never
 * "in the world". stats.html says that out loud rather than letting the figure be read as
 * listenership.
 *
 * WHY DE-DUPLICATION LIVES HERE AND NOT ON THE SERVER. "One play per listener per episode" needs
 * to know who the listener is. Doing it server-side means storing something that identifies them
 * -- an IP, a cookie, a fingerprint -- which is exactly the thing hn_radio/plays.py is built not
 * to hold. sessionStorage answers the same question with no identity at all: the key lives in the
 * tab, and it dies with the tab. The trade is that a listener with two tabs counts twice and a
 * listener who comes back tomorrow counts again, which for a daily show is arguably the more
 * honest number anyway.
 *
 * NOTHING HERE MAY THROW INTO PLAYBACK. Every entry point is wrapped. A private-mode browser with
 * sessionStorage disabled, a blocked request, an ad blocker eating the endpoint: all of those have
 * to end with the audio still playing and the console quiet.
 */
(function () {
  'use strict';

  var ENDPOINT = '/api/plays';
  // Must match hn_radio.plays.MILESTONES. Fixed marks, not a percentage, so the funnel on
  // stats.html is four readable numbers instead of a histogram nobody asked for.
  var MILESTONES = [25, 50, 75, 100];

  function remember(key) {
    // Returns true the FIRST time it sees a key. When storage is unavailable it returns true
    // every time, which double-counts rather than silently counting nothing -- the failure that
    // shows up in the number is better than the one that hides in it.
    try {
      if (sessionStorage.getItem(key)) return false;
      sessionStorage.setItem(key, '1');
      return true;
    } catch (e) {
      return true;
    }
  }

  function send(body) {
    var json = JSON.stringify(body);
    try {
      // sendBeacon survives the page going away, which is the common case for the last milestone
      // of an episode: the listener closes the tab as it ends. A plain fetch there is cancelled
      // on unload and the event is simply lost.
      if (navigator.sendBeacon) {
        navigator.sendBeacon(ENDPOINT, new Blob([json], { type: 'application/json' }));
        return;
      }
      // keepalive is the fetch-shaped version of the same guarantee. Safari has had it for a
      // while but sendBeacon is the older and better-supported of the two, hence the order.
      fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: json,
        keepalive: true
      }).catch(function () {});
    } catch (e) {
      /* a counter is never worth a console error on a page that is playing audio fine */
    }
  }

  function track(episodeId, event, pct) {
    if (!episodeId || !event) return;
    var key = 'hnplays:' + episodeId + ':' + event + (pct == null ? '' : ':' + pct);
    if (!remember(key)) return;
    var body = { episode_id: episodeId, event: event };
    if (pct != null) body.pct = pct;
    send(body);
  }

  function attach(episodeId, audio) {
    if (!episodeId || !audio) return;

    audio.addEventListener('play', function () { track(episodeId, 'play'); });

    audio.addEventListener('timeupdate', function () {
      var d = audio.duration;
      // NaN before metadata arrives, Infinity on a stream. Either one turns the ratio below into
      // nonsense, and nonsense here means a milestone fired at the wrong moment and remembered
      // for the rest of the session.
      if (!isFinite(d) || d <= 0) return;
      var pct = (audio.currentTime / d) * 100;
      for (var i = 0; i < MILESTONES.length; i++) {
        if (pct >= MILESTONES[i]) track(episodeId, 'progress', MILESTONES[i]);
      }
    });

    // Scrubbing to the end fires 100 without listening to a word, and this does not try to detect
    // that. Guarding it properly means tracking which spans were actually played, which is a
    // session recording in all but name. The de-duplication caps the damage at one bogus complete
    // per session, and the honest fix is to read the number as "reached", not as "listened to".
  }

  var statsPromise = null;
  function stats() {
    // Memoised: index.html asks once for the whole board and then reads rows out of it, so a
    // fifteen-episode archive is one request rather than fifteen.
    if (!statsPromise) {
      statsPromise = fetch('/api/stats')
        .then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; });
    }
    return statsPromise;
  }

  function byEpisode() {
    return stats().then(function (d) {
      var map = {};
      if (d && d.episodes) {
        d.episodes.forEach(function (row) { map[row.id] = row; });
      }
      return map;
    });
  }

  window.HNPlays = {
    track: track,
    attach: attach,
    view: function (episodeId) { track(episodeId, 'view'); },
    stats: stats,
    byEpisode: byEpisode
  };
}());
