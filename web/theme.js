// Shared light/dark toggle (matches the roadmap/hub chrome). No build step.
//
// The button shows an icon rather than a text label so it can sit as a square box in line with the
// subscribe row's app icons.
//
// The icon reflects the mode that is ACTIVE: sun in light, moon in dark. (Both conventions exist;
// the other one shows the mode you would switch to, and reads as inverted.) The accessible name
// does the opposite on purpose and describes the ACTION, because a screen reader user needs to know
// what the button will do, not what mode they are already in.
(function () {
  var root = document.documentElement, btn;

  // Feather-style glyphs, stroked with currentColor so they follow the theme.
  var SUN = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"'
    + ' stroke-width="2" stroke-linecap="round" aria-hidden="true">'
    + '<circle cx="12" cy="12" r="4.2"/>'
    + '<path d="M12 2.2v2.2M12 19.6v2.2M2.2 12h2.2M19.6 12h2.2'
    + 'M5.1 5.1l1.6 1.6M17.3 17.3l1.6 1.6M18.9 5.1l-1.6 1.6M6.7 17.3l-1.6 1.6"/></svg>';
  var MOON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"'
    + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<path d="M20.5 14.6A8.6 8.6 0 1 1 9.4 3.5a6.7 6.7 0 0 0 11.1 11.1z"/></svg>';

  function set(t) {
    root.setAttribute('data-theme', t);
    if (!btn) return;
    var isDark = t === 'dark';
    btn.innerHTML = isDark ? MOON : SUN;             // icon = the mode you are in
    var action = isDark ? 'Switch to light mode' : 'Switch to dark mode';
    btn.setAttribute('aria-label', action);          // name = what the click does
    btn.setAttribute('title', action);
  }

  document.addEventListener('DOMContentLoaded', function () {
    btn = document.getElementById('theme-toggle');
    var stored = null; try { stored = localStorage.getItem('doc-theme'); } catch (e) {}
    var sysDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    var theme = stored || (sysDark ? 'dark' : 'light');
    set(theme);
    if (btn) btn.addEventListener('click', function () {
      theme = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      set(theme); try { localStorage.setItem('doc-theme', theme); } catch (e) {}
    });
  });
})();
