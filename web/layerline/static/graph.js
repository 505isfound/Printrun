// Temperature graph (approved design): thick solid line = actual, thin dashed
// line in the same colour = target. Nozzle blue, bed orange. Fixed 0-300°
// scale (grows only if a temperature goes higher). Large view adds time
// labels, target tags and a separate fan strip.
"use strict";

const TempGraph = (() => {
  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function setup(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(50, Math.round(rect.width));
    const h = Math.max(50, Math.round(rect.height));
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx, w, h };
  }

  function minuteLabel(secAgo) {
    if (secAgo === 0) return "now";
    return secAgo % 60 === 0 ? `−${secAgo / 60} min` : `−${secAgo}s`;
  }

  // samples: [{t, n, nt, b, bt, f}], seconds: visible range
  function draw(canvas, samples, opts) {
    const { ctx, w, h } = setup(canvas);
    const big = !!opts.big;
    const seconds = opts.seconds || 60;
    const colors = {
      grid: css("--sep"), axis: css("--ink-2"), ink: css("--ink"), card: css("--card"),
      nozzle: css("--nozzle"), bed: css("--bed"), fan: css("--ink-2"),
    };
    const font = getComputedStyle(document.body).fontFamily;
    const fs = big ? 12 : 11;
    ctx.font = `${fs}px ${font}`;
    ctx.textBaseline = "middle";

    const now = samples.length ? samples[samples.length - 1].t : Date.now() / 1000;
    const start = now - seconds;
    const visible = samples.filter((s) => s.t >= start - 10);

    let maxT = 0;
    for (const s of visible) maxT = Math.max(maxT, s.n, s.nt, s.b, s.bt);
    const ymax = Math.max(300, Math.ceil((maxT + 20) / 50) * 50);
    const ystep = big ? 50 : 100;

    const labelW = ctx.measureText(String(ymax)).width + 8;
    const fanH = big ? 46 : 0;
    const xLabH = fs + 8;
    const px = labelW, pw = w - labelW - (big ? 4 : 6);
    const py = 6, ph = h - py - xLabH - (fanH ? fanH + 14 : 0);
    const X = (t) => px + ((t - start) / seconds) * pw;
    const Y = (v) => py + ph - (Math.min(v, ymax) / ymax) * ph;

    // grid + y labels
    ctx.lineWidth = 1;
    ctx.strokeStyle = colors.grid;
    ctx.fillStyle = colors.axis;
    ctx.textAlign = "right";
    for (let v = 0; v <= ymax; v += ystep) {
      const y = Math.round(Y(v)) + 0.5;
      ctx.beginPath(); ctx.moveTo(px, y); ctx.lineTo(px + pw, y); ctx.stroke();
      ctx.fillText(String(v), px - 6, Math.min(Math.max(y, fs / 2 + 1), py + ph));
    }

    // x labels
    ctx.textBaseline = "top";
    const xLabels = big
      ? (() => { const step = seconds <= 60 ? 10 : 60 * (seconds / 300); const out = [];
          for (let a = seconds; a >= 0; a -= step) out.push(a); return out; })()
      : [seconds, 0];
    xLabels.forEach((ago) => {
      const text = big ? minuteLabel(ago) : (ago === 0 ? "now" : "1 min ago");
      const x = px + ((seconds - ago) / seconds) * pw;
      const tw = ctx.measureText(text).width;
      ctx.textAlign = "left";
      ctx.fillText(text, Math.min(Math.max(x - tw / 2, px), px + pw - tw), py + ph + 5);
    });

    if (visible.length < 2) {
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.font = `${big ? 15 : 13}px ${font}`;
      ctx.fillText(samples.length ? "Waiting for more readings…" : "No temperature readings yet",
        px + pw / 2, py + ph / 2);
      return;
    }

    ctx.save();
    ctx.beginPath(); ctx.rect(px, 0, pw, py + ph + 1); ctx.clip();
    const series = [
      { key: "b", tkey: "bt", color: colors.bed },
      { key: "n", tkey: "nt", color: colors.nozzle },
    ];
    for (const s of series) {
      // target: thin dashed, stepped (targets change in jumps)
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1.3;
      ctx.strokeStyle = s.color;
      ctx.globalAlpha = 0.85;
      ctx.beginPath();
      let started = false;
      let prev = null;
      for (const p of visible) {
        const x = X(p.t), y = Y(p[s.tkey]);
        if (!p[s.tkey]) { started = false; prev = null; continue; }
        if (!started) { ctx.moveTo(x, y); started = true; }
        else { ctx.lineTo(x, Y(prev)); ctx.lineTo(x, y); }
        prev = p[s.tkey];
      }
      if (started && prev) ctx.lineTo(px + pw, Y(prev));
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;

      // actual: thick solid
      ctx.lineWidth = big ? 2.5 : 2;
      ctx.lineJoin = "round";
      ctx.beginPath();
      visible.forEach((p, i) => (i ? ctx.lineTo(X(p.t), Y(p[s.key])) : ctx.moveTo(X(p.t), Y(p[s.key]))));
      ctx.stroke();
      const last = visible[visible.length - 1];
      ctx.fillStyle = s.color;
      ctx.beginPath(); ctx.arc(X(last.t), Y(last[s.key]), big ? 4 : 3.5, 0, Math.PI * 2); ctx.fill();
    }
    ctx.restore();

    if (big) {
      // target tags at the right edge, just above each dashed line
      const last = visible[visible.length - 1];
      ctx.font = `600 ${fs}px ${font}`;
      ctx.textBaseline = "bottom";
      ctx.textAlign = "right";
      for (const s of series) {
        const t = last[s.tkey];
        if (!t) continue;
        const text = `target ${Math.round(t)}°`;
        const tw = ctx.measureText(text).width;
        const y = Y(t) - 4;
        ctx.fillStyle = colors.card;
        ctx.fillRect(px + pw - tw - 18, y - fs - 2, tw + 6, fs + 3);
        ctx.fillStyle = s.color;
        ctx.fillText(text, px + pw - 14, y);
      }

      // fan strip
      const fy = py + ph + xLabH + 14;
      ctx.font = `${fs}px ${font}`;
      ctx.fillStyle = colors.axis;
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      ctx.fillText("Fan", 0, fy);
      ctx.font = `600 ${fs + 1}px ${font}`;
      ctx.fillStyle = colors.ink;
      ctx.fillText(`${Math.round(last.f || 0)}%`, 0, fy + fs + 4);
      const base = fy + fanH - 6;
      const fh = fanH - 10;
      ctx.strokeStyle = colors.grid;
      ctx.beginPath(); ctx.moveTo(px, base + 0.5); ctx.lineTo(px + pw, base + 0.5); ctx.stroke();
      ctx.save();
      ctx.beginPath(); ctx.rect(px, fy - 2, pw, fanH); ctx.clip();
      ctx.fillStyle = colors.fan;
      ctx.globalAlpha = 0.35;
      ctx.beginPath();
      ctx.moveTo(X(visible[0].t), base);
      let prevF = visible[0].f || 0;
      for (const p of visible) {
        const x = X(p.t);
        ctx.lineTo(x, base - (prevF / 100) * fh);
        ctx.lineTo(x, base - ((p.f || 0) / 100) * fh);
        prevF = p.f || 0;
      }
      ctx.lineTo(px + pw, base - (prevF / 100) * fh);
      ctx.lineTo(px + pw, base);
      ctx.closePath();
      ctx.fill();
      ctx.restore();
    }
  }

  return { draw };
})();
