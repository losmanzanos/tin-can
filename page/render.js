// Draws Zelda's phone book onto a canvas.
// The preview page (/) and the admin page both use this. After every save the
// admin page draws it at the Pi's exact screen size and uploads the pixels,
// so the Pi Zero never has to lay out text or decode photos itself.
(function () {
  const PAPER = "#fbf5e8", INK = "#16181f", MUSTARD = "#e9ad1c", KEY = "#262b3b", KEY_SHADOW = "#0c0e14", RULE = "#d8cdb4";
  const FONT = "Andika, sans-serif";
  const PER_PAGE = 7;      // the 8th slot is always Zelda's own card
  const MAX_PEOPLE = 21;   // three pages; the Pi flips through them

  // Same colors and symbols as the four small buttons on top of the phone.
  const PADS = {
    bolt: { bg: "#2c4c8c", w: 20, h: 30, draw(c) {
      c.fillStyle = "#fff"; c.fill(new Path2D("M12 1 3 17h6l-2 12 10-17h-6z"));
    } },
    star: { bg: "#6e9a7c", w: 30, h: 30, draw(c) {
      c.strokeStyle = "#fff"; c.lineWidth = 5.5; c.lineCap = "round";
      c.stroke(new Path2D("M15 3v24M4.6 9l20.8 12M4.6 21 25.4 9"));
    } },
    smile: { bg: "#a3473a", w: 30, h: 30, draw(c) {
      c.fillStyle = "#f6e7cf"; roundRect(c, 2, 2, 26, 26, 9); c.fill();
      c.fillStyle = "#a3473a";
      c.beginPath(); c.arc(11, 12, 2.2, 0, 7); c.arc(19, 12, 2.2, 0, 7); c.fill();
      c.strokeStyle = "#a3473a"; c.lineWidth = 2.6; c.lineCap = "round";
      c.stroke(new Path2D("M9.5 17.5q5.5 6 11 0"));
    } },
    lips: { bg: "#a87650", w: 40, h: 22, draw(c) {
      c.fillStyle = "#f2b7a8"; c.fill(new Path2D("M2 8q6-7 12-5 3 1 6 2 3-1 6-2 6-2 12 5-7 12-18 12T2 8z"));
      c.strokeStyle = "#7a3d33"; c.lineWidth = 2; c.lineCap = "round";
      c.stroke(new Path2D("M5 9.5q15 4 30 0"));
    } },
  };

  function roundRect(c, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }

  // The number keys' slightly lumpy outline (CSS: 46% 54% 50% 50% / 52% 48% 52% 48%).
  function keyShape(c, x, y, w, h) {
    const tl = [0.46 * w, 0.52 * h], tr = [0.54 * w, 0.48 * h], br = [0.5 * w, 0.52 * h], bl = [0.5 * w, 0.48 * h];
    c.beginPath();
    c.ellipse(x + tl[0], y + tl[1], tl[0], tl[1], 0, Math.PI, 1.5 * Math.PI);
    c.ellipse(x + w - tr[0], y + tr[1], tr[0], tr[1], 0, 1.5 * Math.PI, 2 * Math.PI);
    c.ellipse(x + w - br[0], y + h - br[1], br[0], br[1], 0, 0, 0.5 * Math.PI);
    c.ellipse(x + bl[0], y + h - bl[1], bl[0], bl[1], 0, 0.5 * Math.PI, Math.PI);
    c.closePath();
  }

  function font(px) { return `700 ${px}px ${FONT}`; }

  // Baseline for one line of text whose line box (line-height 1) starts at top.
  function baseline(c, top, px) {
    const m = c.measureText("Hg");
    const asc = m.fontBoundingBoxAscent ?? px * 0.9, desc = m.fontBoundingBoxDescent ?? px * 0.3;
    return top + (px - (asc + desc)) / 2 + asc;
  }

  // Shrink a little to fit, then cut off with an ellipsis if it still doesn't.
  function fitText(c, text, px, maxW) {
    let size = px;
    c.font = font(size);
    while (c.measureText(text).width > maxW && size > px * 0.72) { size -= px * 0.04; c.font = font(size); }
    if (c.measureText(text).width <= maxW) return { text, size };
    let t = text;
    while (t.length > 1 && c.measureText(t + "…").width > maxW) t = t.slice(0, -1);
    return { text: t.trimEnd() + "…", size };
  }

  function loadImage(src) {
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => resolve(null);
      img.src = src;
    });
  }

  async function ready(doc) {
    try { await document.fonts.load(font(40), "Zelda's 0123456789"); } catch (e) { /* falls back to sans-serif */ }
    const photos = {};
    const wanted = [...(doc.people || []), ...(doc.tiles || [])].filter(p => p.photo).map(p => p.photo);
    await Promise.all([...new Set(wanted)].map(async (name) => {
      photos[name] = await loadImage("/photos/" + encodeURIComponent(name));
    }));
    return photos;
  }

  function cells(W, H) {
    const out = [];
    if (H > W) {
      // Portrait: one column of eight, mustard stripe across the top.
      const stripe = 0.014 * H, padY = 0.014 * H, padX = 0.05 * W;
      const row = (H - stripe - 2 * padY) / 8;
      for (let i = 0; i < 8; i++) out.push({ x: padX, y: stripe + padY + i * row, w: W - 2 * padX, h: row, rule: i < 7 });
      return { stripe: [0, 0, W, stripe], row, cells: out };
    }
    // Landscape: two columns of four, mustard stripe down the left.
    const stripe = 0.014 * W, padT = 0.022 * H, padL = 0.024 * W, padR = 0.03 * W, gap = 0.032 * W;
    const colW = (W - stripe - padL - padR - gap) / 2, row = (H - 2 * padT) / 4;
    for (let col = 0; col < 2; col++)
      for (let r = 0; r < 4; r++)
        out.push({ x: stripe + padL + col * (colW + gap), y: padT + r * row, w: colW, h: row, rule: r < 3 });
    return { stripe: [0, 0, stripe, H], row, cells: out };
  }

  function drawPerson(c, p, cell, row, photos) {
    const ruleW = Math.max(1, Math.round(row * 0.014));
    if (cell.rule) { c.fillStyle = RULE; c.fillRect(cell.x, cell.y + cell.h - ruleW, cell.w, ruleW); }
    const cy = cell.y + (cell.h - ruleW) / 2;

    // Face: ink ring with a hard shadow, photo or colored circle with a letter.
    const R = row * 0.41, ring = row * 0.035, sh = row * 0.04, cx = cell.x + R;
    c.fillStyle = INK;
    c.beginPath(); c.arc(cx + sh, cy + sh, R, 0, 7); c.fill();
    c.beginPath(); c.arc(cx, cy, R, 0, 7); c.fill();
    c.save();
    c.beginPath(); c.arc(cx, cy, R - ring, 0, 7); c.clip();
    c.fillStyle = /^#[0-9a-f]{6}$/i.test(p.tone || "") ? p.tone : "#888";
    c.fillRect(cx - R, cy - R, 2 * R, 2 * R);
    const img = p.photo && photos[p.photo];
    if (img) {
      const s = Math.min(img.naturalWidth, img.naturalHeight);
      c.drawImage(img, (img.naturalWidth - s) / 2, (img.naturalHeight - s) / 2, s, s, cx - R, cy - R, 2 * R, 2 * R);
    } else {
      const letter = (p.letter || "").trim() || [...(p.name || "?").trim()][0] || "?";
      const t = fitText(c, letter, row * 0.46, 2 * (R - ring) * 0.86);
      c.font = font(t.size);
      c.fillStyle = "#fff"; c.textAlign = "center"; c.textBaseline = "alphabetic";
      const m = c.measureText(t.text);
      c.fillText(t.text, cx, cy + (m.actualBoundingBoxAscent - m.actualBoundingBoxDescent) / 2);
    }
    c.restore();

    // Name and how to dial them.
    const tx = cell.x + row * 0.82 + row * 0.16, maxW = cell.x + cell.w - tx;
    const dialH = p.pad ? row * 0.34 : row * 0.32;
    const top = cy - (row * 0.30 + row * 0.09 + dialH) / 2;
    const nm = fitText(c, p.name || "", row * 0.30, maxW);
    c.font = font(nm.size); c.fillStyle = INK; c.textAlign = "left"; c.textBaseline = "alphabetic";
    c.fillText(nm.text, tx, baseline(c, top + (row * 0.30 - nm.size) / 2, nm.size));
    const dy = top + row * 0.39;

    if (p.pad && PADS[p.pad]) {
      const pad = PADS[p.pad], w = row * 0.62, h = row * 0.34, b = row * 0.022;
      c.fillStyle = INK;
      roundRect(c, tx, dy + row * 0.025, w, h, h / 2); c.fill();
      roundRect(c, tx, dy, w, h, h / 2); c.fill();
      c.fillStyle = pad.bg;
      roundRect(c, tx + b, dy + b, w - 2 * b, h - 2 * b, h / 2 - b); c.fill();
      const ih = row * 0.2, s = ih / pad.h;
      c.save();
      c.translate(tx + w / 2 - pad.w * s / 2, dy + h / 2 - ih / 2);
      c.scale(s, s);
      pad.draw(c);
      c.restore();
    } else if (p.code) {
      const w = row * 0.42, h = row * 0.32, gap = row * 0.07;
      [...String(p.code)].forEach((d, i) => {
        const kx = tx + i * (w + gap);
        c.save();
        c.translate(kx + w / 2, dy + h / 2);
        c.rotate(-7 * Math.PI / 180);
        c.fillStyle = KEY_SHADOW; keyShape(c, -w / 2, -h / 2 + row * 0.025, w, h); c.fill();
        c.fillStyle = KEY; keyShape(c, -w / 2, -h / 2, w, h); c.fill();
        c.rotate(7 * Math.PI / 180);
        c.font = font(row * 0.22); c.fillStyle = "#fff"; c.textAlign = "center";
        const m = c.measureText(d);
        c.fillText(d, 0, (m.actualBoundingBoxAscent - m.actualBoundingBoxDescent) / 2 - row * 0.01);
        c.restore();
      });
    }
  }

  // Zelda's own number: a dark card in the last slot so it reads from across the room.
  function drawMe(c, number, cell, row, page, pages) {
    const h = row * 0.84, sh = row * 0.04, r = row * 0.14, w = cell.w - sh;
    const x = cell.x, y = cell.y + (cell.h - h) / 2;
    c.fillStyle = MUSTARD; roundRect(c, x + sh, y + sh, w, h, r); c.fill();
    c.fillStyle = INK; roundRect(c, x, y, w, h, r); c.fill();
    const padX = row * 0.2, maxW = w - 2 * padX;
    const label = row * 0.17, num = row * 0.27, gap = row * 0.05;
    let top = y + (h - (number ? label + gap + num : label)) / 2;
    c.textAlign = "left"; c.textBaseline = "alphabetic";
    c.font = font(label); c.fillStyle = MUSTARD;
    c.fillText("Zelda's phone", x + padX, baseline(c, top, label));
    if (pages > 1) {
      // Page dots in the top right corner of the card.
      const r = row * 0.035, gap = row * 0.11;
      for (let i = 0; i < pages; i++) {
        c.fillStyle = i === page ? MUSTARD : "#4a4f5e";
        c.beginPath(); c.arc(x + w - padX + r - (pages - 1 - i) * gap, y + row * 0.16, r, 0, 7); c.fill();
      }
    }
    if (number) {
      const t = fitText(c, number, num, maxW);
      c.font = font(t.size); c.fillStyle = "#fff";
      c.fillText(t.text, x + padX, baseline(c, top + label + gap + (num - t.size) / 2, t.size));
    }
  }

  function pageCount(doc) {
    return Math.max(1, Math.ceil(Math.min((doc.people || []).length, MAX_PEOPLE) / PER_PAGE));
  }

  async function drawBook(canvas, doc, W, H, page = 0) {
    const photos = await ready(doc);
    canvas.width = W; canvas.height = H;
    const c = canvas.getContext("2d", { willReadFrequently: true });
    c.fillStyle = PAPER; c.fillRect(0, 0, W, H);
    const L = cells(W, H);
    c.fillStyle = MUSTARD; c.fillRect(...L.stripe);
    const pages = pageCount(doc);
    page = Math.min(Math.max(0, page), pages - 1);
    (doc.people || []).slice(page * PER_PAGE, (page + 1) * PER_PAGE).forEach((p, i) => drawPerson(c, p, L.cells[i], L.row, photos));
    drawMe(c, doc.number || "", L.cells[7], L.row, page, pages);
    return canvas;
  }

  // ------------------------------------------------------------ calendar
  // After her wooden Lovevery board: a colored column per day, three periods
  // (sunrise, sun, moon), white tiles, and past days folded away.
  const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const DAY_COLORS = ["#b768d6", "#e2413b", "#f2c230", "#ef7f2c", "#6cbf4a", "#46bfc0", "#d9d6cf"];
  const DAY_TEXT = ["#9a4cbc", "#d6332d", "#c99a0e", "#e06d17", "#4f9f30", "#2ea2a3", "#8a8169"];
  const PERIODS = ["Morning", "Midday", "Evening"];
  const EMOJI = '"Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif';

  function periodIcon(c, i, x, y, s) {
    // x, y: center; s: size
    c.save();
    c.translate(x, y);
    c.lineCap = "round";
    if (i === 0) {          // sunrise over a horizon
      c.fillStyle = c.strokeStyle = "#f2675a";
      c.lineWidth = s * 0.07;
      c.beginPath(); c.arc(0, s * 0.12, s * 0.24, Math.PI, 0); c.fill();
      for (let k = 0; k < 7; k++) {
        const a = Math.PI + (k * Math.PI) / 6;
        c.beginPath();
        c.moveTo(Math.cos(a) * s * 0.33, s * 0.12 + Math.sin(a) * s * 0.33);
        c.lineTo(Math.cos(a) * s * 0.46, s * 0.12 + Math.sin(a) * s * 0.46);
        c.stroke();
      }
      c.beginPath(); c.moveTo(-s * 0.48, s * 0.2); c.lineTo(s * 0.48, s * 0.2); c.stroke();
    } else if (i === 1) {   // sun
      c.fillStyle = c.strokeStyle = "#f6b92a";
      c.lineWidth = s * 0.07;
      c.beginPath(); c.arc(0, 0, s * 0.22, 0, 7); c.fill();
      for (let k = 0; k < 12; k++) {
        const a = (k * Math.PI) / 6;
        c.beginPath();
        c.moveTo(Math.cos(a) * s * 0.31, Math.sin(a) * s * 0.31);
        c.lineTo(Math.cos(a) * s * 0.45, Math.sin(a) * s * 0.45);
        c.stroke();
      }
    } else {                // moon and two stars
      // Crescent: the moon's circle minus a second circle. Done with a clip and
      // even-odd fill so it never punches a see-through hole in what's behind.
      c.save();
      c.beginPath(); c.arc(-s * 0.05, 0, s * 0.3, 0, 7); c.clip();
      c.fillStyle = "#3fb6a8";
      c.beginPath(); c.rect(-s, -s, 2 * s, 2 * s); c.arc(s * 0.1, -s * 0.1, s * 0.26, 0, 7); c.fill("evenodd");
      c.restore();
      c.strokeStyle = "#3fb6a8"; c.lineWidth = s * 0.045;
      for (const [sx, sy, r] of [[0.32, -0.22, 0.07], [0.3, 0.16, 0.05]]) {
        c.beginPath();
        c.moveTo(s * (sx - r), s * sy); c.lineTo(s * (sx + r), s * sy);
        c.moveTo(s * sx, s * (sy - r)); c.lineTo(s * sx, s * (sy + r));
        c.stroke();
      }
    }
    c.restore();
  }

  // One magnet tile: white rounded square with an emoji, a smiley, a photo or nothing.
  function drawTile(c, t, x, y, s, photos) {
    const r = s * 0.14;
    c.fillStyle = "rgba(22,24,31,0.16)";
    roundRect(c, x + s * 0.03, y + s * 0.05, s, s, r); c.fill();
    c.fillStyle = "#ffffff";
    roundRect(c, x, y, s, s, r); c.fill();
    c.strokeStyle = "#e3dccb"; c.lineWidth = Math.max(1, s * 0.02);
    roundRect(c, x, y, s, s, r); c.stroke();
    if (!t) return;
    const cx = x + s / 2, cy = y + s / 2;
    if (t.kind === "photo" && photos[t.photo]) {
      const img = photos[t.photo], m = Math.min(img.naturalWidth, img.naturalHeight);
      c.save();
      roundRect(c, x + s * 0.05, y + s * 0.05, s * 0.9, s * 0.9, r * 0.8); c.clip();
      c.drawImage(img, (img.naturalWidth - m) / 2, (img.naturalHeight - m) / 2, m, m, x + s * 0.05, y + s * 0.05, s * 0.9, s * 0.9);
      c.restore();
    } else if (t.kind === "emoji") {
      c.font = `${s * 0.6}px ${EMOJI}`;
      c.textAlign = "center"; c.textBaseline = "middle"; c.fillStyle = INK;
      c.fillText(t.emoji, cx, cy + s * 0.04);
    } else if (t.kind === "face") {
      c.fillStyle = t.color;
      c.beginPath(); c.arc(cx, cy, s * 0.33, 0, 7); c.fill();
      c.strokeStyle = "rgba(22,24,31,0.6)"; c.lineWidth = s * 0.03; c.lineCap = "round";
      for (const dx of [-0.1, 0.1]) {   // happy closed eyes
        c.beginPath(); c.arc(cx + s * dx, cy - s * 0.04, s * 0.04, Math.PI * 1.15, Math.PI * 1.85); c.stroke();
      }
      c.beginPath(); c.arc(cx, cy + s * 0.03, s * 0.1, Math.PI * 0.2, Math.PI * 0.8); c.stroke();
    }
    c.textBaseline = "alphabetic";
  }

  // The week along the bottom: past days folded, today lifted, the shown day outlined.
  function drawWeek(c, x, y, w, h, today, shown) {
    const gap = w * 0.02, bw = (w - gap * 6) / 7;
    for (let d = 0; d < 7; d++) {
      const bx = x + d * (bw + gap);
      const folded = today != null && d < today;
      const lifted = d === shown;
      const bh = folded ? h * 0.5 : lifted ? h : h * 0.8;
      const by = y + h - bh - (lifted ? h * 0.08 : 0);
      c.globalAlpha = folded ? 0.38 : 1;
      if (lifted) { c.fillStyle = INK; roundRect(c, bx + h * 0.05, by + h * 0.07, bw, bh, bw * 0.22); c.fill(); }
      c.fillStyle = DAY_COLORS[d];
      roundRect(c, bx, by, bw, bh, bw * 0.22); c.fill();
      if (lifted) { c.strokeStyle = INK; c.lineWidth = h * 0.05; roundRect(c, bx, by, bw, bh, bw * 0.22); c.stroke(); }
      c.globalAlpha = 1;
      c.font = font(Math.min(bw, bh) * 0.42);
      c.fillStyle = d === 2 || d === 6 ? INK : "#fff";
      c.textAlign = "center"; c.textBaseline = "middle";
      if (!folded) c.fillText(DAYS[d][0], bx + bw / 2, by + bh / 2 + h * 0.02);
      c.textBaseline = "alphabetic";
    }
  }

  // day: 0 = Monday. today: which day is today (null when just browsing).
  // period: 0-2 to light up the current part of the day, or null.
  // Hints for her 3-button controller, drawn the way the buttons look:
  // dark round arrows for back / next, a yellow button for "the next screen".
  function arrowButton(c, x, y, r, dir) {
    c.fillStyle = "rgba(22,24,31,0.25)";
    c.beginPath(); c.arc(x, y + r * 0.12, r, 0, 7); c.fill();
    c.fillStyle = INK;
    c.beginPath(); c.arc(x, y, r, 0, 7); c.fill();
    c.fillStyle = "#fff";
    c.beginPath();
    c.moveTo(x + dir * r * 0.45, y);
    c.lineTo(x - dir * r * 0.3, y - r * 0.45);
    c.lineTo(x - dir * r * 0.3, y + r * 0.45);
    c.closePath(); c.fill();
  }
  function screenButton(c, x, y, r, emoji) {
    c.fillStyle = INK;
    c.beginPath(); c.arc(x + r * 0.08, y + r * 0.12, r, 0, 7); c.fill();
    c.fillStyle = MUSTARD;
    c.beginPath(); c.arc(x, y, r, 0, 7); c.fill();
    c.strokeStyle = INK; c.lineWidth = r * 0.1; c.stroke();
    c.font = `${r * 1.05}px ${EMOJI}`;
    c.textAlign = "center"; c.textBaseline = "middle";
    c.fillText(emoji, x, y + r * 0.06);
    c.textBaseline = "alphabetic";
  }

  async function drawDay(canvas, doc, W, H, day, today = null, period = null, opts = {}) {
    const photos = await ready(doc);
    canvas.width = W; canvas.height = H;
    const c = canvas.getContext("2d", { willReadFrequently: true });
    const u = Math.min(W, H) / 100, portrait = H > W;
    const tiles = Object.fromEntries((doc.tiles || []).map(t => [t.id, t]));
    const plan = (doc.week || [])[day] || [[], [], []];
    c.fillStyle = PAPER; c.fillRect(0, 0, W, H);

    // Where things go.
    let col, legendX, text, week;
    if (portrait) {
      // Room for "Today is / Tuesday, / a weekday. / Evening: ..." above the column.
      const lines = (today === day ? u * 7 : 0) + u * 15.75 + u * 9 + (today === day && period != null ? u * 9 : 0);
      text = { x: W * 0.07, y: u * 6, w: W * 0.86, h: lines + u * 6 };
      week = { x: W * 0.07, y: H - u * 15, w: W * 0.86, h: u * 10 };
      legendX = W * 0.07 + u * 7;
      col = { x: W * 0.07 + u * 15, y: text.y + text.h + u * 2, w: W * 0.86 - u * 15, h: week.y - u * 4 - (text.y + text.h + u * 2) };
    } else {
      c.fillStyle = MUSTARD; c.fillRect(0, 0, 0.014 * W, H);
      legendX = 0.014 * W + u * 9;
      col = { x: legendX + u * 9, y: u * 5, w: Math.min(W * 0.36, H * 0.8), h: H - u * 10 };
      const rx = col.x + col.w + u * 7;
      text = { x: rx, y: u * 15, w: W - rx - u * 5, h: H * 0.5 };
      week = { x: rx, y: H - u * 22, w: W - rx - u * 5, h: u * 13 };
    }

    // The day's column, like the colored frames on her board.
    const bw = u * 2.2, hr = u * 3.5;
    c.fillStyle = DAY_COLORS[day];
    roundRect(c, col.x, col.y, col.w, col.h, u * 4); c.fill();
    c.fillStyle = "#fffdf7";
    roundRect(c, col.x + bw, col.y + bw, col.w - 2 * bw, col.h - 2 * bw, u * 2.6); c.fill();
    const headH = u * 9;
    c.font = font(u * 5.2); c.fillStyle = DAY_TEXT[day]; c.textAlign = "center"; c.textBaseline = "middle";
    c.fillText(DAYS[day], col.x + col.w / 2, col.y + bw + headH / 2 + u * 0.4);
    c.fillStyle = DAY_COLORS[day];
    c.fillRect(col.x + col.w * 0.18, col.y + bw + headH - u * 0.6, col.w * 0.64, u * 0.5);
    c.textBaseline = "alphabetic";

    const top = col.y + bw + headH + u, rowH = (col.y + col.h - bw - u - top) / 3;
    for (let p = 0; p < 3; p++) {
      const ry = top + p * rowH, cy = ry + rowH / 2;
      if (p === period) {
        c.fillStyle = DAY_COLORS[day]; c.globalAlpha = 0.22;
        roundRect(c, col.x + bw + u, ry + u * 0.6, col.w - 2 * bw - 2 * u, rowH - u * 1.2, u * 2.4); c.fill();
        c.globalAlpha = 1;
        // "now" ring around the period icon
        c.fillStyle = "#fff"; c.strokeStyle = INK; c.lineWidth = u * 0.6;
        c.beginPath(); c.arc(legendX, cy, u * 6.4, 0, 7); c.fill(); c.stroke();
      }
      periodIcon(c, p, legendX, cy, u * (p === period ? 10 : 8.5));
      const ids = (plan[p] || []).filter(id => tiles[id]);
      const s = Math.min(rowH * 0.8, (col.w - 2 * bw - u * 6) / 2);
      const n = ids.length, total = n * s + Math.max(0, n - 1) * u * 3;
      let tx = col.x + col.w / 2 - total / 2;
      for (const id of ids) {
        drawTile(c, tiles[id], tx, cy - s / 2, s, photos);
        tx += s + u * 3;
      }
    }

    // "Today is Tuesday, a weekday."
    const weekend = day >= 5;
    c.textAlign = "left"; c.textBaseline = "alphabetic";
    let ty = text.y;
    if (today === day) {
      c.font = font(u * 7); c.fillStyle = INK;
      ty += u * 7; c.fillText("Today is", text.x, ty);
    }
    const big = fitText(c, DAYS[day] + (today === day ? "," : ""), u * 15, text.w);
    c.font = font(big.size); c.fillStyle = DAY_TEXT[day];
    ty += big.size * 1.05; c.fillText(big.text, text.x, ty);
    c.font = font(u * 7); c.fillStyle = INK;
    ty += u * 9; c.fillText(weekend ? "a weekend day." : "a weekday.", text.x, ty);

    // What's happening now, in words, for grown-ups reading along.
    if (period != null && today === day) {
      const now = (plan[period] || []).map(id => tiles[id]?.label).filter(Boolean);
      if (now.length) {
        const t = fitText(c, PERIODS[period] + ": " + now.join(" & "), u * 5, text.w);
        c.font = font(t.size); c.fillStyle = "#6f6752";
        ty += u * 9; c.fillText(t.text, text.x, ty);
      }
    }
    if (opts.hints) {
      // ◀ ▶ sit at the ends of the week, because that's what they move along.
      const r = week.h * 0.36, cy = week.y + week.h * 0.55;
      arrowButton(c, week.x + r, cy, r, -1);
      arrowButton(c, week.x + week.w - r, cy, r, 1);
      week = { x: week.x + r * 2.35, y: week.y, w: week.w - r * 4.7, h: week.h };
      // The yellow button goes to the phone book next. paint.py covers this
      // spot with a mail tag when postcards are waiting.
      screenButton(c, W - u * 10, u * 9, u * 5, "📞");
    }
    drawWeek(c, week.x, week.y, week.w, week.h, today, day);
    return canvas;
  }

  // ------------------------------------------------------------ postcards (Zelda's view)
  // The Pi draws these itself (postcards.py); this is the same look for the browser.
  async function drawPostcard(canvas, card, W, H) {
    try { await document.fonts.load(font(40), "From"); } catch (e) { /* sans-serif */ }
    const img = card && card.photo ? await loadImage("/postcards/" + card.id + ".jpg") : null;
    canvas.width = W; canvas.height = H;
    const c = canvas.getContext("2d");
    const u = Math.min(W, H) / 100;
    c.fillStyle = PAPER; c.fillRect(0, 0, W, H);
    if (W > H) { c.fillStyle = MUSTARD; c.fillRect(0, 0, W * 0.014, H); }
    const x0 = W * 0.06, y0 = u * 5, x1 = W * 0.94, y1 = H - u * 16, sh = u * 1.2;
    c.fillStyle = INK; roundRect(c, x0 + sh, y0 + sh, x1 - x0, y1 - y0, u * 4); c.fill();
    c.fillStyle = "#fffdf7"; roundRect(c, x0, y0, x1 - x0, y1 - y0, u * 4); c.fill();
    c.strokeStyle = INK; c.lineWidth = u * 0.6; roundRect(c, x0, y0, x1 - x0, y1 - y0, u * 4); c.stroke();
    const pad = u * 3, caption = (card && card.caption || "").trim();
    let ix0 = x0 + pad, iy0 = y0 + pad, ix1 = x1 - pad, iy1 = y1 - pad;
    c.textAlign = "center"; c.textBaseline = "middle"; c.fillStyle = INK;
    if (img) {
      if (caption) iy1 -= u * 11;
      const k = Math.min((ix1 - ix0) / img.naturalWidth, (iy1 - iy0) / img.naturalHeight);
      const w = img.naturalWidth * k, h = img.naturalHeight * k;
      c.drawImage(img, (ix0 + ix1 - w) / 2, (iy0 + iy1 - h) / 2, w, h);
      if (caption) {
        const t = fitText(c, caption, u * 7, ix1 - ix0);
        c.font = font(t.size); c.fillText(t.text, (ix0 + ix1) / 2, iy1 + u * 5.5);
      }
    } else {
      const msg = caption || "No postcards yet. Ask Nana to send one!";
      let size = u * 12, lines;
      for (;;) {
        c.font = font(size);
        lines = [];
        let line = "";
        for (const word of msg.split(/\s+/)) {
          const test = line ? line + " " + word : word;
          if (c.measureText(test).width <= ix1 - ix0 || !line) line = test; else { lines.push(line); line = word; }
        }
        lines.push(line);
        if (lines.length * size * 1.25 <= iy1 - iy0 || size < u * 5) break;
        size *= 0.9;
      }
      const top = (iy0 + iy1) / 2 - (lines.length - 1) * size * 1.25 / 2;
      lines.forEach((ln, i) => c.fillText(ln, (ix0 + ix1) / 2, top + i * size * 1.25));
    }
    if (card) {
      const who = fitText(c, "From " + (card.from || "someone"), u * 8, W * 0.5);
      c.font = font(who.size);
      const tw = c.measureText(who.text).width, bx = x0, by = y1 + u * 3.2, bwid = tw + u * 6, bh = u * 9.5;
      c.fillStyle = INK; roundRect(c, bx + sh, by + sh, bwid, bh, bh / 2); c.fill();
      c.fillStyle = MUSTARD; roundRect(c, bx, by, bwid, bh, bh / 2); c.fill();
      c.fillStyle = INK; c.fillText(who.text, bx + bwid / 2, by + bh / 2);
      c.font = font(u * 5.5); c.fillStyle = "#8a8169"; c.textAlign = "right";
      const wd = (new Date(card.ts * 1000).getDay() + 6) % 7;
      c.fillText(DAYS[wd], x1, by + bh / 2);
    }
    c.textAlign = "left"; c.textBaseline = "alphabetic";
    return canvas;
  }

  // Which part of the day it is: morning until 11, midday until 4, then evening.
  function periodAt(date) {
    const m = date.getHours() * 60 + date.getMinutes();
    return m < 11 * 60 ? 0 : m < 16 * 60 ? 1 : 2;
  }
  function weekdayOf(date) { return (date.getDay() + 6) % 7; }  // Monday = 0

  async function gzipBytes(bytes) {
    if (typeof CompressionStream === "undefined") return null;
    const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream("gzip"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  // Pixels in the layout /dev/fb0 wants: 32 bpp is B,G,R,X; 16 bpp is RGB565, little-endian.
  function framebuffer(canvas, bpp) {
    const { width: w, height: h } = canvas;
    const src = canvas.getContext("2d").getImageData(0, 0, w, h).data;
    if (bpp === 16) {
      const out = new Uint8Array(w * h * 2);
      for (let i = 0, j = 0; i < src.length; i += 4, j += 2) {
        const v = ((src[i] & 0xf8) << 8) | ((src[i + 1] & 0xfc) << 3) | (src[i + 2] >> 3);
        out[j] = v & 0xff; out[j + 1] = v >> 8;
      }
      return out;
    }
    const out = new Uint8Array(w * h * 4);
    for (let i = 0; i < src.length; i += 4) {
      out[i] = src[i + 2]; out[i + 1] = src[i + 1]; out[i + 2] = src[i]; out[i + 3] = 0;
    }
    return out;
  }

  // ------------------------------------------------------------ the Pi's screen
  // The Pi Zero has no browser, so a browser (the admin page, or the magnet
  // board) draws the phone book and calendar at the screen's exact size and
  // uploads the pixels. A key per group says whether it needs redrawing.
  function hash(s) {   // FNV-1a, enough to notice a change
    let h = 0x811c9dc5;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return (h >>> 0).toString(16);
  }
  function screenKey(doc, fb, group) {
    const part = group === "book"
      ? { people: doc.people.map(({ email, phone, ...p }) => p), number: doc.number }
      : { tiles: doc.tiles, week: doc.week };
    return hash(JSON.stringify([part, fb.w, fb.h, fb.bpp, 4]));
  }
  function screenFrames(doc, group) {
    if (group === "book") return [...Array(pageCount(doc)).keys()].map(i => ({ name: "book-" + i, draw: (c, W, H) => drawBook(c, doc, W, H, i) }));
    const out = [];
    for (let d = 0; d < 7; d++) {
      for (let p = 0; p < 3; p++) out.push({ name: `today-${d}-${p}`, draw: (c, W, H) => drawDay(c, doc, W, H, d, d, p, { hints: true }) });
      out.push({ name: `day-${d}`, draw: (c, W, H) => drawDay(c, doc, W, H, d, null, null, { hints: true }) });
    }
    return out;
  }
  function staleGroups(doc, fb, framesInfo, groups) {
    return groups.filter(g => {
      const m = framesInfo[g];
      return !m || m.key !== screenKey(doc, fb, g) || m.w !== fb.w || m.h !== fb.h || m.bpp !== fb.bpp;
    });
  }
  // Redraw and upload what's out of date. Returns the new framesInfo.
  async function syncScreens(doc, fb, framesInfo, groups, { headers = {}, progress = () => {} } = {}) {
    framesInfo = { ...framesInfo };
    for (const group of staleGroups(doc, fb, framesInfo, groups)) {
      const frames = screenFrames(doc, group);
      let n = 0;
      for (const f of frames) {
        progress(Math.round((n++ / frames.length) * 100));
        const canvas = await f.draw(document.createElement("canvas"), fb.w, fb.h);
        const raw = framebuffer(canvas, fb.bpp);
        const gz = await gzipBytes(raw);
        const r = await fetch(`/api/frames/${group}/${f.name}`, { method: "POST", body: gz || raw, headers: { ...headers,
          "Content-Type": "application/octet-stream", "X-Width": fb.w, "X-Height": fb.h, "X-Bpp": fb.bpp, "X-Gzip": gz ? "1" : "0" } });
        if (!r.ok) throw new Error("The screen didn't update: " + ((await r.json().catch(() => ({}))).error || r.status));
      }
      const meta = { w: fb.w, h: fb.h, bpp: fb.bpp, key: screenKey(doc, fb, group), names: frames.map(f => f.name) };
      const r = await fetch(`/api/frames/${group}/commit`, { method: "POST", body: JSON.stringify(meta), headers: { ...headers, "Content-Type": "application/json" } });
      if (!r.ok) throw new Error("The screen didn't update: " + ((await r.json().catch(() => ({}))).error || r.status));
      framesInfo[group] = meta;
    }
    return framesInfo;
  }

  window.PhoneBook = {
    syncScreens, staleGroups, screenKey, periodIcon, DAY_TEXT,
    drawBook, drawDay, drawPostcard, drawTile, framebuffer, gzipBytes, pageCount, periodAt, weekdayOf,
    PADS, PER_PAGE, MAX_PEOPLE, DAYS, DAY_COLORS, PERIODS,
  };
})();
