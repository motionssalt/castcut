import test from "node:test";
import assert from "node:assert/strict";

// Mirror functions from site/app.js & site/functions/_lib/gh.js
const VIDEO_EXT = [".mkv", ".mp4", ".m4v", ".mov", ".webm", ".avi", ".ts", ".m2ts"];

function bdecode(u8) {
  let i = 0;
  const txt = (b) => new TextDecoder().decode(b);
  function parse() {
    const c = u8[i];
    if (c === 0x69) {
      i++; const e = u8.indexOf(0x65, i); if (e < 0) throw new Error("bad integer");
      const v = parseInt(txt(u8.slice(i, e)), 10); if (Number.isNaN(v)) throw new Error("bad integer");
      i = e + 1; return v;
    }
    if (c === 0x6c) { i++; const a = []; while (i < u8.length && u8[i] !== 0x65) a.push(parse()); i++; return a; }
    if (c === 0x64) { i++; const o = new Map(); while (i < u8.length && u8[i] !== 0x65) { const k = parse(); o.set(txt(k), parse()); } i++; return o; }
    if (c >= 0x30 && c <= 0x39) {
      const colon = u8.indexOf(0x3a, i); if (colon < 0) throw new Error("bad string");
      const len = parseInt(txt(u8.slice(i, colon)), 10); i = colon + 1;
      const v = u8.slice(i, i + len); if (v.length !== len) throw new Error("truncated");
      i += len; return v;
    }
    throw new Error("not a .torrent file");
  }
  return parse();
}

function parseTorrentFiles(buf) {
  const root = bdecode(new Uint8Array(buf));
  if (!(root instanceof Map)) throw new Error("not a .torrent file");
  const info = root.get("info");
  if (!(info instanceof Map)) throw new Error("torrent has no info dictionary");
  const dec = (b) => new TextDecoder().decode(b);
  const name = info.get("name") ? dec(info.get("name")) : "torrent";
  const files = [];
  const rawFiles = info.get("files");
  if (Array.isArray(rawFiles)) {
    rawFiles.forEach((f, idx) => {
      const parts = (f.get("path") || []).map(dec);
      const path = parts.join("/");
      files.push({ index: idx + 1, path, length: f.get("length") || 0 });
    });
  } else {
    files.push({ index: 1, path: name, length: info.get("length") || 0 });
  }
  files.forEach(f => { f.is_video = VIDEO_EXT.includes(("." + f.path.split(".").pop()).toLowerCase()) && f.length > 0; });
  return { name, files };
}

function bytesToBase64(bytes) {
  const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const CHUNK_SIZE = 8192;
  let binary = "";
  for (let i = 0; i < u8.length; i += CHUNK_SIZE) {
    binary += String.fromCharCode.apply(null, u8.subarray(i, i + CHUNK_SIZE));
  }
  return btoa(binary);
}

function naturalSortFiles(files) {
  return [...files].sort((a, b) => (a.path || "").localeCompare(b.path || "", undefined, { numeric: true, sensitivity: "base" }));
}

// Bencode serializer for building synthetic torrent test fixtures
function encodeBencode(val) {
  if (typeof val === "number") {
    return Buffer.from(`i${val}e`);
  }
  if (typeof val === "string") {
    const buf = Buffer.from(val, "utf8");
    return Buffer.concat([Buffer.from(`${buf.length}:`), buf]);
  }
  if (Buffer.isBuffer(val)) {
    return Buffer.concat([Buffer.from(`${val.length}:`), val]);
  }
  if (Array.isArray(val)) {
    const parts = [Buffer.from("l")];
    for (const item of val) parts.push(encodeBencode(item));
    parts.push(Buffer.from("e"));
    return Buffer.concat(parts);
  }
  if (val && typeof val === "object") {
    const parts = [Buffer.from("d")];
    const keys = Object.keys(val).sort();
    for (const k of keys) {
      parts.push(encodeBencode(k));
      parts.push(encodeBencode(val[k]));
    }
    parts.push(Buffer.from("e"));
    return Buffer.concat(parts);
  }
  throw new Error("unsupported value");
}

function createSyntheticTorrent(fileCount, pieceCount = 1000) {
  const files = [];
  for (let i = 1; i <= fileCount; i++) {
    const pad = String(i).padStart(4, "0");
    const ext = i % 10 === 0 ? "nfo" : i % 7 === 0 ? "srt" : "mkv";
    files.push({
      length: 1024 * 1024 * 350 + i * 1024,
      path: [`Code Geass Lelouch of the Rebellion R2 - Episode ${i} (1080p BD FLAC).${ext}`],
    });
  }
  // Realistic piece hashes (20 bytes per piece, 1000 pieces = 20 KB of binary)
  const pieceHashes = Buffer.alloc(pieceCount * 20, 0xab);
  const torrentDict = {
    announce: "http://tracker.example.com/announce",
    info: {
      name: "Code Geass Lelouch of the Rebellion R2 [1080p BD-Rip FLAC]",
      "piece length": 1024 * 1024,
      pieces: pieceHashes,
      files,
    },
  };
  return encodeBencode(torrentDict);
}

