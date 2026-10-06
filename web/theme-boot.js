// Applies the saved theme before first paint. A classic script loaded
// synchronously in <head> (an inline script would need a CSP exception), so
// the page never flashes the other theme. With no saved choice the attribute
// stays off and style.css follows prefers-color-scheme. Same key and values as
// theme.js; theme.test.js runs both. A pinned theme also copies its colour
// into both theme-color metas (see syncThemeColor in theme.js).
(function () {
    var pref = null;
    try { pref = window.localStorage.getItem('proxion_theme'); } catch (_) { /* storage blocked */ }
    if (pref !== 'dark' && pref !== 'light') return;
    document.documentElement.setAttribute('data-theme', pref);
    if (typeof document.querySelectorAll !== 'function') return;
    var metas = document.querySelectorAll('meta[name="theme-color"][data-color]');
    var color = null;
    for (var i = 0; i < metas.length; i++) {
        if (metas[i].getAttribute('data-scheme') === pref) color = metas[i].getAttribute('data-color');
    }
    if (!color) return;
    for (var j = 0; j < metas.length; j++) metas[j].setAttribute('content', color);
}());
