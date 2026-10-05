// Applies the saved theme before first paint. A classic script loaded
// synchronously in <head> (an inline script would need a CSP exception), so
// the page never flashes the other theme. With no saved choice the attribute
// stays off and style.css follows prefers-color-scheme. Same key and values as
// theme.js; theme.test.js runs both.
(function () {
    var pref = null;
    try { pref = window.localStorage.getItem('proxion_theme'); } catch (_) { /* storage blocked */ }
    if (pref === 'dark' || pref === 'light') document.documentElement.setAttribute('data-theme', pref);
}());
