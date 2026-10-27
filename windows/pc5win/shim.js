// Мост интерфейса для Windows: имитирует window.webkit.messageHandlers.couch (WebKitGTK) через HTTP.
// Вставляется сервером в index.html до всех модулей. __PC5_TOKEN__ подставляет сервер.
(function () {
  var TOKEN = "__PC5_TOKEN__";
  var H = { "X-PC5-Token": TOKEN, "Content-Type": "application/json" };
  window.webkit = { messageHandlers: { couch: {
    postMessage: function (msg) {
      return fetch("/rpc", { method: "POST", headers: H, body: msg }).then(function (r) { return r.text(); });
    } } } };

  // События Python -> страница (длинный опрос)
  var since = 0;
  function poll() {
    fetch("/poll?since=" + since, { headers: H }).then(function (r) { return r.json(); }).then(function (res) {
      since = res.seq;
      (res.events || []).forEach(function (e) {
        if (e.event === "pc5.reload") { location.search = res.query; return; }
        var tries = 0;
        (function send() {
          if (window.__couch && window.__couch.dispatch) window.__couch.dispatch(e.event, e.payload);
          else if (tries++ < 100) setTimeout(send, 50);
        })();
      });
      poll();
    }).catch(function () { setTimeout(poll, 1000); });
  }
  poll();

  // Клавиатура -> кнопки геймпада (на Linux их даёт настоящий контроллер)
  var KEYS = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right", Enter: "cross", " ": "cross",
    Escape: "circle", Backspace: "circle", o: "options", x: "square", y: "triangle", q: "l1", e: "r1", 1: "l2", 3: "r2",
    PageUp: "rs-up", PageDown: "rs-down" };
  window.addEventListener("keydown", function (e) {
    if (e.key === "F11") { e.preventDefault();
      if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen(); return; }
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    var t = e.target && e.target.tagName;
    if (t === "INPUT" || t === "TEXTAREA") return;
    if (e.key === "Home") { e.preventDefault(); window.__couch && window.__couch.dispatch("ps", { kind: "long" }); return; }
    var b = KEYS[e.key]; if (!b) return;
    e.preventDefault();
    window.__couch && window.__couch.dispatch("pad", { button: b, repeat: e.repeat });
  }, true);
})();
