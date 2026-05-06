#!/usr/bin/env node
// generate-report.js — high-performance external image version

const fs = require('fs');
const path = require('path');
const duckdb = require('duckdb');

const TOOLBOX = path.dirname(process.argv[1]);
const CONFIG_PATH = process.argv[2] || path.join(TOOLBOX, 'config.json');
const OUT_PATH = process.argv[3] || path.join(TOOLBOX, '..', '..', 'actresses-report.html');
const IMAGES_DIR = path.join(TOOLBOX, 'images');

// ── dual log output (if LOG_DIR env var set) ──
const LOG_DIR = process.env.LOG_DIR;
if(LOG_DIR){
  fs.mkdirSync(LOG_DIR, {recursive: true});
  const logFile = path.join(LOG_DIR, 'generate-report.log');
  const logStream = fs.createWriteStream(logFile, {flags: 'a'});
  const origLog = console.log;
  const origErr = console.error;
  console.log = function(...args){ origLog.apply(console, args); logStream.write(args.join(' ') + '\n'); };
  console.error = function(...args){ origErr.apply(console, args); logStream.write('[ERROR] ' + args.join(' ') + '\n'); };
}

const config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
const solo = config.actresses.filter(a => !a.type || a.type === 'solo');
console.log('[filter] Solo: ' + solo.length + ' actresses');

// ensure image directories exist
const heroesDir = path.join(IMAGES_DIR, 'heroes');
const worksDir = path.join(IMAGES_DIR, 'works');
fs.mkdirSync(heroesDir, { recursive: true });
fs.mkdirSync(worksDir, { recursive: true });

function detectExt(buf) {
  if (buf.length < 12) return '.jpg';
  if (buf[0] === 0x89 && buf[1] === 0x50 && buf[2] === 0x4E && buf[3] === 0x47) return '.png';
  if (buf[0] === 0xFF && buf[1] === 0xD8 && buf[2] === 0xFF) return '.jpg';
  if (buf[0] === 0x47 && buf[1] === 0x49 && buf[2] === 0x46) return '.gif';
  if (buf[0] === 0x52 && buf[1] === 0x49 && buf[2] === 0x46 && buf[3] === 0x46) {
    // RIFF -> check WEBP
    if (buf[8] === 0x57 && buf[9] === 0x45 && buf[10] === 0x42 && buf[11] === 0x50) return '.webp';
  }
  if (buf[0] === 0x52 && buf[1] === 0x49 && buf[2] === 0x46 && buf[3] === 0x46) return '.webp';
  return '.jpg';
}

function saveBase64(name, base64Str, outDir, fileBase) {
  if (!base64Str) return { success: false, path: '', ext: '' };
  const clean = base64Str.replace(/^data:image\/\w+;base64,/, '');
  if (clean.length < 200) return { success: false, path: '', ext: '' };
  try {
    const buf = Buffer.from(clean, 'base64');
    const ext = detectExt(buf);
    const outPath = path.join(outDir, fileBase + ext);
    fs.writeFileSync(outPath, buf);
    return { success: true, path: outPath, ext: ext };
  } catch (e) {
    console.error('[save] failed:', name, e.message);
    return { success: false, path: '', ext: '' };
  }
}

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function extractHashAttr(magnet) {
  const decoded = magnet.replace(/&amp;/g, '&');
  const m = decoded.match(/xt=urn:btih:([a-f0-9]{40})/i);
  return m ? m[1].toLowerCase() : '';
}

function rel(absPath) {
  const outDir = path.dirname(path.resolve(OUT_PATH));
  return path.relative(outDir, absPath).replace(/\\/g, '/');
}

