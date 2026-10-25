// Applies data.js (window.PITCH): slide order, sheet numbers, figures, the supplier slide and the team.
(function () {
  var P = window.PITCH;
  if (!P) return;
  var body = document.body;
  function pad(n) {
    return (n < 10 ? "0" : "") + n;
  }
  function esc(t) {
    return String(t).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function val(k) {
    var n = P.numbers && P.numbers[k];
    return n && n.v ? n.v : "";
  }

  var byId = {};
  [].forEach.call(document.querySelectorAll(".slide[data-id]"), function (s) {
    byId[s.getAttribute("data-id")] = s;
  });
  var main = P.order || [];
  var seq = main.concat(P.backup || []);
  var ui = document.querySelector(".ui");
  Object.keys(byId).forEach(function (id) {
    if (seq.indexOf(id) < 0) byId[id].remove();
  });
  var b = 0;
  seq.forEach(function (id, i) {
    var s = byId[id];
    if (!s) return;
    body.insertBefore(s, ui);
    var backup = i >= main.length;
    s.classList.toggle("backup", backup);
    var tb = s.querySelector(".tb b");
    if (tb) tb.textContent = backup ? "B" + ++b : pad(i + 1) + "/" + pad(main.length);
    var mk = s.querySelector(".tb-mark");
    if (mk) mk.textContent = backup ? "Backup" : P.deck.rev || mk.textContent;
  });

  [].forEach.call(document.querySelectorAll("[data-n]"), function (el) {
    var v = val(el.getAttribute("data-n"));
    if (v) el.textContent = v;
  });
  [].forEach.call(document.querySelectorAll("[data-show-if]"), function (el) {
    if (!val(el.getAttribute("data-show-if"))) el.remove();
  });
  [].forEach.call(document.querySelectorAll("[data-hide-if]"), function (el) {
    if (val(el.getAttribute("data-hide-if"))) el.remove();
  });

  var box = document.querySelector("[data-persona]");
  var p = P.persona;
  if (box && p) {
    var facts = p.facts
      .map(function (f, i) {
        return (
          '<li class="f' + (i + 1) + '"><p class="pf-fact">' + esc(f.fact) + "<code>" + esc(f.id) + '</code></p><p class="pf-did">' + esc(f.did) + "</p></li>"
        );
      })
      .join("");
    box.innerHTML =
      '<article class="pcard"><img src="' + esc(p.portrait) + '" alt="Portrait of ' + esc(p.name) + '" /><div class="pc-body"><p class="pc-name">' + esc(p.name) + '</p><p class="pc-role">' + esc(p.role) + " · " + esc(p.company) +
      '</p><p class="pc-about">' + esc(p.about) + "</p></div></article>" +
      '<div class="pfacts"><div class="pf-head"><p class="lab">Parley knows</p><p class="lab">So it</p></div><ol>' + facts + "</ol></div>" +
      '<figure class="ladder"><figcaption><span>Price per unit</span><span>' + esc(p.case) + "</span></figcaption>" + ladder(p.steps) +
      '<p class="ld-score">Score <b>' + esc(val(p.score)) + "</b> · Tacto&rsquo;s house agent " + esc(val(p.house)) + "</p></figure>";
  }

  function ladder(steps) {
    steps = steps.map(function (s) {
      return { label: s.label, note: s.note, price: parseFloat(val(s.key).replace(/[^0-9.]/g, "")) };
    });
    var W = 480, H = 380, L = 10, R = 10, T = 64, B = 330;
    var n = steps.length, seg = (W - L - R) / n;
    var hi = steps[0].price, lo = steps[n - 1].price, span = hi - lo || 1;
    function y(v) {
      return T + ((hi - v) / span) * (B - 80 - T);
    }
    var d = "", out = "";
    steps.forEach(function (s, i) {
      var x0 = L + i * seg, x1 = x0 + seg, yy = y(s.price), last = i === n - 1;
      d += (i ? "V" : "M" + x0 + " ") + yy + "H" + x1;
      var cx = x0 + seg / 2, dl = 3.75 + (2.4 * (i + 0.5)) / n;
      out +=
        '<g class="ld-i" style="--d:' + dl.toFixed(2) + 's"><circle class="ld-dot' + (last ? " last" : "") + '" cx="' + cx + '" cy="' + yy + '" r="' + (last ? 10 : 7) +
        '"/><text class="ld-p' + (last ? " last" : "") + '" x="' + cx + '" y="' + (yy - (last ? 30 : 22)) + '" text-anchor="middle">€' + s.price.toFixed(2) +
        '</text><text class="ld-r" x="' + cx + '" y="' + (B + 30) + '" text-anchor="middle">' + esc(s.label) + "</text></g>";
      if (last) {
        var w = 196, h = 66, bx = cx - w / 2, by = yy - 30 - 52;
        out +=
          '<path class="ld-lk ld-lk-a" d="M' + bx + " " + (by + 22) + "V" + by + "H" + (bx + 30) + '"/>' +
          '<path class="ld-lk ld-lk-b" d="M' + (bx + w) + " " + (by + h - 22) + "V" + (by + h) + "H" + (bx + w - 30) + '"/>' +
          '<g class="ld-tag"><rect x="' + (cx - 58) + '" y="' + (by - 40) + '" width="116" height="30"/><text x="' + cx + '" y="' + (by - 20) + '" text-anchor="middle">DEAL</text></g>';
      }
    });
    var notes = steps
      .map(function (s, i) {
        return '<text class="ld-n ld-i" style="--d:' + (3.75 + (2.4 * (i + 0.5)) / n).toFixed(2) + 's" x="' + (L + i * seg + seg / 2) + '" y="' + (B + 56) + '" text-anchor="middle">' + esc(s.note) + "</text>";
      })
      .join("");
    return (
      '<svg viewBox="0 0 ' + W + " " + (H + 30) + '" role="img" aria-label="Price per unit by round: ' +
      steps.map(function (s) { return s.label + " €" + s.price.toFixed(2); }).join(", ") +
      '"><path class="ld-axis" d="M' + L + " " + B + "H" + (W - R) + '"/><path class="ld-step" pathLength="1" d="' + d + '"/>' + out + notes + "</svg>"
    );
  }

  var site = P.deck.site || "";
  [].forEach.call(document.querySelectorAll("[data-site]"), function (el) {
    if (site) el.textContent = site.replace(/^https?:\/\//, "").replace(/\/$/, "");
  });
  [].forEach.call(document.querySelectorAll("img[data-qr]"), function (el) {
    if (P.deck.qr) el.src = P.deck.qr;
    if (site) el.alt = "QR code for " + site;
  });

  var team = document.querySelector("[data-team]");
  if (team && P.team) {
    team.innerHTML = P.team
      .map(function (m, i) {
        var ini = m.name.split(/\s+/).map(function (w) { return w[0]; }).join("").slice(0, 2);
        return (
          '<div class="face" style="--d:' + (0.9 + i * 0.18).toFixed(2) + 's"><div class="ring">' + esc(ini) +
          (m.photo ? '<img src="' + esc(m.photo) + '" alt="' + esc(m.name) + '" onerror="this.remove()" />' : "") +
          '</div><p class="nm">' + esc(m.name) + "</p></div>"
        );
      })
      .join("");
  }
})();

(function () {
  var q = new URLSearchParams(location.search);
  var body = document.body;
  var slides = [].slice.call(document.querySelectorAll(".slide"));
  var one = parseInt(q.get("slide") || "", 10);
  if (one) {
    body.classList.add("single");
    if (slides[one - 1]) slides[one - 1].classList.add("on");
    return;
  }
  if (q.has("print")) return;
  if (q.has("all") || innerWidth < 900) {
    body.classList.add("all");
    var zoom = function () {
      body.style.setProperty("--z", Math.min(1, innerWidth / 1920));
    };
    zoom();
    addEventListener("resize", zoom);
    return;
  }

  var cur = -1;
  var cnt = document.querySelector(".cnt"),
    bar = document.querySelector(".prog i");
  body.classList.add("present");
  function fit() {
    body.style.setProperty(
      "--k",
      Math.min(innerWidth / 1920, innerHeight / 1080),
    );
  }
  fit();
  addEventListener("resize", fit);
  function pad(n) {
    return (n < 10 ? "0" : "") + n;
  }
  function show(i) {
    i = Math.max(0, Math.min(slides.length - 1, i));
    if (i === cur) return;
    if (cur >= 0) slides[cur].classList.remove("on", "play");
    cur = i;
    var s = slides[cur];
    s.classList.add("on");
    [].forEach.call(s.querySelectorAll("img[data-anim]"), function (img) {
      if (!matchMedia("(prefers-reduced-motion: reduce)").matches)
        img.src = img.getAttribute("data-anim");
    });
    void s.offsetWidth;
    s.classList.add("play");
    cnt.textContent = pad(cur + 1) + " / " + pad(slides.length);
    bar.style.width = ((cur + 1) / slides.length) * 100 + "%";
    if (location.hash !== "#" + (cur + 1))
      history.replaceState(null, "", "#" + (cur + 1));
  }
  function go(d) {
    show(cur + d);
  }
  function fs() {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (document.documentElement.requestFullscreen)
      document.documentElement.requestFullscreen();
  }
  var idle;
  addEventListener("mousemove", function () {
    body.classList.remove("idle");
    clearTimeout(idle);
    idle = setTimeout(function () {
      body.classList.add("idle");
    }, 2600);
  });
  function wake() {
    body.classList.remove("idle");
    clearTimeout(idle);
    idle = setTimeout(function () {
      body.classList.add("idle");
    }, 2600);
  }
  addEventListener("focusin", wake);
  addEventListener("keydown", function (e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    wake();
    var k = e.key;
    // Enter and Space activate a focused link or control; they turn the page only elsewhere.
    if (
      (k === " " || k === "Enter") &&
      e.target.closest &&
      e.target.closest("a, button, input, textarea, select, summary")
    )
      return;
    if (k === "ArrowRight" || k === "PageDown" || k === " " || k === "Enter") {
      e.preventDefault();
      go(1);
    } else if (k === "ArrowLeft" || k === "PageUp" || k === "Backspace") {
      e.preventDefault();
      go(-1);
    } else if (k === "Home") show(0);
    else if (k === "End") show(slides.length - 1);
    else if (k === "f" || k === "F") fs();
  });
  document.querySelector(".ctl").addEventListener("click", function (e) {
    var b = e.target.closest("button");
    if (!b) return;
    e.stopPropagation();
    if (b.hasAttribute("data-fs")) fs();
    else go(+b.dataset.go);
  });
  addEventListener("click", function (e) {
    if (e.button !== 0 || e.target.closest("a,button,.ctl")) return;
    if (String(getSelection())) return;
    go(e.clientX < innerWidth / 3 ? -1 : 1);
  });
  var tx = null;
  addEventListener(
    "touchstart",
    function (e) {
      tx = e.touches[0].clientX;
    },
    { passive: true },
  );
  addEventListener("touchend", function (e) {
    if (tx === null) return;
    var dx = e.changedTouches[0].clientX - tx;
    tx = null;
    if (Math.abs(dx) > 40) go(dx < 0 ? 1 : -1);
  });
  addEventListener("hashchange", function () {
    show((parseInt(location.hash.slice(1), 10) || 1) - 1);
  });
  show((parseInt(location.hash.slice(1), 10) || 1) - 1);
  idle = setTimeout(function () {
    body.classList.add("idle");
  }, 3500);
})();
