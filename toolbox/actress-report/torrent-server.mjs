#!/usr/bin/env node
/**
 * torrent-server.mjs — WebTorrent HTTP 流服务器
 *
 * 功能：
 *   1. 接收 magnet 链接，用 WebTorrent 下载
 *   2. 提供 HTTP 流式播放（支持 Range 请求）
 *   3. 本地缓存 + LRU 淘汰
 *   4. CORS 支持，浏览器直接播放
 *
 * 端点：
 *   POST /add           { magnet }           → 添加/预加载种子
 *   GET  /stream/<hash>                      → 视频流（Range 支持）
 *   GET  /status/<hash>                      → 下载状态 JSON
 *   GET  /progress/<hash>                    → text/event-stream 实时进度
 *
 * 启动：
 *   node torrent-server.mjs --port 8768 --max-size 20
 */

import WebTorrent from 'webtorrent';
import http from 'http';
import fs from 'fs';
import path from 'path';
import { URL } from 'url';

// ── 配置 ──────────────────────────────────────────────
const PORT = parseInt(process.argv.find((_, i, a) => i > 0 && a[i - 1] === '--port') || '8768', 10);
const MAX_SIZE_GB = parseInt(process.argv.find((_, i, a) => i > 0 && a[i - 1] === '--max-size') || '20', 10);
const MAX_SIZE_BYTES = MAX_SIZE_GB * 1024 * 1024 * 1024;

const SCRIPT_DIR = path.dirname(new URL(import.meta.url).pathname);
const CACHE_DIR = path.join(SCRIPT_DIR, 'cache', 'torrent');

// 垃圾文件名黑名单（正则）
const SPAM_PATTERNS = [
  /游戏大全/i,
  /996gg/i,
  /hhd800/i,
  /^\d+\.txt$/i,
  /^readme/i,
  /\.url$/i,
  /\.txt$/i,
];

// 视频扩展名
const VIDEO_EXTS = ['.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.m4v', '.webm'];

fs.mkdirSync(CACHE_DIR, { recursive: true });

// ── WebTorrent 客户端 ─────────────────────────────────
const client = new WebTorrent();

// hash → { torrent, addedAt, lastAccess }
const torrents = new Map();

function getHash(magnet) {
  const m = magnet.match(/xt=urn:btih:([a-f0-9]{40})/i);
  return m ? m[1].toLowerCase() : null;
}

function getTorrentDir(hash) {
  return path.join(CACHE_DIR, hash);
}

/** 选择最佳视频文件 */
function pickVideoFile(torrent) {
  if (!torrent.files || torrent.files.length === 0) return null;

  // 过滤出视频文件
  let candidates = torrent.files.filter(f => {
    const ext = path.extname(f.name).toLowerCase();
    return VIDEO_EXTS.includes(ext);
  });

  if (candidates.length === 0) candidates = torrent.files;

  // 排除垃圾文件
  candidates = candidates.filter(f => !SPAM_PATTERNS.some(p => p.test(f.name)));
  if (candidates.length === 0) candidates = torrent.files;

  // 选择最大的
  return candidates.sort((a, b) => b.length - a.length)[0];
}

/** 计算 torrent 缓存目录总大小 */
function getCacheSize() {
  let total = 0;
  try {
    const entries = fs.readdirSync(CACHE_DIR);
    for (const hash of entries) {
      const dir = path.join(CACHE_DIR, hash);
      const stat = fs.statSync(dir);
      if (stat.isDirectory()) {
        const files = fs.readdirSync(dir);
        for (const f of files) {
          try {
            total += fs.statSync(path.join(dir, f)).size;
          } catch (_) {}
        }
      }
    }
  } catch (_) {}
  return total;
}

/** LRU 淘汰：删除最旧的 torrent */
function evictIfNeeded() {
  const current = getCacheSize();
  if (current < MAX_SIZE_BYTES) return;

  // 按 lastAccess 排序，删除最旧的
  const sorted = Array.from(torrents.entries())
    .filter(([, info]) => info.torrent.done)
    .sort((a, b) => a[1].lastAccess - b[1].lastAccess);

  for (const [hash, info] of sorted) {
    if (getCacheSize() < MAX_SIZE_BYTES * 0.8) break;

    console.log('[lru] evicting', hash, info.torrent.name);
    client.remove(info.torrent);
    torrents.delete(hash);

    const dir = getTorrentDir(hash);
    try {
      fs.rmSync(dir, { recursive: true, force: true });
    } catch (e) {
      console.error('[lru] rm failed:', e.message);
    }
  }
}