async function generate(){
let totalWorks = 0;
let navHtml = '';
let cardsHtml = '';

// ── read data directly from DuckDB ──
const duckdb = require('duckdb');
const DB_DATA = {};
await new Promise(function(resolve, reject){
  const db = new duckdb.Database(path.join(TOOLBOX, 'data', 'claw.duckdb'));
  const conn = db.connect();
  conn.all(`
    SELECT
      a.code as actress_code,
      a.name,
      w.code as work_code,
      w.title,
      w.release_date,
      w.views,
      w.likes,
      w.resolution,
      w.download_url,
      w.cover_url,
      w.cover_b64,
      w.jable_m3u8,
      w.jable_cover,
      m.magnet
    FROM actresses a
    LEFT JOIN works w ON w.actress_id = a.id
    LEFT JOIN magnets m ON m.work_id = w.id AND m.is_primary = true
    ORDER BY a.name, w.release_date DESC
  `, function(err, rows){
    if(err){ db.close(); reject(err); return; }
    rows.forEach(function(r){
      var code = r.actress_code;
      if(!DB_DATA[code]) DB_DATA[code] = {name: r.name, works: [], posts: []};
      if(r.work_code){
        DB_DATA[code].works.push({
          code: r.work_code,
          title: r.title,
          date: r.release_date,
          views: String(r.views || ''),
          likes: String(r.likes || ''),
          resolution: r.resolution || '',
          download_url: r.download_url || '',
          cover_url: r.cover_url || '',
          cover_b64: r.cover_b64 || '',
          m3u8_url: r.jable_m3u8 || '',
          jable_cover: r.jable_cover || '',
          magnet: r.magnet || '',
        });
      }
    });
    // query social posts
    conn.all(`
      SELECT a.code as actress_code, s.platform, s.content, s.post_url, s.posted_at
      FROM social_posts s
      JOIN actresses a ON s.actress_id = a.id
      ORDER BY COALESCE(s.posted_at, s.created_at) DESC
    `, function(err2, rows2){
      if(err2){ db.close(); reject(err2); return; }
      rows2.forEach(function(r){
        var code = r.actress_code;
        if(!DB_DATA[code]) DB_DATA[code] = {name: '', works: [], posts: []};
        if(!DB_DATA[code].posts) DB_DATA[code].posts = [];
        DB_DATA[code].posts.push({
          platform: r.platform,
          content: r.content,
          url: r.post_url || '',
          posted_at: r.posted_at || '',
        });
      });
      db.close();
      resolve();
    });
  });
});

const actressData = solo.map(function(a) {
  const id = a.code.toLowerCase();
  const ijavWorks = DB_DATA[a.code] ? (DB_DATA[a.code].works || []) : [];
  var heroB64 = '';
  if(ijavWorks.length > 0){
    heroB64 = ijavWorks[0].cover_b64 || '';
  }

  let works = ijavWorks.map(function(w) {
    const codeUpper = w.code.toUpperCase();
    return {
      code: codeUpper,
      title: w.title,
      date: w.date || '',
      views: w.views || '',
      likes: w.likes || '',
      cover_b64: w.cover_b64 || '',
      cover_local: w.jable_cover || '',
      magnet: (w.magnet || '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>'),
      m3u8_url: w.m3u8_url || '',
      resolution: w.resolution || '',
    };
  });

  // sort by date descending (newest first)
  works.sort(function(a, b) {
    const da = a.date ? (a.date.split('/')[2] + a.date.split('/')[1] + a.date.split('/')[0]) : '00000000';
    const db = b.date ? (b.date.split('/')[2] + b.date.split('/')[1] + b.date.split('/')[0]) : '00000000';
    return db.localeCompare(da);
  });

  // limit to latest 3 works
  works = works.slice(0, 3);
  totalWorks += works.length;

  // social posts (dedup, max 3)
  const allPosts = DB_DATA[a.code] ? (DB_DATA[a.code].posts || []) : [];
  const seen = new Set();
  const posts = [];
  for (const p of allPosts) {
    if (!seen.has(p.content)) {
      seen.add(p.content);
      posts.push(p);
      if (posts.length >= 3) break;
    }
  }

  return {
    a: a,
    id: id,
    heroB64: heroB64,
    works: works,
    posts: posts,
  };
});

// sort entire stream by each actress's latest work date ascending
actressData.sort(function(ad, bd) {
  const da = ad.works.length > 0 && ad.works[0].date
    ? (ad.works[0].date.split('/')[2] + ad.works[0].date.split('/')[1] + ad.works[0].date.split('/')[0])
    : '99999999';
  const db = bd.works.length > 0 && bd.works[0].date
    ? (bd.works[0].date.split('/')[2] + bd.works[0].date.split('/')[1] + bd.works[0].date.split('/')[0])
    : '99999999';
  return da.localeCompare(db);
});


// assign global id (starting from 1)
var globalIdMap = {};
var gid = 1;
actressData.forEach(function(data) {
  data.works.forEach(function(w) {
    globalIdMap[w.code.toUpperCase()] = gid++;
  });
});

var heroBannerHtml = '';
var rowsHtml = '';

actressData.forEach(function(data) {
  const a = data.a;
  const id = data.id;
  const heroB64 = data.heroB64;
  const works = data.works;
  const initial = a.name.charAt(0);

  // save actress hero cover
  let heroSaved = { success: false };
  if (works.length > 0 && works[0].cover_local && fs.existsSync(works[0].cover_local)) {
    const src = works[0].cover_local;
    const ext = path.extname(src) || '.jpg';
    const dst = path.join(heroesDir, id + ext);
    try {
      fs.copyFileSync(src, dst);
      heroSaved = { success: true, path: dst, ext: ext };
    } catch (e) {}
  }
  if (!heroSaved.success) {
    heroSaved = saveBase64(id, heroB64, heroesDir, id);
  }
  if (heroSaved.success) {
    try {
      if (fs.statSync(heroSaved.path).size < 10000) {
        fs.unlinkSync(heroSaved.path);
        heroSaved.success = false;
      }
    } catch (e) {}
  }
  if (!heroSaved.success && works.length > 0 && works[0].cover_local && fs.existsSync(works[0].cover_local)) {
    const src = works[0].cover_local;
    const ext = path.extname(src) || '.jpg';
    const dst = path.join(heroesDir, id + ext);
    try {
      fs.copyFileSync(src, dst);
      heroSaved = { success: true, path: dst, ext: ext };
    } catch (e) {}
  }

  const hasHero = heroSaved.success;
  const heroRel = hasHero ? rel(heroSaved.path) : '';

  // nav item
  const navImg = hasHero
    ? `<img src="${esc(heroRel)}" alt="${esc(a.name)}" loading="lazy" decoding="async">`
    : `<span class="nav-initial">${initial}</span>`;
  navHtml += `  <a class="nav-item" href="#${id}" data-target="${id}" aria-label="${esc(a.name)}">`
           + `    <div class="nav-avatar">${navImg}</div>`
           + `    <span class="nav-label">${esc(a.name)}</span>`
           + `  </a>`;

  // work showcase
  const actressWorksDir = path.join(worksDir, id);
  fs.mkdirSync(actressWorksDir, { recursive: true });

  const workData = [];
  works.forEach(function(w) {
    let coverRel = heroRel;
    let hasWorkCover = false;

    if (w.cover_local && fs.existsSync(w.cover_local)) {
      const src = w.cover_local;
      const ext = path.extname(src) || '.jpg';
      const dst = path.join(actressWorksDir, w.code.toLowerCase() + ext);
      try {
        fs.copyFileSync(src, dst);
        coverRel = rel(dst);
        hasWorkCover = true;
      } catch (e) {}
    }
    if (!hasWorkCover && w.cover_b64) {
      const workSaved = saveBase64(w.code, w.cover_b64, actressWorksDir, w.code.toLowerCase());
      hasWorkCover = workSaved.success;
      if (hasWorkCover) coverRel = rel(workSaved.path);
    }
    if (hasWorkCover) {
      try {
        const absPath = path.resolve(path.dirname(path.resolve(OUT_PATH)), coverRel);
        if (fs.existsSync(absPath) && fs.statSync(absPath).size < 8000) {
          fs.unlinkSync(absPath);
          coverRel = heroRel;
          hasWorkCover = false;
        }
      } catch (e) {}
    }

    const hashAttr = w.magnet ? extractHashAttr(w.magnet) : '';
    const globalId = globalIdMap[w.code.toUpperCase()] || 0;
    const isPrefetchTarget = (globalId % 3 === 1);

    workData.push({
      code: w.code,
      title: w.title || '',
      date: w.date || '',
      resolution: w.resolution || '',
      magnet: w.magnet || '',
      coverRel: coverRel,
      hashAttr: hashAttr,
      globalId: globalId,
      isPrefetchTarget: isPrefetchTarget,
    });
  });

  // Fallback: if a work has no cover, use the first work's cover or hero
  var firstCover = '';
  workData.forEach(function(w){ if(w.coverRel && !firstCover) firstCover = w.coverRel; });
  if(!firstCover) firstCover = heroRel;
  workData.forEach(function(w){ if(!w.coverRel) w.coverRel = firstCover; });

  let showcaseHtml = '';
  let tabsHtml = '';
  let rowPrefetchClass = '';

  if (workData.length > 0) {
    const first = workData[0];
    if (first.isPrefetchTarget) rowPrefetchClass = 'prefetch-target';

    const res = first.resolution;
    const resBadge = res ? `<span class="res-badge">${esc(res)}</span>` : '';
    const hashAttr = first.hashAttr;
    const globalId = first.globalId;
    const cacheBadge = hashAttr ? `<span class="cache-badge pending ${first.isPrefetchTarget ? 'prefetch-target' : ''}" data-hash="${hashAttr}" data-id="${globalId}" title="Not cached"></span>` : '';

    let btnPlay = '', btnMagnet = '', btnCopy = '';
    if (first.magnet) {
      btnPlay = `<button class="btn-action btn-play" data-magnet="${esc(first.magnet)}"><svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M8 5v14l11-7z"/></svg><span>Play</span></button>`;
      btnMagnet = `<a class="btn-action btn-magnet" href="${esc(first.magnet)}" target="_blank" rel="noopener noreferrer"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M6 9l6 6 6-6"/></svg><span>Magnet</span></a>`;
      btnCopy = `<button class="btn-action btn-copy" data-magnet="${esc(first.magnet)}" title="Copy magnet link"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg></button>`;
    }

    const dateStr = first.date ? `<span class="featured-date">${esc(first.date)}</span>` : '';
    const descStr = first.title ? `<p class="featured-desc">${esc(first.title)}</p>` : '';

    const tabsInner = workData.map(function(w, idx){
      var thumbClass = idx === 0 ? 'work-tab active' : 'work-tab';
      var thumbSrc = w.coverRel ? esc(w.coverRel) : esc(heroRel);
      return `<button class="${thumbClass}" data-index="${idx}">`
           + `  <img src="${thumbSrc}" alt="${esc(w.code)}" loading="lazy" decoding="async">`
           + `</button>`;
    }).join('');

    showcaseHtml = `<div class="featured-showcase">`
                 + `  <div class="featured-media">`
                 + `    <img src="${esc(first.coverRel)}" alt="${esc(first.title || first.code)}" loading="lazy" decoding="async">`
                 + `  </div>`
                 + `  <div class="work-tabs">${tabsInner}</div>`
                 + `  <div class="featured-info">`
                 + `    <h3 class="featured-title">${esc(first.title || first.code)}</h3>`
                 + `    <div class="featured-meta">`
                 + `      ${dateStr}`
                 + `      <div class="featured-badges">${cacheBadge}<span class="id-badge">#${globalId}</span>${resBadge}</div>`
                 + `    </div>`
                 + `    <div class="featured-actions">${btnPlay}${btnMagnet}${btnCopy}</div>`
                 + `    ${descStr}`
                 + `  </div>`
                 + `</div>`;
  }

  // Hero banner (first actress with cover)
  if (!heroBannerHtml && hasHero) {
    const firstWork = works[0];
    const firstMagnet = firstWork ? firstWork.magnet : '';
    heroBannerHtml = `<section class="hero-banner" style="background-image:url('${esc(heroRel)}')">`
                   + `  <div class="hero-gradient"></div>`
                   + `  <div class="hero-content">`
                   + `    <h1 class="hero-title">${esc(a.name)}</h1>`
                   + `    <p class="hero-subtitle">${esc(a.jp)} · ${esc(a.code)}</p>`
                   + `    <p class="hero-desc">${works.length} latest works</p>`
                   + `    <div class="hero-actions">`
                   + `      <button class="hero-btn hero-btn-primary btn-play" data-magnet="${esc(firstMagnet)}"><svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>Play Now</button>`
                   + `      <a class="hero-btn hero-btn-secondary" href="${esc(firstMagnet)}" target="_blank" rel="noopener noreferrer"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M6 9l6 6 6-6"/></svg>Magnet Link</a>`
                   + `    </div>`
                   + `  </div>`
                   + `</section>`;
  }

  // social feed HTML
  let socialHtml = '';
  if (data.posts.length > 0) {
    socialHtml = `<div class="social-feed">`
               + `  <div class="social-feed-header">`
               + `    <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>`
               + `    <span>Latest</span>`
               + `  </div>`
               + `  <div class="social-posts">`;
    data.posts.forEach(function(p) {
      const url = p.url || `https://x.com/${a.handle}`;
      socialHtml += `<a class="social-post" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(p.content)}</a>`;
    });
    socialHtml += `  </div></div>`;
  }

  rowsHtml += `<section class="actor-row ${rowPrefetchClass}" id="${id}" data-name="${esc(a.name)} ${esc(a.jp)} ${a.code}" data-works="${esc(JSON.stringify(workData))}">`
            + `  <h2 class="actor-title">`
            + `    <span class="actor-name">${esc(a.name)}</span>`
            + `    <span class="actor-jp">${esc(a.jp)}</span>`
            + `    <span class="actor-code">${a.code}</span>`
            + `  </h2>`
            + socialHtml
            + showcaseHtml
            + tabsHtml
            + `</section>`;
});

// build full HTML
const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${esc(config.title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Noto+Sans+SC:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
/* ===== Premium Cinematic Theme ===== */
:root{
  --bg:#f8f7f4;
  --surface:#ffffff;
  --surface-hover:#f5f5f2;
  --text-primary:#0f0f0f;
  --text-secondary:#4a4a4a;
  --text-tertiary:#8a8a8a;
  --accent:#c41e3a;
  --accent-green:#22c55e;
  --accent-blue:#3b82f6;
  --accent-gold:#f59e0b;
  --border:rgba(0,0,0,0.06);
  --border-light:rgba(0,0,0,0.04);
  --shadow:0 1px 2px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.06);
  --shadow-lg:0 8px 30px rgba(0,0,0,0.08), 0 2px 8px rgba(0,0,0,0.04);
  --shadow-xl:0 24px 80px rgba(0,0,0,0.12);
  --radius:12px;
  --radius-lg:20px;
  --transition:all 0.35s cubic-bezier(0.4,0,0.2,1);
}
[data-theme="dark"]{
  --bg:#0c0c14;
  --surface:#161622;
  --surface-hover:#1e1e2e;
  --text-primary:#f0f0f5;
  --text-secondary:#a0a0b8;
  --text-tertiary:#606070;
  --accent:#ff4757;
  --accent-green:#22c55e;
  --accent-blue:#3b82f6;
  --accent-gold:#f59e0b;
  --border:rgba(255,255,255,0.06);
  --border-light:rgba(255,255,255,0.04);
  --shadow:0 1px 2px rgba(0,0,0,0.2), 0 4px 12px rgba(0,0,0,0.3);
  --shadow-lg:0 8px 30px rgba(0,0,0,0.4), 0 2px 8px rgba(0,0,0,0.2);
  --shadow-xl:0 24px 80px rgba(0,0,0,0.6);
}
*{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display','Inter','Noto Sans SC',sans-serif;
  background:var(--bg);color:var(--text-primary);line-height:1.5;
  -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;
}
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--text-tertiary);border-radius:3px;opacity:0.4}
::-webkit-scrollbar-thumb:hover{background:var(--text-secondary);opacity:0.7}

