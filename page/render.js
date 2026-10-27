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
    await Promise.all((doc.people || []).filter(p => p.photo).map(async (p) => {
      photos[p.photo] = await loadImage("/photos/" + encodeURIComponent(p.photo));
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

  window.PhoneBook = { drawBook, framebuffer, pageCount, PADS, PER_PAGE, MAX_PEOPLE };
})();
