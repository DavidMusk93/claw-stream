#!/usr/bin/env node
/**
 * torrent-server.mjs — WebTorrent HTTP 流服务器
 *
 * 功能：
 *   1. 接收 magnet 链接，用 WebTorrent 下载
 *   2. 优先下载视频头尾部（MP4 moov atom 快速启动）
 *   3. 已完整下载的文件直接用 fs.createReadStream（绕过 WebTorrent）
 *   4. 本地缓存 + LRU 淘汰
 *   5. CORS 支持，浏览器直接播放
 *
 * 端点：
 *   POST /add           { magnet }           → 添加/预加载种子
 *   GET  /stream/<hash>                      → 视频流（Range 支持，本地文件优先）
 *   GET  /status/<hash>                      → 下载状态 JSON
 *   GET  /cache                              → 缓存状态列表
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

// 优先下载头尾部大小
const HEAD_BYTES = 5 * 1024 * 1024;   // 5MB
const TAIL_BYTES = 1 * 1024 * 1024;   // 1MB

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

// hash → { torrent, addedAt, lastAccess, videoFilePath, videoFileName }
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

  let candidates = torrent.files.filter(f => {
    const ext = path.extname(f.name).toLowerCase();
    return VIDEO_EXTS.includes(ext);
  });

  if (candidates.length === 0) candidates = torrent.files;
  candidates = candidates.filter(f => !SPAM_PATTERNS.some(p => p.test(f.name)));
  if (candidates.length === 0) candidates = torrent.files;

  return candidates.sort((a, b) => b.length - a.length)[0];
}

/** 检查本地文件是否已完整下载 */
function isFileFullyCached(videoFile) {
  if (!videoFile) return false;
  const localPath = path.join(videoFile._torrent.path, videoFile.path);
  try {
    const stat = fs.statSync(localPath);
    return stat.size === videoFile.length;
  } catch (_) {
    return false;
  }
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

/** 优先下载视频头尾部 */
function prioritizeHeadAndTail(videoFile) {
  if (!videoFile) return;
  const len = videoFile.length;

  // 高优先级下载头部
  const headEnd = Math.min(HEAD_BYTES, len) - 1;
  videoFile.select(0, headEnd, 10);
  console.log('[prioritize] head', videoFile.name, '0-', headEnd);

  // 高优先级下载尾部（moov 可能在尾部）
  if (len > TAIL_BYTES) {
    const tailStart = len - TAIL_BYTES;
    videoFile.select(tailStart, len - 1, 10);
    console.log('[prioritize] tail', videoFile.name, tailStart, '-', len - 1);
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
  const info = { torrent, magnet, addedAt: Date.now(), lastAccess: Date.now(), videoFilePath: null, videoFileName: null };
  torrents.set(hash, info);

  // metadata 就绪后，选择视频文件并优先下载头尾部
  torrent.on('metadata', () => {
    const vf = pickVideoFile(torrent);
    if (vf) {
      info.videoFileName = vf.name;
      info.videoFilePath = path.join(torrentDir, vf.path);
      prioritizeHeadAndTail(vf);
    }
  });

  torrent.on('done', () => {
    console.log('[torrent] done:', torrent.name, hash);
    evictIfNeeded();
  });

  torrent.on('error', (err) => {
    console.error('[torrent] error:', hash, err.message);
  });

  torrent.on('warning', (err) => {
    if (err.message && err.message.includes('tracker')) return;
    console.warn('[torrent] warning:', hash, err.message);
  });

  return info;
}

/** 处理流式请求（本地文件优先） */
function handleStream(req, res, info, videoFile) {
  info.lastAccess = Date.now();

  // 检查本地文件是否完整
  const localPath = info.videoFilePath;
  const useLocalFile = localPath && isFileFullyCached(videoFile);

  if (useLocalFile) {
    console.log('[stream] serving from local file:', localPath);
  }

  const total = videoFile.length;
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

    if (useLocalFile) {
      fs.createReadStream(localPath, { start, end }).pipe(res);
    } else {
      videoFile.createReadStream({ start, end }).pipe(res);
    }
  } else {
    res.writeHead(200, {
      'Accept-Ranges': 'bytes',
      'Content-Length': videoFile.length,
      'Content-Type': 'video/mp4',
    });

    if (useLocalFile) {
      fs.createReadStream(localPath).pipe(res);
    } else {
      videoFile.createReadStream().pipe(res);
    }
  }
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

    handleStream(req, res, info, videoFile);
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
      cached: videoFile ? isFileFullyCached(videoFile) : false,
    }));
    return;
  }

  // ── GET /cache ─────────────────────────────────────
  if (pathname === '/cache' && req.method === 'GET') {
    const items = [];
    for (const [hash, info] of torrents) {
      const t = info.torrent;
      const vf = pickVideoFile(t);
      items.push({
        hash,
        name: t.name,
        videoFile: vf ? vf.name : null,
        videoSize: vf ? vf.length : 0,
        peers: t.numPeers,
        progress: Math.round(t.progress * 10000) / 100,
        speed: t.downloadSpeed,
        done: t.done,
        ready: !!(t.files && t.files.length > 0),
        cached: vf ? isFileFullyCached(vf) : false,
        lastAccess: info.lastAccess,
        addedAt: info.addedAt,
      });
    }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      totalSize: getCacheSize(),
      maxSize: MAX_SIZE_BYTES,
      itemCount: items.length,
      items,
    }));
    return;
  }

  // ── GET / ──────────────────────────────────────────
  if (pathname === '/') {
    res.writeHead(200, { 'Content-Type': 'text/plain' });
    res.end('Torrent Stream Server\nEndpoints:\n  POST /add { magnet }\n  GET /stream/<hash>\n  GET /status/<hash>\n  GET /cache\n');
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