/* Floating Pill Nav */
.top-nav{
  position:fixed;top:20px;left:50%;transform:translateX(-50%);
  z-index:100;max-width:900px;width:calc(100% - 40px);height:64px;
  background:rgba(255,255,255,0.72);backdrop-filter:blur(24px) saturate(160%);
  -webkit-backdrop-filter:blur(24px) saturate(160%);
  border:1px solid var(--border);border-radius:100px;
  padding:0 20px;display:flex;align-items:center;gap:16px;
  box-shadow:var(--shadow-lg);transition:transform 0.4s cubic-bezier(0.4,0,0.2,1), background 0.3s;
}
[data-theme="dark"] .top-nav{background:rgba(22,22,34,0.72)}
.nav-brand{font-size:1.1rem;font-weight:800;color:var(--accent);letter-spacing:-0.3px;white-space:nowrap;flex-shrink:0}
.nav-scroll{flex:1;overflow-x:auto;display:flex;gap:6px;scrollbar-width:none;align-items:center;mask-image:linear-gradient(to right, transparent, black 8px, black calc(100% - 8px), transparent);-webkit-mask-image:linear-gradient(to right, transparent, black 8px, black calc(100% - 8px), transparent)}
.nav-scroll::-webkit-scrollbar{display:none}
.nav-item{flex-shrink:0;display:flex;flex-direction:column;align-items:center;gap:3px;padding:5px 8px;border-radius:12px;transition:background .2s, transform .2s;text-decoration:none;color:inherit}
.nav-item:hover{background:rgba(0,0,0,0.04)}
.nav-item.active{background:rgba(196,30,58,0.08)}
[data-theme="dark"] .nav-item:hover{background:rgba(255,255,255,0.04)}
[data-theme="dark"] .nav-item.active{background:rgba(255,71,87,0.1)}
.nav-avatar{width:44px;height:44px;border-radius:50%;overflow:hidden;border:2px solid var(--border);background:var(--surface);transition:transform .3s cubic-bezier(0.4,0,0.2,1), border-color .3s}
.nav-item:hover .nav-avatar{border-color:var(--accent);transform:scale(1.06)}
.nav-avatar img{width:100%;height:100%;object-fit:cover}
.nav-initial{width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:0.85rem;color:var(--text-secondary)}
.nav-label{font-size:0.6rem;color:var(--text-tertiary);font-weight:500;white-space:nowrap;max-width:56px;overflow:hidden;text-overflow:ellipsis}
.nav-search-btn,.nav-theme-btn,.nav-magnet-btn,.nav-refresh-btn{width:40px;height:40px;border-radius:50%;border:none;background:rgba(0,0,0,0.04);color:var(--text-secondary);cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background .2s, color .2s, transform .3s;flex-shrink:0}
.nav-search-btn:hover,.nav-theme-btn:hover,.nav-magnet-btn:hover,.nav-refresh-btn:hover{background:rgba(0,0,0,0.08);color:var(--text-primary)}
.nav-theme-btn:hover{transform:rotate(30deg)}
.nav-refresh-btn:hover{transform:rotate(180deg)}
.nav-refresh-btn.spinning{animation:spin 1s linear infinite}
@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}

/* Search Bar */
.search-bar{position:fixed;top:92px;left:50%;transform:translateX(-50%) translateY(-20px);z-index:99;max-width:860px;width:calc(100% - 80px);background:rgba(255,255,255,0.85);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);border:1px solid var(--border);border-radius:100px;padding:10px 24px;box-shadow:var(--shadow-lg);opacity:0;pointer-events:none;transition:opacity .3s ease, transform .3s ease}
[data-theme="dark"] .search-bar{background:rgba(22,22,34,0.85)}
.search-bar.open{opacity:1;pointer-events:auto;transform:translateX(-50%) translateY(0)}
.search-input{width:100%;display:block;background:transparent;border:none;padding:6px 4px;color:var(--text-primary);font-size:0.95rem;outline:none;font-family:inherit}
.search-input::placeholder{color:var(--text-tertiary)}