test("Task A Regression: bytesToBase64 handles large buffers without Maximum call stack size exceeded", () => {
  // Test with various buffer sizes up to 2 MB (which easily crashed String.fromCharCode(...arr))
  for (const size of [50_000, 100_000, 200_000, 500_000, 1_500_000]) {
    const buf = new Uint8Array(size);
    for (let i = 0; i < size; i++) buf[i] = (i * 31) & 0xff;
    
    let b64;
    assert.doesNotThrow(() => {
      b64 = bytesToBase64(buf);
    }, `Failed at size ${size}`);

    const expected = Buffer.from(buf).toString("base64");
    assert.equal(b64, expected, `Mismatch for size ${size}`);
  }
});

test("Task A Regression: 50-file torrent parses and encodes without RangeError", () => {
  const torrentBuf = createSyntheticTorrent(50, 8000); // realistic size (~170 KB)
  assert.ok(torrentBuf.length > 100_000, "Torrent buffer is realistic size (>100KB)");

  const parsed = parseTorrentFiles(torrentBuf);
  assert.equal(parsed.name, "Code Geass Lelouch of the Rebellion R2 [1080p BD-Rip FLAC]");
  assert.equal(parsed.files.length, 50);

  // Assert base64 conversion does NOT overflow call stack
  assert.doesNotThrow(() => {
    const b64 = bytesToBase64(torrentBuf);
    assert.ok(b64.length > 0);
  });
});

test("Task A Regression: 200-file and 1000-file torrents scale cleanly", () => {
  for (const count of [200, 1000]) {
    const torrentBuf = createSyntheticTorrent(count, 5000);
    assert.ok(torrentBuf.length > 50_000);

    const parsed = parseTorrentFiles(torrentBuf);
    assert.equal(parsed.files.length, count);

    assert.doesNotThrow(() => {
      const b64 = bytesToBase64(torrentBuf);
      assert.ok(b64.length > 0);
    });
  }
});

test("Task B: Default selection rules", () => {
  // Case 1: Exactly 1 video file -> pre-select
  const singleVideo = [
    { index: 1, path: "Movie.mp4", is_video: true, length: 1000 },
    { index: 2, path: "Subs.srt", is_video: false, length: 50 },
  ];
  const videos1 = singleVideo.filter(f => f.is_video);
  const checked1 = new Set(videos1.length === 1 ? [videos1[0].index] : []);
  assert.equal(checked1.size, 1);
  assert.ok(checked1.has(1));

  // Case 2: Multi-video torrent (50 files) -> select NOTHING by default
  const torrentBuf = createSyntheticTorrent(50);
  const parsed = parseTorrentFiles(torrentBuf);
  const videos50 = parsed.files.filter(f => f.is_video);
  assert.ok(videos50.length > 1);

  const checked50 = new Set(videos50.length === 1 ? [videos50[0].index] : []);
  assert.equal(checked50.size, 0, "Multi-video torrent must select 0 files by default");
});

test("Task B: Natural sorting orders episodes correctly", () => {
  const files = [
    { index: 1, path: "Series S01E10.mkv" },
    { index: 2, path: "Series S01E02.mkv" },
    { index: 3, path: "Series S01E01.mkv" },
    { index: 4, path: "Series S01E20.mkv" },
    { index: 5, path: "Series S01E03.mkv" },
  ];
  const sorted = naturalSortFiles(files);
  const paths = sorted.map(f => f.path);
  assert.deepEqual(paths, [
    "Series S01E01.mkv",
    "Series S01E02.mkv",
    "Series S01E03.mkv",
    "Series S01E10.mkv",
    "Series S01E20.mkv",
  ]);
  // Original indices are preserved on the objects
  assert.equal(sorted[0].index, 3);
  assert.equal(sorted[1].index, 2);
  assert.equal(sorted[2].index, 5);
  assert.equal(sorted[3].index, 1);
  assert.equal(sorted[4].index, 4);
});

test("Task B: Search filtering does not lose existing selections", () => {
  const files = [
    { index: 1, path: "Attack on Titan - 01.mkv", is_video: true },
    { index: 2, path: "Attack on Titan - 02.mkv", is_video: true },
    { index: 3, path: "Attack on Titan - OVA 01.mkv", is_video: true },
  ];
  const checked = new Set([1]); // User picked episode 1

  // User filters for "OVA"
  const q = "ova";
  const visible = files.filter(f => f.path.toLowerCase().includes(q));
  assert.equal(visible.length, 1);
  assert.equal(visible[0].index, 3);

  // User checks OVA (index 3) while filtered
  checked.add(visible[0].index);

  // User clears filter
  assert.ok(checked.has(1), "Original selection (1) retained");
  assert.ok(checked.has(3), "New selection (3) added");
  assert.equal(checked.size, 2);
});