/** 添加/获取种子 */
function getOrAddTorrent(magnet) {
  const hash = getHash(magnet);
  if (!hash) return null;

  if (torrents.has(hash)) {
    const info = torrents.get(hash);
    info.lastAccess = Date.now();
    return info;
  }

  const torrentDir = getTorrentDir(hash);
  fs.mkdirSync(torrentDir, { recursive: true });

  const torrent = client.add(magnet, { path: torrentDir });
  const info = { torrent, magnet, addedAt: Date.now(), lastAccess: Date.now() };
  torrents.set(hash, info);

  torrent.on('done', () => {
    console.log('[torrent] done:', torrent.name, hash);
    evictIfNeeded();
  });

  torrent.on('error', (err) => {
    console.error('[torrent] error:', hash, err.message);
  });

  torrent.on('warning', (err) => {
    // 忽略常见的 tracker 警告
    if (err.message && err.message.includes('tracker')) return;
    console.warn('[torrent] warning:', hash, err.message);
  });

  return info;
}

// ── HTTP 服务器 ───────────────────────────────────────
const server = http.createServer((req, res) => {
  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Range');

  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  const url = new URL(req.url, `http://${req.headers.host}`);
  const pathname = url.pathname;

  // ── POST /add ──────────────────────────────────────
  if (pathname === '/add' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const data = JSON.parse(body);
        const magnet = data.magnet;
        if (!magnet) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'missing magnet' }));
          return;
        }
        const hash = getHash(magnet);
        if (!hash) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'invalid magnet' }));
          return;
        }
        const info = getOrAddTorrent(magnet);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
          hash,
          name: info.torrent.name,
          status: 'added',
          peers: info.torrent.numPeers,
          progress: info.torrent.progress,
        }));
      } catch (e) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  // ── GET /stream/<hash> ─────────────────────────────
  const streamMatch = pathname.match(/^\/stream\/([a-f0-9]{40})$/i);
  if (streamMatch && req.method === 'GET') {
    const hash = streamMatch[1].toLowerCase();
    const info = torrents.get(hash);

    if (!info) {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'torrent not found, POST /add first' }));
      return;
    }

    info.lastAccess = Date.now();
    const torrent = info.torrent;

    // 等待 metadata 就绪
    if (!torrent.files || torrent.files.length === 0) {
      res.writeHead(503, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'torrent metadata not ready yet', hash }));
      return;
    }

    const videoFile = pickVideoFile(torrent);
    if (!videoFile) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'no video file found' }));
      return;
    }

    const range = req.headers.range;
    if (range) {
      const parts = range.replace(/bytes=/, '').split('-');
      const start = parseInt(parts[0], 10);
      const end = parts[1] ? parseInt(parts[1], 10) : videoFile.length - 1;
      const chunksize = (end - start) + 1;
      res.writeHead(206, {
        'Content-Range': `bytes ${start}-${end}/${videoFile.length}`,
        'Accept-Ranges': 'bytes',
        'Content-Length': chunksize,
        'Content-Type': 'video/mp4',
      });
      videoFile.createReadStream({ start, end }).pipe(res);
    } else {
      res.writeHead(200, {
        'Accept-Ranges': 'bytes',
        'Content-Length': videoFile.length,
        'Content-Type': 'video/mp4',
      });
      videoFile.createReadStream().pipe(res);
    }
    return;
  }

  // ── GET /status/<hash> ─────────────────────────────
  const statusMatch = pathname.match(/^\/status\/([a-f0-9]{40})$/i);
  if (statusMatch && req.method === 'GET') {
    const hash = statusMatch[1].toLowerCase();
    const info = torrents.get(hash);

    if (!info) {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'not found' }));
      return;
    }

    const t = info.torrent;
    const videoFile = pickVideoFile(t);

    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      hash,
      name: t.name,
      videoFile: videoFile ? videoFile.name : null,
      videoSize: videoFile ? videoFile.length : 0,
      peers: t.numPeers,
      progress: Math.round(t.progress * 10000) / 100,
      downloaded: t.downloaded,
      length: t.length,
      speed: t.downloadSpeed,
      done: t.done,
      ready: !!(t.files && t.files.length > 0),
    }));
    return;
  }

  // ── GET / ──────────────────────────────────────────
  if (pathname === '/') {
    res.writeHead(200, { 'Content-Type': 'text/plain' });
    res.end('Torrent Stream Server\nEndpoints:\n  POST /add { magnet }\n  GET /stream/<hash>\n  GET /status/<hash>\n');
    return;
  }

  res.writeHead(404);
  res.end('Not found');
});

server.listen(PORT, () => {
  console.log(`[torrent-server] listening on http://localhost:${PORT}`);
  console.log(`[torrent-server] cache dir: ${CACHE_DIR}`);
  console.log(`[torrent-server] max size: ${MAX_SIZE_GB}GB`);
});

// ── 优雅退出 ──────────────────────────────────────────
process.on('SIGINT', () => {
  console.log('\n[torrent-server] shutting down...');
  client.destroy(() => {
    server.close(() => process.exit(0));
  });
});

process.on('SIGTERM', () => {
  console.log('\n[torrent-server] shutting down...');
  client.destroy(() => {
    server.close(() => process.exit(0));
  });
});