/* Hero Banner */
.hero-banner{position:relative;height:70vh;min-height:480px;max-height:800px;background-size:cover;background-position:center top;margin-top:0;display:flex;align-items:flex-end}
.hero-gradient{position:absolute;inset:0;background:linear-gradient(to top, rgba(15,15,15,0.95) 0%, rgba(15,15,15,0.6) 45%, rgba(15,15,15,0.15) 75%, transparent 100%)}
.hero-content{position:relative;z-index:2;padding:0 4vw 80px;width:100%;max-width:900px;margin:0 auto}
.hero-title{font-size:3.5rem;font-weight:800;margin-bottom:10px;letter-spacing:-1.5px;line-height:1.1;color:#fff}
.hero-subtitle{font-size:1.1rem;color:rgba(255,255,255,0.75);margin-bottom:10px;font-weight:400}
.hero-desc{font-size:0.95rem;color:rgba(255,255,255,0.55);margin-bottom:28px}
.hero-actions{display:flex;gap:14px;flex-wrap:wrap}
.hero-btn{display:inline-flex;align-items:center;gap:8px;padding:12px 28px;border-radius:100px;font-size:0.95rem;font-weight:600;text-decoration:none;border:none;cursor:pointer;transition:transform .25s, box-shadow .25s, opacity .25s}
.hero-btn:hover{transform:translateY(-2px); box-shadow:0 8px 24px rgba(0,0,0,0.25)}
.hero-btn-primary{background:var(--accent);color:#fff}
.hero-btn-secondary{background:rgba(255,255,255,0.12);color:#fff;backdrop-filter:blur(10px);border:1px solid rgba(255,255,255,0.15)}
.hero-btn-secondary:hover{background:rgba(255,255,255,0.2)}

/* Actor Row */
.actor-row{padding:80px 4vw;scroll-margin-top:100px}
.actor-row.hidden{display:none}
.actor-title{display:flex;align-items:center;gap:16px;margin-bottom:32px;position:relative}
.actor-title::before{content:'';width:4px;height:32px;background:var(--accent);border-radius:2px;flex-shrink:0}
.actor-name{font-size:1.8rem;font-weight:800;letter-spacing:-0.5px;line-height:1.2}
.actor-jp{font-size:1rem;color:var(--text-secondary);font-weight:500}
.actor-code{font-size:0.75rem;color:var(--text-tertiary);background:var(--surface);padding:4px 12px;border-radius:100px;border:1px solid var(--border);font-weight:600;letter-spacing:0.5px}

/* Social Feed */
.social-feed{margin-bottom:24px}
.social-feed-header{display:flex;align-items:center;gap:6px;font-size:0.7rem;font-weight:700;color:var(--text-tertiary);margin-bottom:10px;text-transform:uppercase;letter-spacing:0.8px}
.social-feed-header svg{color:var(--text-secondary)}
.social-posts{display:flex;flex-direction:column;gap:8px}
.social-post{display:block;font-size:0.85rem;color:var(--text-secondary);line-height:1.5;padding:14px 18px;background:var(--surface);border-radius:var(--radius);border:1px solid var(--border);text-decoration:none;transition:background .2s, border-color .2s, transform .2s, box-shadow .2s;max-width:640px}
.social-post:hover{background:var(--surface-hover);border-color:var(--border-light);color:var(--text-primary);transform:translateY(-1px);box-shadow:var(--shadow)}
.social-post{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}

/* Featured Showcase */
/* Featured Showcase — Gallery Layout */
.featured-showcase{display:flex;flex-direction:column;align-items:center;max-width:640px;margin:0 auto}
.featured-media{width:100%;max-width:520px;border-radius:var(--radius-lg);overflow:hidden;background:var(--surface);border:1px solid var(--border);box-shadow:var(--shadow-lg)}
.featured-media img{width:100%;height:auto;display:block}
.actor-row.prefetch-target .featured-media{position:relative}
.actor-row.prefetch-target .featured-media::before{content:'';position:absolute;inset:0;border-radius:var(--radius-lg);border:2px solid var(--accent-gold);pointer-events:none;z-index:5;opacity:0.5}

.featured-info{display:flex;flex-direction:column;align-items:center;gap:16px;margin-top:32px;text-align:center;width:100%;max-width:520px}
.featured-title{font-size:1.2rem;font-weight:700;color:var(--text-primary);line-height:1.4;letter-spacing:-0.3px;text-align:center}
.featured-meta{display:flex;align-items:center;justify-content:center;gap:14px;flex-wrap:wrap}
.featured-date{font-size:0.85rem;color:var(--text-secondary);font-weight:500}
.featured-badges{display:flex;align-items:center;gap:8px}
.id-badge{font-size:0.7rem;font-weight:700;color:#fff;background:rgba(0,0,0,0.45);padding:3px 8px;border-radius:6px;backdrop-filter:blur(4px)}
.featured-actions{display:flex;gap:12px;flex-wrap:wrap;justify-content:center;margin-top:4px}
.btn-action{display:inline-flex;align-items:center;gap:6px;padding:10px 20px;border-radius:100px;font-size:0.85rem;font-weight:600;border:none;cursor:pointer;text-decoration:none;transition:transform .2s, box-shadow .2s, opacity .2s}
.btn-action:hover{transform:translateY(-2px);box-shadow:0 6px 16px rgba(0,0,0,0.15)}
.btn-play{background:var(--accent);color:#fff}
.btn-magnet{background:var(--surface-hover);color:var(--text-primary);border:1px solid var(--border)}
.btn-magnet:hover{background:var(--border-light)}
.btn-copy{background:var(--surface-hover);color:var(--text-secondary);border:1px solid var(--border);padding:8px 14px}
.btn-copy:hover{background:var(--border-light);color:var(--text-primary)}
.res-badge{font-size:0.7rem;font-weight:700;color:#fff;background:rgba(59,130,246,0.8);padding:3px 8px;border-radius:4px;letter-spacing:0.3px}
.featured-desc{font-size:0.9rem;color:var(--text-secondary);line-height:1.6;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;font-weight:500;text-align:center;margin-top:4px}

/* Work Tabs */
.work-tabs{display:flex;gap:14px;margin-top:24px;justify-content:center;flex-wrap:wrap}
.work-tab{background:none;border:2px solid transparent;border-radius:10px;padding:3px;cursor:pointer;transition:all .25s cubic-bezier(0.4,0,0.2,1);opacity:0.55}
.work-tab img{width:72px;height:auto;display:block;border-radius:7px}
.work-tab:hover{opacity:0.85;transform:translateY(-2px)}
.work-tab.active{opacity:1;border-color:var(--accent);box-shadow:0 4px 16px rgba(196,30,58,0.18);transform:translateY(-2px)}
[data-theme="dark"] .work-tab.active{box-shadow:0 4px 16px rgba(255,71,87,0.18)}

/* Cache Badge */
.cache-badge{width:10px;height:10px;border-radius:50%;display:inline-block;transition:box-shadow .3s}
.cache-badge.pending{background:var(--text-tertiary);box-shadow:0 0 0 0 transparent}
.cache-badge.downloading{background:var(--accent-blue);box-shadow:0 0 10px var(--accent-blue);animation:pulse 1.5s infinite}
.cache-badge.cached{background:var(--accent-green);box-shadow:0 0 10px var(--accent-green)}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(59,130,246,0.5)}70%{box-shadow:0 0 0 8px rgba(59,130,246,0)}100%{box-shadow:0 0 0 0 rgba(59,130,246,0)}}

/* Stats Bar */
.stats-bar{display:flex;justify-content:center;gap:56px;padding:40px 4vw;background:transparent}
.stat{display:flex;flex-direction:column;align-items:center;gap:6px;text-align:center}
.stat-num{font-size:2rem;font-weight:800;color:var(--accent);line-height:1;letter-spacing:-1px}
.stat-label{font-size:0.8rem;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:1px;font-weight:600}

/* Cache Panel */
.cache-panel{position:fixed;bottom:0;left:0;right:0;z-index:200;background:rgba(248,247,244,0.92);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);border-top:1px solid var(--border);transition:transform .35s cubic-bezier(0.4,0,0.2,1);box-shadow:0 -4px 24px rgba(0,0,0,0.04)}
[data-theme="dark"] .cache-panel{background:rgba(12,12,20,0.92)}
.cache-panel-header{display:flex;align-items:center;justify-content:space-between;padding:14px 4vw;cursor:pointer;user-select:none}
.cache-panel-header:hover{background:rgba(0,0,0,0.02)}
[data-theme="dark"] .cache-panel-header:hover{background:rgba(255,255,255,0.02)}
.cache-panel-body{max-height:0;overflow:hidden;transition:max-height .35s cubic-bezier(0.4,0,0.2,1), padding .35s;padding:0 4vw}
.cache-panel-body.open{max-height:420px;padding-bottom:20px;overflow-y:auto}
.cache-list{display:flex;flex-direction:column;gap:10px}
.cache-item{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:12px 16px;background:var(--surface);border-radius:var(--radius);border:1px solid var(--border);transition:transform .2s, box-shadow .2s}
.cache-item:hover{transform:translateY(-1px);box-shadow:var(--shadow)}
.cache-item-name{font-size:0.85rem;font-weight:600;color:var(--text-primary);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cache-item-meta{font-size:0.75rem;color:var(--text-tertiary);margin-top:2px}
.cache-item-bar{flex:1;height:5px;background:var(--border);border-radius:3px;overflow:hidden;max-width:140px}
.cache-item-bar-inner{height:100%;border-radius:3px;transition:width .3s}
.cache-item-del{font-size:0.75rem;padding:5px 12px;border-radius:6px;border:1px solid var(--border);background:transparent;color:var(--text-secondary);cursor:pointer;transition:all .2s;font-weight:600}
.cache-item-del:hover{background:var(--accent);color:#fff;border-color:var(--accent)}
.cache-clear-btn{font-size:0.85rem;padding:10px 20px;border-radius:var(--radius);border:none;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;margin-top:14px;transition:opacity .2s, transform .2s}
.cache-clear-btn:hover{opacity:0.9;transform:translateY(-1px)}

/* Video Modal */
.video-modal-overlay{position:fixed;inset:0;z-index:300;background:rgba(0,0,0,0.88);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);display:flex;align-items:center;justify-content:center;opacity:0;pointer-events:none;transition:opacity .4s ease}
.video-modal-overlay.active{opacity:1;pointer-events:auto}
.video-modal-box{position:relative;width:90vw;max-width:1200px;aspect-ratio:16/9;background:var(--surface);border-radius:var(--radius-lg);overflow:hidden;box-shadow:0 24px 80px rgba(0,0,0,0.6);border:1px solid rgba(255,255,255,0.08);transform:scale(0.96);transition:transform .4s cubic-bezier(0.4,0,0.2,1)}
.video-modal-overlay.active .video-modal-box{transform:scale(1)}
.video-modal-box video{width:100%;height:100%;object-fit:contain;background:#000}
.video-modal-close{position:absolute;top:16px;right:16px;z-index:10;width:44px;height:44px;border-radius:50%;border:none;background:rgba(0,0,0,0.5);color:#fff;font-size:1.5rem;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background .2s, transform .2s;backdrop-filter:blur(10px)}
.video-modal-close:hover{background:rgba(0,0,0,0.7);transform:rotate(90deg)}
.video-modal-loading{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;background:rgba(0,0,0,0.8);color:#fff;font-size:0.95rem;z-index:5}
.video-modal-loading::before{content:'';width:40px;height:40px;border-radius:50%;border:3px solid rgba(255,255,255,0.15);border-top-color:var(--accent);animation:spin 1s linear infinite}

/* Magnet Modal */
.magnet-modal-overlay{position:fixed;inset:0;z-index:310;background:rgba(0,0,0,0.5);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);display:flex;align-items:center;justify-content:center;opacity:0;pointer-events:none;transition:opacity .35s ease}
.magnet-modal-overlay.active{opacity:1;pointer-events:auto}
.magnet-modal-box{position:relative;width:90vw;max-width:520px;background:var(--surface);border-radius:var(--radius-lg);border:1px solid var(--border);box-shadow:var(--shadow-xl);padding:36px;transform:translateY(20px) scale(0.98);transition:transform .35s cubic-bezier(0.4,0,0.2,1)}
.magnet-modal-overlay.active .magnet-modal-box{transform:translateY(0) scale(1)}
.magnet-modal-close{position:absolute;top:14px;right:14px;width:40px;height:40px;border-radius:50%;border:none;background:transparent;color:var(--text-secondary);font-size:1.5rem;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background .2s, color .2s, transform .2s}
.magnet-modal-close:hover{background:var(--surface-hover);color:var(--text-primary);transform:rotate(90deg)}
.magnet-modal-title{font-size:1.35rem;font-weight:800;margin-bottom:8px;color:var(--text-primary);letter-spacing:-0.5px}
.magnet-modal-desc{font-size:0.9rem;color:var(--text-secondary);margin-bottom:24px;line-height:1.5}
.magnet-input{width:100%;background:var(--surface-hover);border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px;color:var(--text-primary);font-size:0.9rem;font-family:inherit;resize:vertical;outline:none;transition:border-color .2s, box-shadow .2s}
.magnet-input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(196,30,58,0.1)}
[data-theme="dark"] .magnet-input:focus{box-shadow:0 0 0 3px rgba(255,71,87,0.15)}
.magnet-input::placeholder{color:var(--text-tertiary)}
.magnet-modal-actions{display:flex;gap:12px;margin-top:20px}
.magnet-play-btn{display:inline-flex;align-items:center;gap:8px;padding:12px 28px;border-radius:100px;font-size:0.95rem;font-weight:600;border:none;cursor:pointer;background:var(--accent);color:#fff;transition:opacity .2s, transform .2s, box-shadow .2s}
.magnet-play-btn:hover{opacity:0.9;transform:translateY(-2px);box-shadow:0 8px 24px rgba(196,30,58,0.3)}
[data-theme="dark"] .magnet-play-btn:hover{box-shadow:0 8px 24px rgba(255,71,87,0.3)}
.magnet-modal-hint{margin-top:18px;font-size:0.8rem;color:var(--text-tertiary);line-height:1.5}

/* Back to Top */
.back-to-top{position:fixed;bottom:100px;right:28px;z-index:90;padding:12px 20px;border-radius:100px;border:none;background:var(--surface);color:var(--text-primary);border:1px solid var(--border);cursor:pointer;display:flex;align-items:center;gap:8px;opacity:0;transform:translateY(20px);transition:var(--transition);box-shadow:var(--shadow-lg);font-size:0.85rem;font-weight:600}
.back-to-top.visible{opacity:1;transform:translateY(0)}
.back-to-top:hover{background:var(--surface-hover);border-color:var(--accent);color:var(--accent);transform:translateY(-2px)}
.back-to-top::after{content:'Top'}

/* Toast */
#toast{position:fixed;bottom:110px;left:50%;transform:translateX(-50%) translateY(20px);background:var(--surface);color:var(--text-primary);padding:14px 28px;border-radius:100px;font-size:0.9rem;z-index:9999;opacity:0;transition:all 0.35s cubic-bezier(0.4,0,0.2,1);pointer-events:none;white-space:nowrap;border:1px solid var(--border);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);box-shadow:var(--shadow-xl);font-weight:600}

/* Responsive */
@media(max-width:768px){
  .hero-title{font-size:2.2rem}
  .hero-banner{height:55vh;min-height:380px}
  .featured-showcase{grid-template-columns:1fr}
  .featured-info{padding:4px 0}
  .work-tab img{width:64px}
  .top-nav{padding:0 14px;height:56px}
  .actor-row{padding:48px 16px}
  .hero-content{padding:0 16px 48px}
  .actor-name{font-size:1.4rem}
  .stats-bar{gap:32px}
  .stat-num{font-size:1.6rem}
}
</style>
</head>
<body>

<nav class="top-nav">
  <div class="nav-brand">${esc(config.title)}</div>
  <div class="nav-scroll" id="siteNav">${navHtml}</div>
  <button class="nav-search-btn" id="searchToggle" aria-label="Search">
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
  </button>
  <button class="nav-magnet-btn" id="magnetToggle" aria-label="Magnet Player">
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>
  </button>
  <button class="nav-refresh-btn" id="refreshToggle" aria-label="Refresh" title="Re-fetch data and regenerate">
    <span id="refreshIcon">🔄</span>
  </button>
  <button class="nav-theme-btn" id="themeToggle" aria-label="Toggle theme">
    <svg class="theme-icon-sun" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>
    <svg class="theme-icon-moon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.5" style="display:none"><path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/></svg>
  </button>
</nav>

<div class="search-bar" id="searchBar">
  <input type="search" class="search-input" id="searchInput" placeholder="Search actress, JP name or code..." autocomplete="off">
</div>

${heroBannerHtml}

<div class="stats-bar">
  <div class="stat"><div class="stat-num">${solo.length}</div><div class="stat-label">Actresses</div></div>
  <div class="stat"><div class="stat-num">${totalWorks}</div><div class="stat-label">Works</div></div>
</div>

<main id="main">${rowsHtml}</main>

<!-- Cache Panel -->
<div class="cache-panel" id="cachePanel">
  <div class="cache-panel-header" id="cachePanelHeader">
    <div style="display:flex;align-items:center;gap:12px">
      <h3 style="font-size:0.95rem;font-weight:700;letter-spacing:0.3px">📦 Cache Manager</h3>
      <span class="cache-item-meta" id="cacheSummary">Loading...</span>
    </div>
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 18l6-6-6-6"/></svg>
  </div>
  <div class="cache-panel-body" id="cachePanelBody">
    <div class="cache-list" id="cacheList"></div>
    <div style="display:flex;gap:8px;margin-top:12px">
      <button class="cache-clear-btn" id="cacheClearBtn" style="display:none;flex:1">Clear All</button>
      <button class="cache-clear-btn" id="viewLogsBtn" style="flex:1;background:var(--surface-hover);color:var(--text-secondary);border:1px solid var(--border)">📋 View Logs</button>
    </div>
  </div>
</div>

<button class="back-to-top" id="backToTop" aria-label="Back to top">
  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 15l-6-6-6 6"/></svg>
</button>

<!-- Video Modal -->
<div class="video-modal-overlay" id="videoModal">
  <div class="video-modal-box">
    <button class="video-modal-close" id="modalClose" aria-label="Close">&times;</button>
    <video id="modalVideo" controls playsinline></video>
    <div class="video-modal-loading" id="modalLoading" style="display:none">
      <span>Loading video...</span>
    </div>
  </div>
</div>

<!-- Magnet Player Modal -->
<div class="magnet-modal-overlay" id="magnetModal">
  <div class="magnet-modal-box">
    <button class="magnet-modal-close" id="magnetModalClose" aria-label="Close">&times;</button>
    <div class="magnet-modal-content">
      <h2 class="magnet-modal-title">🧲 Magnet Player</h2>
      <p class="magnet-modal-desc">Paste any magnet link to stream via local cache.</p>
      <textarea class="magnet-input" id="magnetInput" placeholder="magnet:?xt=urn:btih:..." rows="3"></textarea>
      <div class="magnet-modal-actions">
        <button class="magnet-play-btn" id="magnetPlayBtn">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
          <span>Play Now</span>
        </button>
      </div>
      <div class="magnet-modal-hint">
        <span>💡 Tip: If cached, plays instantly; otherwise wait for header (~30-60s)</span>
      </div>
    </div>
  </div>
</div>

<script>
(function(){
  'use strict';

  // ===== Global Error Reporter =====
  function reportError(level, message, extra){
    var payload = JSON.stringify({level: level, message: message, extra: extra || {}, url: location.href, ts: new Date().toISOString()});
    if(navigator.sendBeacon){
      navigator.sendBeacon('/api/log', new Blob([payload], {type: 'application/json'}));
    }else{
      fetch('/api/log', {method: 'POST', body: payload, headers: {'Content-Type': 'application/json'}}).catch(function(){});
    }
  }
  window.addEventListener('error', function(e){
    reportError('error', e.message, {filename: e.filename, lineno: e.lineno, colno: e.colno});
  });
  window.addEventListener('unhandledrejection', function(e){
    reportError('error', String(e.reason), {type: 'unhandledrejection'});
  });

  // ===== Theme Toggle =====
  var themeToggle = document.getElementById('themeToggle');
  var sunIcon = themeToggle.querySelector('.theme-icon-sun');
  var moonIcon = themeToggle.querySelector('.theme-icon-moon');
  var currentTheme = localStorage.getItem('theme') || 'light';

  function applyTheme(theme){
    document.documentElement.setAttribute('data-theme', theme);
    if(theme === 'dark'){
      sunIcon.style.display = 'none';
      moonIcon.style.display = '';
    } else {
      sunIcon.style.display = '';
      moonIcon.style.display = 'none';
    }
    localStorage.setItem('theme', theme);
  }
  applyTheme(currentTheme);

  themeToggle.addEventListener('click', function(){
    var next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    applyTheme(next);
  });

  // ===== Search Toggle =====
  var searchToggle = document.getElementById('searchToggle');
  var searchBar = document.getElementById('searchBar');
  var searchInput = document.getElementById('searchInput');
  searchToggle.addEventListener('click', function(){
    searchBar.classList.toggle('open');
    if(searchBar.classList.contains('open')) searchInput.focus();
  });

  // ===== Search Filter =====
  searchInput.addEventListener('input', function(){
    var q = this.value.trim().toLowerCase();
    document.querySelectorAll('.actor-row').forEach(function(row){
      var name = (row.getAttribute('data-name') || '').toLowerCase();
      var visible = !q || name.includes(q);
      row.classList.toggle('hidden', !visible);
    });
    var visibleRows = document.querySelectorAll('.actor-row:not(.hidden)');
    var visibleIds = new Set(Array.from(visibleRows).map(function(r){ return r.id; }));
    document.querySelectorAll('.nav-item').forEach(function(n){
      n.style.display = visibleIds.has(n.getAttribute('data-target')) ? '' : 'none';
    });
  });

  // ===== Nav Highlight =====
  var navItems = document.querySelectorAll('.nav-item');
  var observer = new IntersectionObserver(function(entries){
    entries.forEach(function(entry){
      if(entry.isIntersecting){
        navItems.forEach(function(n){ n.classList.remove('active'); });
        var id = entry.target.id;
        var active = document.querySelector('.nav-item[data-target="'+id+'"]');
        if(active) active.classList.add('active');
      }
    });
  }, { rootMargin: '-40% 0px -40% 0px', threshold: 0 });
  document.querySelectorAll('.actor-row').forEach(function(row){ observer.observe(row); });

  // ===== Cache Management =====
  function escHtml(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
  var cacheState = {};

  function formatBytes(b){
    if(b === 0) return '0 B';
    var units = ['B','KB','MB','GB','TB'];
    var i = Math.floor(Math.log(b) / Math.log(1024));
    return (b / Math.pow(1024, i)).toFixed(i < 2 ? 0 : 1) + ' ' + units[i];
  }

  function updateCacheBadges(){
    document.querySelectorAll('.cache-badge').forEach(function(badge){
      var hash = badge.getAttribute('data-hash');
      if(!hash) return;
      var st = cacheState[hash];
      badge.classList.remove('cached','downloading','pending');
      if(st && st.head_ready){
        badge.classList.add('cached');
        badge.title = 'Ready | ' + formatBytes(st.size || 0);
      } else if(st && st.downloading){
        badge.classList.add('downloading');
        badge.title = 'Downloading ' + (st.progress || 0).toFixed(1) + '% | Peers: ' + (st.peers || 0);
      } else {
        badge.classList.add('pending');
        badge.title = 'Not cached';
      }
    });
  }

  function updateCachePanel(){
    var list = document.getElementById('cacheList');
    var summary = document.getElementById('cacheSummary');
    var clearBtn = document.getElementById('cacheClearBtn');
    if(!list) return;
    var items = Object.values(cacheState).filter(function(s){ return s.ready || s.downloading; });
    var cachedCount = items.filter(function(s){ return s.cached; }).length;
    var totalSize = items.reduce(function(sum, s){ return sum + (s.size || 0); }, 0);
    summary.textContent = cachedCount + ' cached / ' + items.length + ' tasks';
    clearBtn.style.display = items.length > 0 ? '' : 'none';
    if(items.length === 0){
      list.innerHTML = '<div style="text-align:center;color:var(--text-tertiary);font-size:0.8rem;padding:20px">No cache tasks</div>';
      return;
    }
    list.innerHTML = items.map(function(s){
      var pct = s.cached ? 100 : (s.progress || 0);
      var isCached = s.cached;
      var cardId = '';
      var badge = document.querySelector('.cache-badge[data-hash="' + s.hash + '"]');
      if(badge) cardId = '#' + (badge.getAttribute('data-id') || '');
      return '<div class="cache-item" data-hash="' + s.hash + '">'
        + '<div>'
        +   '<div class="cache-item-name">' + escHtml(s.name || s.hash.slice(0,12)) + '</div>'
        +   '<div class="cache-item-meta">' + cardId + ' ' + (isCached ? '✅ ' : '📥 ') + formatBytes(s.size || 0)
        +     (s.video_size ? ' / ' + formatBytes(s.video_size) : '') + '</div>'
        + '</div>'
        + '<div style="display:flex;align-items:center;gap:12px">'
        +   '<div class="cache-item-bar"><div class="cache-item-bar-inner" style="width:' + pct + '%;background:' + (isCached ? '#22c55e' : '#3b82f6') + '"></div></div>'
        +   '<button class="cache-item-del" data-hash="' + s.hash + '">Del</button>'
        + '</div>'
        + '</div>';
    }).join('');
    list.querySelectorAll('.cache-item-del').forEach(function(btn){
      btn.addEventListener('click', function(){
        var h = this.getAttribute('data-hash');
        if(!h || !confirm('Delete this cache item?')) return;
        fetch('/api/cache/' + h, { method: 'DELETE' })
          .then(function(r){ return r.json(); })
          .then(function(data){
            if(data.deleted){ delete cacheState[h]; updateCacheBadges(); updateCachePanel(); showToast('Deleted'); }
          }).catch(function(){ showToast('Delete failed'); });
      });
    });
  }

  function pollCacheState(){
    fetch('/api/cache')
      .then(function(r){ return r.json(); })
      .then(function(data){
        if(data.items){
          data.items.forEach(function(item){
            var st = cacheState[item.hash] || {};
            cacheState[item.hash] = {
              cached: item.cached, head_ready: item.head_ready,
              downloading: item.progress < 100 && item.peers > 0,
              ready: item.ready, progress: item.progress, peers: item.peers,
              size: item.local_size, video_size: item.video_size,
              name: item.video_file || item.name || item.hash.slice(0,12), hash: item.hash
            };
          });
          updateCacheBadges(); updateCachePanel();
        }
      }).catch(function(err){ console.error('Cache poll error:', err); });
  }

  function prefetchAll(){
    var magnets = [];
    var seen = new Set();
    document.querySelectorAll('[data-magnet]').forEach(function(el){
      var m = el.getAttribute('data-magnet');
      var h = extractHash(m);
      if(h && !seen.has(h)){ seen.add(h); magnets.push(m); }
    });
    var prefetchMagnets = magnets.slice(0, 13);
    console.log('[prefetch] ' + prefetchMagnets.length + ' / ' + magnets.length + ' magnets');
    var idx = 0;
    function next(){
      if(idx >= prefetchMagnets.length) return;
      var m = prefetchMagnets[idx++];
      fetch('/torrent/add', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ magnet: m, prefetch: true })
      }).catch(function(err){});
      setTimeout(next, 500);
    }
    next();
    checkAllCaches();
    setTimeout(function(){ pollCacheState(); }, 5000);
    setInterval(pollCacheState, 10000);
  }

  function checkAllCaches(){
    var hashes = [];
    document.querySelectorAll('[data-magnet]').forEach(function(el){
      var m = el.getAttribute('data-magnet');
      var h = extractHash(m);
      if(h && hashes.indexOf(h) < 0) hashes.push(h);
    });
    var batchSize = 5;
    function checkBatch(start){
      var batch = hashes.slice(start, start + batchSize);
      if(batch.length === 0) return;
      var done = 0;
      batch.forEach(function(h){
        fetch('/api/check/' + h)
          .then(function(r){ return r.json(); })
          .then(function(data){
            var st = cacheState[h] || {};
            cacheState[h] = {
              cached: !!data.cached, head_ready: !!data.head_ready,
              downloading: st.downloading || false, ready: st.ready || false,
              progress: st.progress || 0, peers: st.peers || 0,
              size: data.size || 0, name: data.name || st.name || h.slice(0,12), hash: h
            };
            done++;
            if(done >= batch.length){ updateCacheBadges(); if(start + batchSize < hashes.length){ setTimeout(function(){ checkBatch(start + batchSize); }, 100); } }
          }).catch(function(){ done++; if(done >= batch.length && start + batchSize < hashes.length){ setTimeout(function(){ checkBatch(start + batchSize); }, 100); } });
      });
    }
    checkBatch(0);
  }

  document.getElementById('cachePanelHeader').addEventListener('click', function(){
    var body = document.getElementById('cachePanelBody');
    body.classList.toggle('open');
    if(body.classList.contains('open')) pollCacheState();
  });

  document.getElementById('viewLogsBtn').addEventListener('click', function(){
    window.open('/api/logs', '_blank');
  });

  document.getElementById('cacheClearBtn').addEventListener('click', function(){
    if(!confirm('Clear all cache?')) return;
    fetch('/api/cache')
      .then(function(r){ return r.json(); })
      .then(function(data){
        var items = data.items || [];
        var cleared = 0;
        items.forEach(function(item){
          fetch('/api/cache/' + item.hash, { method: 'DELETE' })
            .then(function(){ cleared++; delete cacheState[item.hash]; if(cleared >= items.length){ updateCacheBadges(); updateCachePanel(); showToast('Cleared ' + items.length + ' items'); } })
            .catch(function(){});
        });
        if(items.length === 0) updateCachePanel();
      });
  });

  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', prefetchAll);
  } else { prefetchAll(); }

  // Back to Top
  var btnTop = document.getElementById('backToTop');
  window.addEventListener('scroll', function(){ btnTop.classList.toggle('visible', window.scrollY > 500); });
  btnTop.addEventListener('click', function(){ window.scrollTo({top:0, behavior:'smooth'}); });

  // ===== Video Modal =====
  var modalOverlay = document.getElementById('videoModal');
  var modalVideo = document.getElementById('modalVideo');
  var modalLoading = document.getElementById('modalLoading');
  var progressTimer = null;

  function closeModal(){
    modalOverlay.classList.remove('active');
    modalVideo.pause(); modalVideo.src = ''; modalVideo.removeAttribute('src');
    modalLoading.style.display = 'none';
    modalLoading.innerHTML = '<span>Loading video...</span>';
    if(progressTimer){ clearInterval(progressTimer); progressTimer = null; }
  }

  document.getElementById('modalClose').addEventListener('click', closeModal);
  modalOverlay.addEventListener('click', function(e){ if(e.target === this) closeModal(); });
  document.addEventListener('keydown', function(e){ if(e.key === 'Escape') closeModal(); });

  function showToast(msg){
    var t = document.getElementById('toast');
    if(!t){
      t = document.createElement('div'); t.id = 'toast';
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.style.opacity = '1'; t.style.transform = 'translateX(-50%) translateY(0)';
    if(t._timer) clearTimeout(t._timer);
    t._timer = setTimeout(function(){ t.style.opacity = '0'; t.style.transform = 'translateX(-50%) translateY(20px)'; }, 2000);
  }

  function extractHash(magnet){
    var m = magnet.match(/xt=urn:btih:([a-f0-9]{40})/i);
    return m ? m[1].toLowerCase() : '';
  }

  // Copy Magnet (delegated for dynamic content)
  document.addEventListener('click', function(e){
    var btn = e.target.closest('.btn-copy');
    if(!btn) return;
    e.preventDefault(); e.stopPropagation();
    var magnet = btn.getAttribute('data-magnet');
    if(!magnet) return;
    if(navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText(magnet).then(function(){ showToast('Magnet copied'); }).catch(function(){ showToast('Copy failed'); });
    } else {
      var ta = document.createElement('textarea'); ta.value = magnet; ta.style.cssText = 'position:fixed;opacity:0';
      document.body.appendChild(ta); ta.select();
      try{ document.execCommand('copy'); showToast('Magnet copied'); } catch(err){ showToast('Copy failed'); }
      document.body.removeChild(ta);
    }
  });

  // ===== Work Tab Switching =====
  document.addEventListener('click', function(e){
    var tab = e.target.closest('.work-tab');
    if(!tab) return;
    var row = tab.closest('.actor-row');
    if(!row) return;
    var worksStr = row.getAttribute('data-works');
    if(!worksStr) return;
    var works;
    try { works = JSON.parse(worksStr); } catch(err){ return; }
    var idx = parseInt(tab.getAttribute('data-index'), 10);
    if(isNaN(idx) || idx < 0 || idx >= works.length) return;
    var w = works[idx];
    if(!w) return;

    var mediaImg = row.querySelector('.featured-media img');
    if(mediaImg){ mediaImg.src = w.coverRel; mediaImg.alt = w.title || w.code; }

    var titleEl = row.querySelector('.featured-title');
    if(titleEl) titleEl.textContent = w.title || w.code;

    var dateEl = row.querySelector('.featured-date');
    if(dateEl){ dateEl.textContent = w.date || ''; dateEl.style.display = w.date ? '' : 'none'; }

    var idBadge = row.querySelector('.id-badge');
    if(idBadge) idBadge.textContent = '#' + w.globalId;

    var resBadge = row.querySelector('.res-badge');
    if(resBadge){ resBadge.textContent = w.resolution || ''; resBadge.style.display = w.resolution ? '' : 'none'; }

    var cacheBadge = row.querySelector('.cache-badge');
    if(cacheBadge){
      cacheBadge.setAttribute('data-hash', w.hashAttr);
      cacheBadge.setAttribute('data-id', w.globalId);
      cacheBadge.className = 'cache-badge pending ' + (w.isPrefetchTarget ? 'prefetch-target' : '');
      cacheBadge.title = 'Not cached';
    }

    var btnPlay = row.querySelector('.btn-play');
    if(btnPlay){ btnPlay.setAttribute('data-magnet', w.magnet || ''); btnPlay.style.display = w.magnet ? '' : 'none'; }

    var btnMagnet = row.querySelector('.btn-magnet');
    if(btnMagnet){ btnMagnet.href = w.magnet || ''; btnMagnet.style.display = w.magnet ? '' : 'none'; }

    var btnCopy = row.querySelector('.btn-copy');
    if(btnCopy){ btnCopy.setAttribute('data-magnet', w.magnet || ''); btnCopy.style.display = w.magnet ? '' : 'none'; }

    var descEl = row.querySelector('.featured-desc');
    if(descEl){ descEl.textContent = w.title || ''; descEl.style.display = w.title ? '' : 'none'; }

    row.querySelectorAll('.work-tab').forEach(function(t){ t.classList.remove('active'); });
    tab.classList.add('active');
  });

  // ===== Unified Play Logic =====
  function playByMagnet(magnet){
    var hash = extractHash(magnet);
    if(!hash){ showToast('Invalid magnet link'); return; }

    modalLoading.style.display = 'flex';
    modalOverlay.classList.add('active');
    if(progressTimer){ clearInterval(progressTimer); progressTimer = null; }

    fetch('/api/check/' + hash)
      .then(function(r){ return r.json(); })
      .then(function(data){
        if(data.head_ready){ startPlayback(hash); return; }
        modalLoading.innerHTML = '<span>Connecting torrent...</span>';
        fetch('/torrent/add', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ magnet: magnet })
        })
        .then(function(r){ return r.json(); })
        .then(function(data){
          if(data.error){ modalLoading.innerHTML = '<span>Launch failed: ' + data.error + '</span>'; return; }
          // Start playback immediately — browser will buffer until moov is downloaded
          startPlayback(hash);
          var startTime = Date.now();
          progressTimer = setInterval(function(){
            fetch('/torrent/status/' + hash)
              .then(function(r){ return r.json(); })
              .then(function(s){
                var elapsed = Math.round((Date.now() - startTime) / 1000);
                var speed = (s.download_rate / 1024 / 1024).toFixed(1);
                var pct = s.progress.toFixed(1);
                var buf = s.local_size ? (s.local_size / 1024 / 1024).toFixed(0) + 'MB' : '';
                // If video already playing, stop polling
                if(!modalVideo.paused && modalVideo.readyState >= 3){
                  clearInterval(progressTimer); progressTimer = null; return;
                }
                if(s.head_ready && modalVideo.readyState < 3){
                  modalLoading.innerHTML = '<span>Buffering first frame... | ' + speed + ' MB/s | ' + buf + ' (' + pct + '%)</span>';
                }
                if(elapsed > 180){ clearInterval(progressTimer); progressTimer = null; modalLoading.innerHTML = '<span>Download timeout, retry later</span>'; return; }
              }).catch(function(err){});
          }, 2000);
        }).catch(function(err){ modalLoading.innerHTML = '<span>Cannot connect to download server</span>'; });
      }).catch(function(err){ modalLoading.innerHTML = '<span>Cannot connect to cache server</span>'; });
  }

  // Play Button (delegated for dynamic content)
  document.addEventListener('click', function(e){
    var btn = e.target.closest('.btn-play');
    if(!btn) return;
    e.preventDefault(); e.stopPropagation();
    var magnet = btn.getAttribute('data-magnet');
    if(!magnet) return;
    playByMagnet(magnet);
  });

  // ===== Magnet Player Modal =====
  var magnetModal = document.getElementById('magnetModal');
  var magnetInput = document.getElementById('magnetInput');
  var magnetPlayBtn = document.getElementById('magnetPlayBtn');

  document.getElementById('magnetToggle').addEventListener('click', function(){
    magnetModal.classList.add('active');
    magnetInput.focus();
  });

  // ===== Refresh Toggle =====
  var refreshToggle = document.getElementById('refreshToggle');
  var refreshIcon = document.getElementById('refreshIcon');
  var isRefreshing = false;
  refreshToggle.addEventListener('click', function(){
    if(isRefreshing) return;
    if(!confirm('Refresh data and regenerate report? (1-3 min)')) return;
    isRefreshing = true;
    refreshToggle.classList.add('spinning');
    refreshIcon.textContent = '⏳';
    showToast('Refreshing data...');

    fetch('/api/regenerate', {method: 'POST'})
      .then(function(r){ return r.text(); })
      .then(function(text){
        // stream JSON: may have multiple lines, take last line as result
        var lines = text.trim().split('\\n');
        var last = lines[lines.length - 1];
        var data = JSON.parse(last);
        refreshToggle.classList.remove('spinning');
        refreshIcon.textContent = '🔄';
        isRefreshing = false;
        if(data.status === 'done'){
          showToast('Refresh done! Reloading in 2s');
          setTimeout(function(){ location.reload(); }, 2000);
        }else{
          showToast('Refresh failed: ' + (data.message || data.stderr || 'Unknown error'));
        }
      })
      .catch(function(err){
        refreshToggle.classList.remove('spinning');
        refreshIcon.textContent = '🔄';
        isRefreshing = false;
        showToast('Refresh failed: ' + err.message);
      });
  });
  document.getElementById('magnetModalClose').addEventListener('click', function(){
    magnetModal.classList.remove('active');
  });
  magnetModal.addEventListener('click', function(e){ if(e.target === this) magnetModal.classList.remove('active'); });

  magnetPlayBtn.addEventListener('click', function(){
    var magnet = magnetInput.value.trim();
    if(!magnet){ showToast('Enter magnet link'); return; }
    magnetModal.classList.remove('active');
    playByMagnet(magnet);
  });

  magnetInput.addEventListener('keydown', function(e){
    if(e.key === 'Enter' && !e.shiftKey){
      e.preventDefault();
      magnetPlayBtn.click();
    }
  });

  function startPlayback(hash){
    modalLoading.innerHTML = '<span>Loading video...</span>';
    modalVideo.currentHash = hash;
    var canplayFired = false;
    function onCanplay(){
      canplayFired = true;
      modalLoading.style.display = 'none';
      modalVideo.play().catch(function(err){ console.error('Play error:', err); });
    }
    modalVideo.addEventListener('canplay', onCanplay, { once: true });
    modalVideo.addEventListener('loadedmetadata', function(){
      if(!canplayFired) modalLoading.innerHTML = '<span>Buffering first frame...</span>';
    }, { once: true });

    var statusTimer = null;
    function startStatusPoll(){
      if(statusTimer) return;
      statusTimer = setInterval(function(){
        fetch('/torrent/status/' + hash)
          .then(function(r){ return r.json(); })
          .then(function(s){
            if(!s.ready) return;
            var speed = (s.download_rate / 1024 / 1024).toFixed(1);
            var pct = s.progress.toFixed(0);
            var buf = s.local_size ? (s.local_size / 1024 / 1024).toFixed(0) + 'MB' : '';
            if(modalVideo.paused || modalVideo.readyState < 3){
              modalLoading.innerHTML = '<span>Buffering | ' + speed + ' MB/s | cached ' + buf + ' (' + pct + '%)</span>';
            }
          }).catch(function(err){});
      }, 2000);
    }
    function stopStatusPoll(){ if(statusTimer){ clearInterval(statusTimer); statusTimer = null; } }

    modalVideo.addEventListener('waiting', function(){ modalLoading.style.display = 'flex'; startStatusPoll(); });
    modalVideo.addEventListener('playing', function(){ modalLoading.style.display = 'none'; stopStatusPoll(); });
    modalVideo.addEventListener('seeking', function(){ modalLoading.style.display = 'flex'; modalLoading.innerHTML = '<span>Seeking...</span>'; startStatusPoll(); });
    modalVideo.addEventListener('seeked', function(){ stopStatusPoll(); if(!modalVideo.paused) modalLoading.style.display = 'none'; });
    modalVideo.addEventListener('error', function(){ stopStatusPoll(); modalLoading.innerHTML = '<span>Playback failed, file may be incomplete</span>'; }, { once: true });

    modalVideo.src = '/stream/' + hash;
    modalVideo.load();
    setTimeout(function(){ if(modalLoading.style.display !== 'none'){ stopStatusPoll(); modalLoading.innerHTML = '<span>Still buffering, please wait...</span>'; } }, 60000);
  }

  // Keyboard Shortcuts
  document.addEventListener('keydown', function(e){
    if(!modalOverlay.classList.contains('active')) return;
    if(e.key === 'ArrowLeft'){ e.preventDefault(); modalVideo.currentTime = Math.max(0, modalVideo.currentTime - 10); }
    else if(e.key === 'ArrowRight'){ e.preventDefault(); modalVideo.currentTime = Math.min(modalVideo.duration || Infinity, modalVideo.currentTime + 10); }
    else if(e.key === 'Escape'){ closeModal(); }
  });

})();
</script>

</body>
</html>`;

fs.mkdirSync(path.dirname(OUT_PATH), { recursive: true });
fs.writeFileSync(OUT_PATH, html, 'utf8');
const size = (Buffer.byteLength(html) / 1024).toFixed(1);
console.log('[report] ✅ ' + OUT_PATH + ' (' + size + 'KB, ' + solo.length + ' actresses, ' + totalWorks + ' works)');
}

generate().catch(function(err){
  console.error('[report] failed:', err);
  process.exit(1);
});
