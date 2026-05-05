#!/usr/bin/env node
// generate-report.js — 高性能外部图片版本

const fs = require('fs');
const path = require('path');

const TOOLBOX = path.dirname(process.argv[1]);
const CONFIG_PATH = process.argv[2] || path.join(TOOLBOX, 'config.json');
const OUT_PATH = process.argv[3] || path.join(TOOLBOX, '..', '..', 'actresses-report.html');
const B64_DIR = '/tmp/actress-b64';
const NEWS_DIR = '/tmp/actress-news';
const IMAGES_DIR = path.join(TOOLBOX, 'images');

const config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
const solo = config.actresses.filter(a => !a.type || a.type === 'solo');
console.log('[filter] Solo: ' + solo.length + ' actresses');

// 确保图片目录存在
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

function readCover(code) {
  try { return fs.readFileSync(path.join(B64_DIR, 'cover_' + code + '.txt'), 'utf8').trim(); }
  catch (e) { return ''; }
}

function readWorks(code) {
  try { return JSON.parse(fs.readFileSync(path.join(NEWS_DIR, code + '.json'), 'utf8')).works || []; }
  catch (e) { return []; }
}

function readJable(code) {
  try { return JSON.parse(fs.readFileSync('/tmp/actress-jable/' + code + '.json', 'utf8')); }
  catch (e) { return null; }
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

let totalWorks = 0;
let navHtml = '';
let cardsHtml = '';

// 收集所有女优数据
const actressData = solo.map(function(a) {
  const id = a.code.toLowerCase();
  const heroB64 = readCover(a.code);
  const ijavWorks = readWorks(a.code);
  const jableData = readJable(a.code);
  const jableWorks = jableData ? (jableData.works || []) : [];

  // 构建 jable 映射（m3u8 + 封面）
  const jableCoverMap = {};
  const m3u8Map = {};
  jableWorks.forEach(function(w) {
    const codeUpper = w.code.toUpperCase();
    jableCoverMap[codeUpper] = w.cover_local || '';
    // 优先使用本地缓存的 m3u8
    if (w.m3u8_local && fs.existsSync(w.m3u8_local)) {
      m3u8Map[codeUpper] = rel(w.m3u8_local);
    } else {
      m3u8Map[codeUpper] = w.m3u8_url || '';
    }
  });

  // 以 ijavtorrent 为主，补充 jable 的 m3u8 和封面
  let works = ijavWorks.map(function(w) {
    const codeUpper = w.code.toUpperCase();
    return {
      code: codeUpper,
      title: w.title,
      date: w.date || '',
      views: w.views || '',
      likes: w.likes || '',
      cover_b64: w.cover_b64 || '',
      cover_local: jableCoverMap[codeUpper] || '',
      magnet: (w.magnet || '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>'),
      m3u8_url: m3u8Map[codeUpper] || '',
      resolution: w.resolution || '',
    };
  });

  // 按日期倒序（最新的在前）
  works.sort(function(a, b) {
    const da = a.date ? (a.date.split('/')[2] + a.date.split('/')[1] + a.date.split('/')[0]) : '00000000';
    const db = b.date ? (b.date.split('/')[2] + b.date.split('/')[1] + b.date.split('/')[0]) : '00000000';
    return db.localeCompare(da);
  });

  // 限制最近 3 部
  works = works.slice(0, 3);
  totalWorks += works.length;

  return {
    a: a,
    id: id,
    heroB64: heroB64,
    works: works,
  };
});

// 按每个女优最新作品的日期升序排序整个 stream
actressData.sort(function(ad, bd) {
  const da = ad.works.length > 0 && ad.works[0].date
    ? (ad.works[0].date.split('/')[2] + ad.works[0].date.split('/')[1] + ad.works[0].date.split('/')[0])
    : '99999999';
  const db = bd.works.length > 0 && bd.works[0].date
    ? (bd.works[0].date.split('/')[2] + bd.works[0].date.split('/')[1] + bd.works[0].date.split('/')[0])
    : '99999999';
  return da.localeCompare(db);
});

// 生成 HTML
actressData.forEach(function(data) {
  const a = data.a;
  const id = data.id;
  const heroB64 = data.heroB64;
  const works = data.works;
  const initial = a.name.charAt(0);

  // 保存女优封面：优先用 jable 第一个作品封面
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

  // 回退到 ijavtorrent base64
  if (!heroSaved.success) {
    heroSaved = saveBase64(id, heroB64, heroesDir, id);
  }

  // 质量检查：太小的封面删掉
  if (heroSaved.success) {
    try {
      if (fs.statSync(heroSaved.path).size < 10000) {
        fs.unlinkSync(heroSaved.path);
        heroSaved.success = false;
      }
    } catch (e) {}
  }

  // 质量失败后再次回退到 jable 封面
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

  // 导航项
  const navImg = hasHero
    ? `<img src="${heroRel}" alt="${esc(a.name)}" loading="lazy" decoding="async">`
    : `<span class="nav-initial">${initial}</span>`;
  navHtml += `    <a class="nav-item" href="#${id}" data-target="${id}" aria-label="${esc(a.name)}">\n`
           + `      <div class="nav-avatar">${navImg}</div>\n`
           + `      <span class="nav-label">${esc(a.name)}</span>\n`
           + `    </a>\n`;

  // 作品
  let worksHtml = '';
  const actressWorksDir = path.join(worksDir, id);
  fs.mkdirSync(actressWorksDir, { recursive: true });

  works.forEach(function(w) {
    let coverRel = heroRel;
    let hasWorkCover = false;

    // 优先使用 jable 本地封面
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

    // 回退到 ijavtorrent base64
    if (!hasWorkCover && w.cover_b64) {
      const workSaved = saveBase64(w.code, w.cover_b64, actressWorksDir, w.code.toLowerCase());
      hasWorkCover = workSaved.success;
      if (hasWorkCover) coverRel = rel(workSaved.path);
    }

    // 质量检查：太小的封面用 hero 替代
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

    let meta = '';
    if (w.date) meta += `<span class="meta-item">${esc(w.date)}</span>`;
    if (w.views) meta += `<span class="meta-item">${esc(w.views)} 次浏览</span>`;
    const res = w.resolution || '';
    let btn = '';
    if (w.magnet) {
      btn += `<a class="btn-magnet" href="${esc(w.magnet)}" target="_blank" rel="noopener noreferrer"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M6 9l6 6 6-6"/></svg>磁力${res ? ' ' + esc(res) : ''}</a>`;
    }
    if (w.magnet) {
      btn += `<a class="btn-magnet btn-play" href="#" data-magnet="${esc(w.magnet)}" onclick="return false;" style="margin-left:6px"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M5 3l14 9-14 9V3z"/></svg>播放</a>`;
      btn += `<a class="btn-magnet btn-copy" href="#" data-magnet="${esc(w.magnet)}" onclick="return false;" title="复制磁力链接" style="margin-left:6px;padding:8px 10px"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg></a>`;
    }

    const hashAttr = w.magnet ? extractHashAttr(w.magnet) : '';
    const cacheBadge = hashAttr ? `<span class="cache-badge pending" data-hash="${hashAttr}" title="未缓存"></span>` : '';
    worksHtml += `      <div class="work-row" data-cover="${coverRel}" data-default="${heroRel}">\n`
               + `        <div class="work-thumb"><img src="${coverRel}" alt="${esc(w.title)}" loading="lazy" decoding="async"></div>\n`
               + `        <div class="work-info">\n`
               + `          <div class="work-code">${cacheBadge}${esc(w.code)}${res ? `<span class="badge">${esc(res)}</span>` : ''}</div>\n`
               + `          <div class="work-title">${esc(w.title)}</div>\n`
               + `          <div class="work-meta">${meta}</div>\n`
               + `        </div>\n`
               + `        <div class="work-action">${btn}</div>\n`
               + `      </div>\n`;
  });

  // Hero
  const heroImg = hasHero
    ? `<img class="card-hero-img" id="hero-${id}" src="${heroRel}" alt="${esc(a.name)} 封面" fetchpriority="high" decoding="async">`
    : `<div class="card-hero-fallback"><span>${initial}</span></div>`;

  cardsHtml += `  <article class="card" id="${id}" data-name="${esc(a.name)} ${esc(a.jp)} ${a.code}">\n`
             + `    <header class="card-header">${heroImg}</header>\n`
             + `    <section class="card-body">\n`
             + `      <div class="card-title-row">\n`
             + `        <div class="card-title">\n`
             + `          <h2>${esc(a.name)}</h2>\n`
             + `          <span class="card-subtitle">${esc(a.jp)}</span>\n`
             + `        </div>\n`
             + `        <span class="code-badge">${a.code}</span>\n`
             + `      </div>\n`;

  if (works.length) {
    cardsHtml += `      <div class="works-wrap">\n`
               + `        <div class="works-header"><span>最新作品</span><span class="works-hint">点击切换封面</span></div>\n`
               + `        <div class="works-list">\n${worksHtml}        </div>\n`
               + `      </div>\n`;
  }

  cardsHtml += `    </section>\n`
             + `  </article>\n`;
});

// 构建完整 HTML
const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="${esc(config.title)} — ${solo.length} 位女优，${totalWorks} 部作品">
<meta property="og:title" content="${esc(config.title)}">
<meta property="og:description" content="${solo.length} 位女优，${totalWorks} 部作品">
<title>${esc(config.title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=Noto+Sans+SC:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#F5F5F7;--surface:#FFFFFF;--surface-2:#F5F5F7;
  --text:#1D1D1F;--text-2:#515154;--text-3:#86868B;
  --border:#E8E8ED;--border-2:#D2D2D7;--accent:#007AFF;--accent-2:#FF2D55;
  --shadow:0 1px 3px rgba(0,0,0,0.04);--shadow-hover:0 8px 24px rgba(0,0,0,0.08);
  --radius:20px;--radius-sm:12px;--nav-height:64px;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#000000;--surface:#1C1C1E;--surface-2:#2C2C2E;
    --text:#F5F5F7;--text-2:#A1A1A6;--text-3:#8E8E93;
    --border:#38383A;--border-2:#48484A;--accent:#0A84FF;--accent-2:#FF375F;
    --shadow:0 1px 3px rgba(0,0,0,0.3);--shadow-hover:0 8px 24px rgba(0,0,0,0.5);
  }
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{font-family:Inter,"Noto Sans SC","SF Pro Display",-apple-system,BlinkMacSystemFont,sans-serif;background:var(--bg);color:var(--text);line-height:1.5;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
img{max-width:100%;height:auto;display:block}
a{color:inherit;text-decoration:none}

/* Header */
.site-header{text-align:center;padding:80px 24px 48px;background:var(--surface);border-bottom:1px solid var(--border)}
.site-header h1{font-size:clamp(1.8rem,4vw,2.6rem);font-weight:800;letter-spacing:-0.03em;margin-bottom:8px}
.site-header p{color:var(--text-3);font-size:0.9rem}
.stats{display:flex;justify-content:center;gap:48px;margin-top:24px}
.stat{text-align:center}
.stat-num{font-size:1.75rem;font-weight:700}
.stat-label{font-size:0.7rem;color:var(--text-3);text-transform:uppercase;letter-spacing:0.08em;margin-top:4px;font-weight:600}

/* Search */
.search-wrap{max-width:560px;margin:0 auto;padding:24px 16px 0}
.search-input{width:100%;padding:12px 20px;border:1px solid var(--border);border-radius:100px;background:var(--surface);color:var(--text);font-size:0.95rem;outline:none;transition:all .2s}
.search-input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(0,122,255,0.15)}
.search-input::placeholder{color:var(--text-3)}

/* Nav */
.site-nav{position:sticky;top:0;z-index:100;background:rgba(245,245,247,0.85);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);border-bottom:1px solid var(--border);padding:10px 16px;overflow-x:auto;display:flex;gap:6px;scrollbar-width:none;justify-content:center}
.site-nav::-webkit-scrollbar{display:none}
@media (prefers-color-scheme: dark){.site-nav{background:rgba(28,28,30,0.85)}}
.nav-item{flex-shrink:0;display:flex;flex-direction:column;align-items:center;gap:4px;padding:6px 10px;border-radius:12px;transition:background .2s;position:relative}
.nav-item:hover{background:rgba(0,0,0,0.05)}
.nav-item.active::after{content:'';position:absolute;bottom:-10px;width:4px;height:4px;border-radius:50%;background:var(--accent)}
@media (prefers-color-scheme: dark){.nav-item:hover{background:rgba(255,255,255,0.08)}}
.nav-avatar{width:40px;height:40px;border-radius:50%;overflow:hidden;border:2px solid var(--border);background:var(--surface-2);transition:border-color .2s,transform .2s}
.nav-item:hover .nav-avatar{border-color:var(--accent);transform:scale(1.05)}
.nav-avatar img{width:100%;height:100%;object-fit:cover}
.nav-initial{width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:0.9rem;color:var(--text-2)}
.nav-label{font-size:0.6rem;color:var(--text-3);font-weight:500;white-space:nowrap;max-width:60px;overflow:hidden;text-overflow:ellipsis}

/* Main */
.container{max-width:980px;margin:0 auto;padding:32px 16px 80px}

/* Card */
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;margin-bottom:40px;box-shadow:var(--shadow);transition:transform .25s,box-shadow .25s,border-color .25s;scroll-margin-top:80px}
.card:hover{box-shadow:var(--shadow-hover);border-color:var(--border-2);transform:translateY(-2px)}
.card-header{background:var(--surface-2);position:relative;overflow:hidden;line-height:0}
.card-hero-img{width:100%;height:auto;display:block;transition:opacity .3s}
.card-hero-fallback{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#18181B,#27272A);color:var(--border-2);font-size:4rem;font-weight:900}
.card-body{padding:28px}
.card-title-row{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:20px;flex-wrap:wrap}
.card-title{min-width:0}
.card-title h2{font-size:clamp(1.2rem,3vw,1.5rem);font-weight:700;line-height:1.2;display:inline}
.card-subtitle{font-size:0.85rem;color:var(--text-3);font-weight:500;margin-left:8px}
.code-badge{font-size:0.72rem;font-weight:600;color:var(--text-3);background:var(--surface-2);padding:4px 14px;border-radius:100px;border:1px solid var(--border);white-space:nowrap}

/* Works */
.works-wrap{margin-top:4px}
.works-header{display:flex;align-items:center;justify-content:space-between;padding-bottom:10px;border-bottom:1px solid var(--border);margin-bottom:4px}
.works-header span:first-child{font-size:0.75rem;font-weight:600;color:var(--text-3);text-transform:uppercase;letter-spacing:0.06em}
.works-hint{font-size:0.7rem;color:var(--text-3);font-weight:400;text-transform:none;letter-spacing:0}
.work-row{display:flex;align-items:center;gap:16px;padding:12px 10px;border-bottom:1px solid var(--surface-2);cursor:pointer;border-radius:10px;transition:background .2s,transform .15s}
.work-row:last-child{border-bottom:none}
.work-row:hover{background:var(--surface-2);transform:translateX(4px)}
.work-row.active{background:rgba(0,122,255,0.08);box-shadow:inset 3px 0 0 var(--accent)}
.work-thumb{flex-shrink:0;width:80px;border-radius:8px;overflow:hidden;transition:transform .2s;line-height:0}
.work-row:hover .work-thumb{transform:scale(1.05)}
.work-thumb img{width:100%;height:auto;display:block;border-radius:8px}
.work-info{flex:1;min-width:0}
.work-code{font-size:0.8rem;font-weight:700;color:var(--accent-2);margin-bottom:4px;display:flex;align-items:center;gap:6px}
.badge{font-size:0.6rem;font-weight:600;color:var(--accent);background:rgba(0,122,255,0.08);padding:2px 8px;border-radius:6px;border:1px solid rgba(0,122,255,0.12)}
.work-title{font-size:0.85rem;color:var(--text-2);line-height:1.5;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.work-meta{display:flex;gap:14px;margin-top:6px}
.meta-item{font-size:0.7rem;color:var(--text-3);font-weight:500}
.work-action{flex-shrink:0}
.btn-magnet{display:inline-flex;align-items:center;gap:4px;font-size:0.7rem;font-weight:600;color:var(--accent);background:rgba(0,122,255,0.06);border:1px solid rgba(0,122,255,0.12);padding:8px 14px;border-radius:10px;transition:all .2s;white-space:nowrap}
.btn-magnet:hover{background:var(--accent);color:#fff;border-color:var(--accent);box-shadow:0 4px 12px rgba(0,122,255,0.25)}
.btn-magnet svg{flex-shrink:0}

/* Footer */
.site-footer{text-align:center;padding:40px 24px;color:var(--text-3);font-size:0.8rem;background:var(--surface);border-top:1px solid var(--border)}

/* Back to top */
.back-to-top{position:fixed;bottom:24px;right:24px;width:44px;height:44px;border-radius:50%;background:var(--surface);color:var(--text);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;cursor:pointer;opacity:0;visibility:hidden;transition:all .3s;box-shadow:var(--shadow-hover);z-index:90}
.back-to-top.visible{opacity:1;visibility:visible}
.back-to-top:hover{background:var(--accent);color:#fff;border-color:var(--accent);transform:translateY(-2px)}
.back-to-top svg{width:20px;height:20px}

/* Video Modal */
.video-modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,0.88);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);z-index:200;display:none;align-items:center;justify-content:center;padding:20px;opacity:0;transition:opacity .3s}
.video-modal-overlay.active{display:flex;opacity:1}
.video-modal-box{width:100%;max-width:1000px;background:#000;border-radius:16px;overflow:hidden;position:relative;box-shadow:0 24px 80px rgba(0,0,0,0.6);transform:scale(0.92);transition:transform .3s}
.video-modal-overlay.active .video-modal-box{transform:scale(1)}
.video-modal-close{position:absolute;top:12px;right:12px;width:40px;height:40px;border-radius:50%;background:rgba(255,255,255,0.12);color:#fff;border:none;cursor:pointer;z-index:10;display:flex;align-items:center;justify-content:center;font-size:1.4rem;line-height:1;transition:background .2s}
.video-modal-close:hover{background:rgba(255,255,255,0.25)}
.video-modal-box video{width:100%;height:auto;display:block;max-height:82vh}
.video-modal-loading{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#fff;font-size:0.85rem;pointer-events:none}
.video-modal-loading::after{content:'';width:32px;height:32px;border:3px solid rgba(255,255,255,0.15);border-top-color:#fff;border-radius:50%;animation:spin 1s linear infinite;margin-left:10px}
@keyframes spin{to{transform:rotate(360deg)}}
@media (max-width:720px){.video-modal-box video{max-height:56vh}}

/* Responsive */
@media (max-width:720px){
  .site-header{padding:56px 20px 32px}
  .stats{gap:32px}
  .container{padding:24px 12px 60px}
  .card-body{padding:20px 16px}
  .work-thumb{width:60px}
  .work-thumb img{width:60px}
  .work-row{gap:12px;padding:10px 6px}
  .site-nav{justify-content:flex-start}
}
@media (max-width:420px){
  .work-thumb{width:48px}
  .work-thumb img{width:48px}
  .btn-magnet{padding:6px 10px;font-size:0.65rem}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{transition-duration:0.01ms!important;animation-duration:0.01ms!important}
}

/* Cache Badge */
.cache-badge{width:8px;height:8px;border-radius:50%;display:inline-block;flex-shrink:0}
.cache-badge.cached{background:#34C759;box-shadow:0 0 0 2px rgba(52,199,89,0.2)}
.cache-badge.downloading{background:#FF9500;animation:pulse 1.5s ease-in-out infinite}
.cache-badge.pending{background:var(--text-3);opacity:0.4}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}

/* Cache Panel */
.cache-panel{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:20px;margin:20px auto;max-width:980px}
.cache-panel-header{display:flex;align-items:center;justify-content:space-between;gap:12px;cursor:pointer;padding:4px 0}
.cache-panel-header h3{font-size:0.9rem;font-weight:700}
.cache-panel-header .cache-summary{font-size:0.75rem;color:var(--text-3)}
.cache-panel-body{display:none;margin-top:16px}
.cache-panel-body.open{display:block}
.cache-panel-toggle{width:28px;height:28px;border-radius:50%;background:var(--surface-2);display:flex;align-items:center;justify-content:center;transition:transform .2s}
.cache-panel-header:hover .cache-panel-toggle{transform:rotate(90deg)}
.cache-list{max-height:300px;overflow-y:auto}
.cache-item{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 0;border-bottom:1px solid var(--border)}
.cache-item:last-child{border-bottom:none}
.cache-item-name{font-size:0.8rem;font-weight:600;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cache-item-meta{font-size:0.7rem;color:var(--text-3);flex-shrink:0}
.cache-item-bar{flex-shrink:0;width:80px;height:4px;background:var(--surface-2);border-radius:2px;overflow:hidden}
.cache-item-bar-inner{height:100%;background:var(--accent);border-radius:2px;transition:width .3s}
.cache-clear-btn{font-size:0.75rem;font-weight:600;color:var(--accent-2);background:rgba(255,45,85,0.06);border:1px solid rgba(255,45,85,0.12);padding:8px 16px;border-radius:8px;cursor:pointer;margin-top:12px;transition:all .2s}
.cache-clear-btn:hover{background:var(--accent-2);color:#fff}
</style>

</head>
<body>

<header class="site-header">
  <h1>${esc(config.title)}</h1>
  <p>数据来源 ijavtorrent.com</p>
  <div class="stats">
    <div class="stat"><div class="stat-num">${solo.length}</div><div class="stat-label">女优</div></div>
    <div class="stat"><div class="stat-num">${totalWorks}</div><div class="stat-label">作品</div></div>
  </div>
</header>

<div class="search-wrap">
  <input type="search" class="search-input" id="search" placeholder="搜索女优、日文名或作品番号..." autocomplete="off">
</div>

<nav class="site-nav" id="siteNav">
${navHtml}</nav>

<main class="container" id="main">
${cardsHtml}</main>

<footer class="site-footer">
  <p>${esc(config.title)} · 数据来自 ijavtorrent.com</p>
</footer>

<!-- Cache Management Panel -->
<div class="cache-panel" id="cachePanel">
  <div class="cache-panel-header" id="cachePanelHeader">
    <div style="display:flex;align-items:center;gap:10px">
      <h3>📦 缓存管理</h3>
      <span class="cache-summary" id="cacheSummary">加载中...</span>
    </div>
    <div class="cache-panel-toggle"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 18l6-6-6-6"/></svg></div>
  </div>
  <div class="cache-panel-body" id="cachePanelBody">
    <div class="cache-list" id="cacheList"></div>
    <button class="cache-clear-btn" id="cacheClearBtn" style="display:none">清理全部缓存</button>
  </div>
</div>

<button class="back-to-top" id="backToTop" aria-label="回到顶部">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 15l-6-6-6 6"/></svg>
</button>

<div class="video-modal-overlay" id="videoModal">
  <div class="video-modal-box">
    <button class="video-modal-close" id="modalClose" aria-label="关闭">&times;</button>
    <video id="modalVideo" controls playsinline></video>
    <div class="video-modal-loading" id="modalLoading" style="display:none"><span>正在加载...</span></div>
  </div>
</div>

<script>
(function(){
  'use strict';

  // 封面切换（带动画）
  document.querySelectorAll('.work-row').forEach(function(row){
    row.addEventListener('click', function(e){
      if(e.target.closest('.btn-magnet')) return;
      var card = this.closest('.card');
      var img = card.querySelector('.card-hero-img');
      if(!img) return;
      var cover = this.getAttribute('data-cover');
      var def = this.getAttribute('data-default');
      var target = cover || def;
      if(!target || target === img.src) return;

      img.style.opacity = '0.6';
      setTimeout(function(){
        img.src = target;
        img.onload = function(){ img.style.opacity = '1'; };
        img.onerror = function(){ img.style.opacity = '1'; };
      }, 150);

      card.querySelectorAll('.work-row').forEach(function(s){ s.classList.remove('active'); });
      this.classList.add('active');
    });
  });

  // 导航高亮（Intersection Observer）
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
  }, { rootMargin: '-45% 0px -45% 0px', threshold: 0 });

  document.querySelectorAll('.card').forEach(function(card){ observer.observe(card); });

  // 搜索过滤
  var searchInput = document.getElementById('search');
  searchInput.addEventListener('input', function(){
    var q = this.value.trim().toLowerCase();
    document.querySelectorAll('.card').forEach(function(card){
      var name = (card.getAttribute('data-name') || '').toLowerCase();
      var visible = !q || name.includes(q);
      card.style.display = visible ? '' : 'none';
    });
    var visibleCards = document.querySelectorAll('.card:not([style*="display: none"])');
    var visibleIds = new Set(Array.from(visibleCards).map(function(c){ return c.id; }));
    navItems.forEach(function(n){
      n.style.display = visibleIds.has(n.getAttribute('data-target')) ? '' : 'none';
    });
  });

  // ── 缓存状态管理 ──────────────────────────────────────
  function escHtml(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
  var cacheState = {};  // hash -> { ready, cached, peers, progress, name }

  function updateCacheBadges(){
    document.querySelectorAll('.cache-badge').forEach(function(badge){
      var hash = badge.getAttribute('data-hash');
      if(!hash) return;
      var st = cacheState[hash];
      if(!st) return;
      badge.classList.remove('cached','downloading','pending');
      if(st.cached){
        badge.classList.add('cached');
        badge.title = '已缓存 | Peers: ' + st.peers;
      } else if(st.ready){
        badge.classList.add('downloading');
        badge.title = '下载中 ' + st.progress.toFixed(1) + '% | Peers: ' + st.peers;
      } else {
        badge.classList.add('pending');
        badge.title = '未缓存';
      }
    });
  }

  function updateCachePanel(){
    var list = document.getElementById('cacheList');
    var summary = document.getElementById('cacheSummary');
    var clearBtn = document.getElementById('cacheClearBtn');
    if(!list) return;

    var items = Object.values(cacheState).filter(function(s){ return s.ready; });
    var cachedCount = items.filter(function(s){ return s.cached; }).length;
    var totalSize = items.reduce(function(sum, s){ return sum + (s.videoSize || 0); }, 0);
    var cachedSize = items.filter(function(s){ return s.cached; }).reduce(function(sum, s){ return sum + (s.videoSize || 0); }, 0);

    summary.textContent = cachedCount + ' / ' + items.length + ' 已缓存';
    clearBtn.style.display = items.length > 0 ? '' : 'none';

    if(items.length === 0){
      list.innerHTML = '<div style="text-align:center;color:var(--text-3);font-size:0.8rem;padding:20px">暂无缓存数据</div>';
      return;
    }

    list.innerHTML = items.map(function(s){
      var pct = s.cached ? 100 : s.progress;
      var sizeStr = s.videoSize ? (s.videoSize / 1024 / 1024 / 1024).toFixed(1) + 'GB' : '';
      return '<div class="cache-item">'
        + '<span class="cache-item-name">' + escHtml(s.name || s.hash.slice(0,8)) + '</span>'
        + '<div class="cache-item-bar"><div class="cache-item-bar-inner" style="width:' + pct + '%"></div></div>'
        + '<span class="cache-item-meta">' + (s.cached ? '已缓存' : pct.toFixed(1) + '%') + ' ' + sizeStr + '</span>'
        + '</div>';
    }).join('');
  }

  // 轮询缓存状态
  function pollCacheState(){
    fetch('/api/cache')
      .then(function(r){ return r.json(); })
      .then(function(data){
        if(data.items){
          data.items.forEach(function(item){
            cacheState[item.hash] = item;
          });
          updateCacheBadges();
          updateCachePanel();
        }
      })
      .catch(function(err){ console.error('Cache poll error:', err); });
  }

  // 预缓存：页面加载后只预添加最新的 13 个 magnet（标记为 prefetch）
  function prefetchAll(){
    var magnets = [];
    var seen = new Set();
    document.querySelectorAll('[data-magnet]').forEach(function(el){
      var m = el.getAttribute('data-magnet');
      var h = extractHash(m);
      if(h && !seen.has(h)){
        seen.add(h);
        magnets.push(m);
      }
    });

    // 只取最新的 13 个（按 HTML 中的顺序，最新的在前）
    var prefetchMagnets = magnets.slice(0, 13);
    console.log('[prefetch] ' + prefetchMagnets.length + ' / ' + magnets.length + ' magnets to prefetch');

    // 批量添加，间隔 500ms 避免并发过高
    var idx = 0;
    function next(){
      if(idx >= prefetchMagnets.length) return;
      var m = prefetchMagnets[idx++];
      fetch('/torrent/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ magnet: m, prefetch: true })
      }).catch(function(err){});
      setTimeout(next, 500);
    }
    next();

    // 开始轮询状态
    setTimeout(function(){ pollCacheState(); }, 5000);
    setInterval(pollCacheState, 10000);
  }

  // 缓存面板折叠
  document.getElementById('cachePanelHeader').addEventListener('click', function(){
    var body = document.getElementById('cachePanelBody');
    body.classList.toggle('open');
    if(body.classList.contains('open')) pollCacheState();
  });

  // 清理缓存按钮
  document.getElementById('cacheClearBtn').addEventListener('click', function(){
    if(!confirm('确定要清理全部缓存吗？')) return;
    // 通过删除 cache/torrent/ 目录下的所有内容来清理
    fetch('/api/cache')
      .then(function(r){ return r.json(); })
      .then(function(data){
        var cleared = 0;
        (data.items || []).forEach(function(item){
          cacheState[item.hash] = { ready: false, cached: false, peers: 0, progress: 0, name: item.name, hash: item.hash };
          cleared++;
        });
        updateCacheBadges();
        updateCachePanel();
        showToast('已清理 ' + cleared + ' 个缓存');
      });
  });

  // 页面加载完成后开始预缓存
  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', prefetchAll);
  } else {
    prefetchAll();
  }

  // 回到顶部
  var btn = document.getElementById('backToTop');
  window.addEventListener('scroll', function(){
    btn.classList.toggle('visible', window.scrollY > 500);
  });
  btn.addEventListener('click', function(){
    window.scrollTo({top:0, behavior:'smooth'});
  });

  // 弹窗视频播放（Torrent 流）
  var modalOverlay = document.getElementById('videoModal');
  var modalVideo = document.getElementById('modalVideo');
  var modalLoading = document.getElementById('modalLoading');
  var progressInterval = null;
  var TORRENT_SERVER = '';  // 通过 /torrent/* 由 cache-server 反向代理

  function closeModal(){
    modalOverlay.classList.remove('active');
    modalVideo.pause();
    modalVideo.src = '';
    modalVideo.removeAttribute('src');
    if(progressInterval){ clearInterval(progressInterval); progressInterval = null; }
    modalLoading.style.display = 'none';
    modalLoading.innerHTML = '<span>正在加载...</span>';
  }

  document.getElementById('modalClose').addEventListener('click', closeModal);
  modalOverlay.addEventListener('click', function(e){ if(e.target === this) closeModal(); });
  document.addEventListener('keydown', function(e){ if(e.key === 'Escape') closeModal(); });

  // Toast 提示
  function showToast(msg){
    var t = document.getElementById('toast');
    if(!t){
      t = document.createElement('div');
      t.id = 'toast';
      t.style.cssText = 'position:fixed;bottom:80px;left:50%;transform:translateX(-50%) translateY(20px);background:rgba(0,0,0,0.8);color:#fff;padding:10px 20px;border-radius:20px;font-size:0.85rem;z-index:9999;opacity:0;transition:all 0.3s ease;pointer-events:none;white-space:nowrap;';
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.style.opacity = '1';
    t.style.transform = 'translateX(-50%) translateY(0)';
    if(t._timer) clearTimeout(t._timer);
    t._timer = setTimeout(function(){
      t.style.opacity = '0';
      t.style.transform = 'translateX(-50%) translateY(20px)';
    }, 2000);
  }

  function extractHash(magnet){
    var m = magnet.match(/xt=urn:btih:([a-f0-9]{40})/i);
    return m ? m[1].toLowerCase() : '';
  }

  // 复制 magnet 到剪切板
  document.querySelectorAll('.btn-copy').forEach(function(btn){
    btn.addEventListener('click', function(e){
      e.preventDefault();
      e.stopPropagation();
      var magnet = this.getAttribute('data-magnet');
      if(!magnet) return;
      if(navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(magnet).then(function(){
          showToast('已复制磁力链接');
        }).catch(function(){
          showToast('复制失败，请手动复制');
        });
      } else {
        // fallback
        var ta = document.createElement('textarea');
        ta.value = magnet;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try{ document.execCommand('copy'); showToast('已复制磁力链接'); }
        catch(err){ showToast('复制失败，请手动复制'); }
        document.body.removeChild(ta);
      }
    });
  });

  document.querySelectorAll('.btn-play').forEach(function(btn){
    btn.addEventListener('click', function(e){
      e.preventDefault();
      e.stopPropagation();
      var magnet = this.getAttribute('data-magnet');
      if(!magnet) return;

      var hash = extractHash(magnet);
      if(!hash) return;

      modalLoading.style.display = 'flex';
      modalOverlay.classList.add('active');

      modalVideo.pause();
      modalVideo.removeAttribute('src');
      if(progressInterval){ clearInterval(progressInterval); progressInterval = null; }

      // POST /add 启动 torrent 下载
      fetch('/torrent/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ magnet: magnet })
      })
      .then(function(r){ return r.json(); })
      .then(function(data){
        if(data.error){
          modalLoading.innerHTML = '<span>启动失败: ' + data.error + '</span>';
          return;
        }

        // 等待 torrent metadata 就绪后再设置视频源
        modalLoading.innerHTML = '<span>正在获取种子信息...</span>';
        var checkStartTime = Date.now();

        var checkReady = function(){
          var elapsed = Math.round((Date.now() - checkStartTime) / 1000);
          fetch('/torrent/status/' + hash)
          .then(function(r){ return r.json(); })
          .then(function(s){
            if(s.error || s.ready === false){
              // 超过 30 秒仍未就绪，提示放弃
              if(elapsed > 30){
                modalLoading.innerHTML = '<span>该种子暂时无法连接，请稍后再试</span>';
                return;
              }
              modalLoading.innerHTML = '<span>正在连接 Peers... (' + (s.peers || 0) + ') | 已等待 ' + elapsed + 's</span>';
              setTimeout(checkReady, 1500);
              return;
            }

            // metadata 就绪，等待头部数据下载
            modalLoading.innerHTML = '<span>缓冲中 | Peers: ' + s.peers + ' | 准备数据中...</span>';
            
            // 轮询进度，等待头部数据就绪
            var bufferWait = setInterval(function(){
              fetch('/torrent/status/' + hash)
              .then(function(r){ return r.json(); })
              .then(function(status){
                if(!status.ready) return;
                var speed = (status.download_rate / 1024 / 1024).toFixed(1);
                var pct = status.progress.toFixed(1);
                modalLoading.innerHTML = '<span>缓冲中 | Peers: ' + status.peers + ' | ' + speed + ' MB/s | ' + pct + '%</span>';
                
                // 进度超过 0.5% 认为头部数据已就绪（约 30MB）
                if(status.progress > 0.5){
                  clearInterval(bufferWait);
                  modalVideo.src = '/torrent/stream/' + hash;
                  
                  // canplay：有足够数据开始播放
                  modalVideo.addEventListener('canplay', function(){
                    if(progressInterval){ clearInterval(progressInterval); progressInterval = null; }
                    modalLoading.style.display = 'none';
                    modalVideo.play().catch(function(err){
                      console.error('Play error:', err);
                    });
                  }, { once: true });
                  
                  // 视频需要缓冲时显示 loading
                  modalVideo.addEventListener('waiting', function(){
                    modalLoading.style.display = 'flex';
                  });
                  
                  // 错误处理
                  modalVideo.addEventListener('error', function(){
                    if(progressInterval){ clearInterval(progressInterval); progressInterval = null; }
                    modalLoading.innerHTML = '<span>播放失败，请重试</span>';
                  }, { once: true });
                }
              })
              .catch(function(err){});
            }, 1000);
            
            // 30 秒超时
            setTimeout(function(){
              clearInterval(bufferWait);
            }, 30000);
          })
          .catch(function(err){
            console.error('Status check error:', err);
            setTimeout(checkReady, 2000);
          });
        };

        checkReady();
      })
      .catch(function(err){
        console.error('Torrent start failed:', err);
        modalLoading.innerHTML = '<span>无法连接播放服务器</span>';
      });
    });
  });

})();
</script>

</body>
</html>`;

fs.mkdirSync(path.dirname(OUT_PATH), { recursive: true });
fs.writeFileSync(OUT_PATH, html, 'utf8');
const size = (Buffer.byteLength(html) / 1024).toFixed(1);
console.log('[report] ✅ ' + OUT_PATH + ' (' + size + 'KB, ' + solo.length + ' actresses, ' + totalWorks + ' works)');
