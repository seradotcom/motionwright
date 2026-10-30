import * as fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

export const GLOBAL_BYTES = 512 * 1024 * 1024;
export const GLOBAL_FILES = 4096;
export const RECORD_BYTES = 16 * 1024 * 1024;
export function need(ok, code) { if (!ok) throw new Error(code); }
export function integer(value, low = 0, high = Number.MAX_SAFE_INTEGER) {
  return Number.isSafeInteger(value) && value >= low && value <= high;
}
export function canonical(value) {
  need(typeof value === 'string' && path.isAbsolute(value) && !value.includes('\0')
    && path.normalize(value) === value && !value.split(path.sep).includes('..'), 'PATH_SHAPE');
  return value;
}
export function inside(root, value) {
  canonical(value);
  const relative = path.relative(root, value);
  need(relative !== '' && relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative), 'PATH_ESCAPED');
  return value;
}
/** Pin every directory component through a held descriptor. Linux procfs is
 * used only as Node's descriptor-relative path bridge; no untrusted named
 * parent is followed after the pin is acquired. All names are revalidated
 * against the held descriptors before publication and after observation. */
export function pinDirectory(value) {
  canonical(value); need(process.platform === 'linux', 'DIRECTORY_PINS_REQUIRE_LINUX');
  const held = [];
  try {
    const flags = fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW;
    let fd = fs.openSync('/', flags);
    held.push({ fd, name: null, physical: fs.fstatSync(fd, { bigint: true }) });
    for (const name of value.split('/').filter(Boolean)) {
      need(name !== '.' && name !== '..', 'DIRECTORY_COMPONENT');
      fd = fs.openSync('/proc/self/fd/' + fd + '/' + name, flags);
      held.push({ fd, name, physical: fs.fstatSync(fd, { bigint: true }) });
    }
    let closed = false;
    const pin = {
      fd,
      entry(name) { need(!closed && typeof name === 'string' && name.length > 0 && !name.includes('/') && !name.includes('\0') && name !== '.' && name !== '..', 'PIN_ENTRY'); return '/proc/self/fd/' + fd + '/' + name; },
      verify() {
        need(!closed, 'DIRECTORY_PIN_CLOSED');
        for (const [index, item] of held.entries()) {
          const current = fs.fstatSync(item.fd, { bigint: true });
          const named = index === 0 ? fs.lstatSync('/', { bigint: true })
            : fs.lstatSync('/proc/self/fd/' + held[index - 1].fd + '/' + item.name, { bigint: true });
          need(current.isDirectory() && named.isDirectory() && !named.isSymbolicLink()
            && current.dev === item.physical.dev && current.ino === item.physical.ino
            && named.dev === current.dev && named.ino === current.ino, 'DIRECTORY_PIN_CHANGED');
        }
      },
      close() { if (closed) return; closed = true; let error; for (const item of held.toReversed()) { try { fs.closeSync(item.fd); } catch (failure) { error ??= failure; } } if (error) throw error; },
    };
    pin.verify(); return pin;
  } catch (error) { for (const item of held.toReversed()) { try { fs.closeSync(item.fd); } catch {} } throw error; }
}
export function existingDirectory(value) {
  const pin = pinDirectory(value); try { pin.verify(); return value; } finally { pin.close(); }
}
export function freshDirectory(value) {
  canonical(value); const parent = pinDirectory(path.dirname(value));
  try {
    parent.verify(); const named = parent.entry(path.basename(value));
    fs.mkdirSync(named, { mode: 0o700 });
    const info = fs.lstatSync(named);
    need(info.isDirectory() && !info.isSymbolicLink() && (info.mode & 0o777) === 0o700, 'PRIVATE_DIRECTORY_MODE');
    fs.fsyncSync(parent.fd); parent.verify(); return value;
  } finally { parent.close(); }
}
export function safeParents(root, destination) {
  inside(root, destination); existingDirectory(root);
  let current = root;
  for (const component of path.relative(root, path.dirname(destination)).split(path.sep).filter(Boolean)) {
    need(component !== '.' && component !== '..', 'PATH_TRAVERSAL');
    current = path.join(current, component);
    try { existingDirectory(current); }
    catch (error) { if (error.code !== 'ENOENT') throw error; freshDirectory(current); }
  }
}
function fileStamp(info) {
  return Object.fromEntries(['dev','ino','size','mtimeNs','ctimeNs','nlink'].map(name => [name,String(info[name])]));
}
function sameStamp(first, second) { return Object.keys(first).every(name => first[name] === second[name]); }
export function stableRead(file, maximum, io = fs) {
  canonical(file); need(integer(maximum, 0, GLOBAL_BYTES), 'READ_BOUND');
  const parent = pinDirectory(path.dirname(file)); let fd;
  try {
    parent.verify(); const namedPath = parent.entry(path.basename(file));
    fd = io.openSync(namedPath, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
    const before = io.fstatSync(fd, { bigint: true });
    need(before.isFile() && before.nlink === 1n && before.size <= BigInt(maximum), 'ORIGINAL_FILE_KIND_OR_BOUND');
    const size = Number(before.size), data = Buffer.alloc(size); let offset = 0;
    while (offset < size) { const count = io.readSync(fd, data, offset, size - offset, offset); need(count > 0, 'ORIGINAL_SHORT_READ'); offset += count; }
    const after = io.fstatSync(fd, { bigint: true }), named = io.lstatSync(namedPath, { bigint: true });
    for (const name of ['dev', 'ino', 'size', 'mtimeNs', 'ctimeNs']) need(before[name] === after[name] && after[name] === named[name], 'ORIGINAL_CHANGED');
    need(!named.isSymbolicLink() && named.nlink === 1n, 'ORIGINAL_LINK'); parent.verify();
    return { data, ref: { path: file, sha256: createHash('sha256').update(data).digest('hex'), size_bytes: size },
      physical: { path: file, device: String(before.dev), inode: String(before.ino) }, stamp: fileStamp(before) };
  } finally { try { if (fd !== undefined) io.closeSync(fd); } finally { parent.close(); } }
}

/** Instantiated exactly once by the Node case factory. Other processes only
 * receive a consumed immutable subquota; they never instantiate this allocator. */
export class SharedPool {
  #ids = new Set(); #allocations = []; #issued = new Set();
  constructor({ bytes = GLOBAL_BYTES, files = GLOBAL_FILES } = {}) {
    need(integer(bytes, 1, GLOBAL_BYTES) && integer(files, 1, GLOBAL_FILES), 'POOL_BOUND');
    this.maximumBytes = bytes; this.maximumFiles = files;
    this.reservedBytes = 0; this.reservedFiles = 0;
  }
  reserve(id, kind, bytes, files) {
    need(typeof id === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,179}$/.test(id), 'RESERVATION_ID');
    need(!this.#ids.has(id), 'RESERVATION_REUSE'); this.#ids.add(id);
    need(integer(bytes, 1, this.maximumBytes) && integer(files, 1, this.maximumFiles), 'SUBQUOTA_BOUND');
    need(this.reservedBytes + bytes <= this.maximumBytes && this.reservedFiles + files <= this.maximumFiles, 'POOL_EXHAUSTED');
    this.reservedBytes += bytes; this.reservedFiles += files;
    const value = Object.freeze({ id, kind, bytes, files, sequence: this.#allocations.length });
    this.#allocations.push(value); return value;
  }
  counter(reservation) {
    need(this.#allocations[reservation?.sequence] === reservation && !this.#issued.has(reservation.sequence), 'COUNTER_NOT_UNIQUE');
    this.#issued.add(reservation.sequence); return new SubquotaCounter(reservation);
  }
  external(reservation) {
    need(this.#allocations[reservation?.sequence] === reservation && !this.#issued.has(reservation.sequence), 'EXTERNAL_RESERVATION_REUSE');
    this.#issued.add(reservation.sequence);
    return Object.freeze({ bytes: reservation.bytes, files: reservation.files });
  }
  observations() { return this.#allocations.map(value => ({ ...value })); }
}
class SubquotaCounter {
  constructor(reservation) { this.reservation = reservation; this.bytes = 0; this.files = 0; this.failed = false; }
  charge(bytes, newFile = false) {
    need(!this.failed, 'SUBQUOTA_TAINTED');
    if (!integer(bytes) || typeof newFile !== 'boolean' || this.bytes + bytes > this.reservation.bytes
        || this.files + Number(newFile) > this.reservation.files) {
      this.failed = true; throw new Error('SUBQUOTA_EXHAUSTED');
    }
    this.bytes += bytes; this.files += Number(newFile);
  }
  fail() { this.failed = true; }
}

/** Exact, exclusive original publication. Partial writes consume the full
 * attempted charge. A failure permanently taints this assigned writer. */
export class OriginalWriter {
  constructor(root, counter, io = fs) { this.root = existingDirectory(root); this.counter = counter; this.io = io; }
  publish(file, raw) {
    let fd, parent;
    try {
      need(Buffer.isBuffer(raw), 'ORIGINAL_REQUIRES_BYTES');
      inside(this.root, file);
      this.counter.charge(raw.length, true);
      safeParents(this.root, file); parent = pinDirectory(path.dirname(file)); parent.verify();
      fd = this.io.openSync(parent.entry(path.basename(file)), fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
      parent.verify(); let offset = 0;
      while (offset < raw.length) { const count = this.io.writeSync(fd, raw, offset, raw.length - offset); need(count > 0, 'ORIGINAL_ZERO_WRITE'); offset += count; }
      this.io.fsyncSync(fd); const written = this.io.fstatSync(fd, { bigint: true });
      need(written.isFile() && written.nlink === 1n && written.size === BigInt(raw.length), 'ORIGINAL_WRITTEN_FILE');
      this.io.closeSync(fd); fd = undefined;
      fs.fsyncSync(parent.fd); parent.verify();
      const observed = stableRead(file, raw.length);
      need(sameStamp(fileStamp(written),observed.stamp) && observed.data.equals(raw), 'ORIGINAL_READBACK_IDENTITY');
      parent.verify(); return observed.ref;
    } catch (error) { this.counter.fail(); throw error; }
    finally { try { if (fd !== undefined) { try { this.io.closeSync(fd); } catch { this.counter.fail(); } } } finally { try { parent?.close(); } catch (error) { this.counter.fail(); throw error; } } }
  }
  json(file, value) { return this.publish(file, Buffer.from(JSON.stringify(value, null, 2) + '\n', 'utf8')); }
}

/** One async writer per raw stream, bounded to one queued chunk by pause/resume.
 * It observes Buffer bytes before decoders and charges each actual write first.
 * On failure it drains without retaining additional bytes, then rejects; no
 * retained prefix can be mistaken for a complete stream. */
export function bindRawPair({ root, prefix, counter, streams, streamLimit, deadlineUnixMs, onFailure = () => {}, io = fs }) {
  need(integer(streamLimit, 1, 1048576) && integer(deadlineUnixMs, 1), 'STREAM_BOUND');
  const prepared = [];
  const report = error => { counter.fail(); try { onFailure(error); } catch {} };
  // Prepare both exclusive files before installing a listener or timer. A
  // failed second open closes every descriptor already acquired; charges and
  // partial originals remain consumed, and the synchronous hook throws.
  try {
    for (const name of ['stdout', 'stderr']) {
      const input = streams[name]; need(input && typeof input.on === 'function' && typeof input.pause === 'function', 'RAW_STREAM_REQUIRED');
      input.pause();
      const destination = inside(root, path.join(root, prefix + '.' + name + '.original'));
      safeParents(root, destination);
      const parent = pinDirectory(path.dirname(destination));
      const item = { input, destination, parent, fd: undefined }; prepared.push(item);
      counter.charge(0, true); parent.verify();
      item.fd = io.openSync(parent.entry(path.basename(destination)), fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
      parent.verify();
    }
  } catch (error) {
    report(error);
    for (const item of prepared) { try { if (item.fd !== undefined) io.closeSync(item.fd); } catch {} finally { item.parent.close(); } }
    for (const name of ['stdout','stderr']) streams[name]?.resume();
    throw error;
  }
  const outcomes = prepared.map(({ input, destination, parent, fd }) => {
    let bytes = 0, pending = false, ended = false, closed = false, failure = null, settled = false, descriptorOpen = true;
    const expectedHash = createHash('sha256');
    let resolveDone, rejectDone;
    const done = new Promise((resolve, reject) => { resolveDone = resolve; rejectDone = reject; }); done.catch(() => undefined);
    const fail = error => { if (!failure) { failure = error instanceof Error ? error : new Error(String(error)); report(failure); } };
    let timer;
    const finish = () => {
      if (settled || pending || (!ended && !closed && !failure)) return;
      settled = true; clearTimeout(timer);
      let observed;
      try {
        io.fsyncSync(fd); const written = io.fstatSync(fd, { bigint: true });
        need(written.isFile() && written.nlink === 1n, 'RAW_WRITTEN_FILE');
        io.closeSync(fd); descriptorOpen = false; fs.fsyncSync(parent.fd); parent.verify();
        observed = stableRead(destination, streamLimit);
        need(sameStamp(fileStamp(written),observed.stamp), 'RAW_READBACK_IDENTITY');
        if (!failure) need(written.size === BigInt(bytes) && observed.ref.sha256 === expectedHash.digest('hex'), 'RAW_READBACK_BYTES');
        parent.verify();
        if (!ended) fail(new Error('RAW_EOF_MISSING'));
      } catch (error) { fail(error); }
      finally {
        try { if (descriptorOpen) { io.closeSync(fd); descriptorOpen = false; } } catch (error) { fail(error); }
        try { parent.close(); } catch (error) { fail(error); }
      }
      if (failure) rejectDone(failure); else resolveDone({ ...observed.ref, eof: true });
    };
    timer = setTimeout(() => { fail(new Error('RAW_SETTLEMENT_DEADLINE')); finish(); input.resume(); }, Math.max(1, deadlineUnixMs - Date.now()));
    input.on('data', chunk => {
      if (failure || settled) return;
      try {
        need(Buffer.isBuffer(chunk), 'RAW_WAS_DECODED'); need(!pending, 'RAW_BACKPRESSURE_BYPASSED');
        need(bytes + chunk.length <= streamLimit, 'RAW_STREAM_EXHAUSTED'); parent.verify();
        counter.charge(chunk.length); bytes += chunk.length; expectedHash.update(chunk); input.pause(); pending = true; let offset = 0;
        const writeNext = () => {
          try {
            io.write(fd, chunk, offset, chunk.length - offset, null, (error, written) => {
              if (error || written <= 0) { fail(error ?? new Error('RAW_ZERO_WRITE')); pending = false; finish(); input.resume(); return; }
              offset += written;
              if (offset < chunk.length) writeNext();
              else { pending = false; finish(); input.resume(); }
            });
          } catch (error) { fail(error); pending = false; finish(); input.resume(); }
        };
        if (chunk.length) writeNext(); else { pending = false; finish(); input.resume(); }
      } catch (error) { fail(error); finish(); input.resume(); }
    });
    input.once('end', () => { ended = true; finish(); });
    input.once('close', () => { closed = true; if (!ended) fail(new Error('RAW_CLOSED_BEFORE_EOF')); finish(); });
    input.once('error', error => { fail(error); finish(); input.resume(); });
    return done;
  });
  for (const item of prepared) item.input.resume();
  // Reject only after both sinks have closed. An early failure is reported
  // immediately, but may not masquerade as completed cleanup of the pair.
  const settled = Promise.allSettled(outcomes).then(results => {
    const failures = results.filter(result => result.status === 'rejected').map(result => result.reason);
    if (failures.length) throw new AggregateError(failures, 'RAW_PAIR_CUSTODY_FAILED');
    return Object.freeze({ stdout: results[0].value, stderr: results[1].value });
  });
  settled.catch(() => undefined); return { settled };
}

/** Bounded inventory with the exact existing Service StoreEntry fields. No SQLite
 * opens or writes; links, hardlinks, traversal and oversized trees reject. */
export function inventoryTree(directory, { bytes = GLOBAL_BYTES, files = GLOBAL_FILES } = {}) {
  need(integer(bytes, 0, GLOBAL_BYTES) && integer(files, 0, GLOBAL_FILES), 'TREE_BOUND');
  const rootPin = pinDirectory(directory), entries = []; let total = 0, count = 0;
  const visit = (current, name) => {
    need(entries.length <= files * 2 + 1, 'TREE_ENTRY_BOUND');
    const parent = pinDirectory(path.dirname(current));
    try {
      const info = fs.lstatSync(parent.entry(path.basename(current))); need(!info.isSymbolicLink(), 'TREE_LINK');
      if (info.isDirectory()) {
        const pin = pinDirectory(current);
        try {
          entries.push({ path: name, kind: 'directory' });
          const children = fs.readdirSync('/proc/self/fd/' + pin.fd).sort(); need(children.length <= files * 2 + 1, 'TREE_DIRECTORY_BOUND');
          for (const child of children) { need(child !== '.' && child !== '..' && !child.includes(path.sep), 'TREE_MEMBER'); visit(path.join(current, child), name ? name + '/' + child : child); }
          pin.verify();
        } finally { pin.close(); }
      } else {
        need(info.isFile() && info.nlink === 1 && ++count <= files && total + info.size <= bytes, 'TREE_FILE_BOUND');
        const original = stableRead(current, Math.min(bytes - total, info.size)); total += original.data.length;
        entries.push({ path: name, kind: 'file', sizeBytes: original.ref.size_bytes, sha256: original.ref.sha256 });
      }
      parent.verify();
    } finally { parent.close(); }
  };
  try { visit(directory, ''); rootPin.verify(); return entries; } finally { rootPin.close(); }
}
export function copyTreeExclusive(source, destination, writer) {
  canonical(source); canonical(destination);
  const relative = path.relative(source, destination);
  need(relative === '..' || relative.startsWith('..' + path.sep), 'COPY_SOURCE_DESTINATION_OVERLAP');
  const before = inventoryTree(source, { bytes: writer.counter.reservation.bytes - writer.counter.bytes,
    files: writer.counter.reservation.files - writer.counter.files });
  safeParents(writer.root, destination); freshDirectory(destination);
  for (const item of before) {
    if (!item.path) continue;
    const from = path.join(source, ...item.path.split('/')), to = inside(destination, path.join(destination, ...item.path.split('/')));
    if (item.kind === 'directory') freshDirectory(to);
    else {
      const original = stableRead(from, item.sizeBytes);
      need(original.ref.sha256 === item.sha256 && original.ref.size_bytes === item.sizeBytes, 'COPY_SOURCE_CHANGED');
      writer.publish(to, original.data);
    }
  }
  need(JSON.stringify(inventoryTree(source)) === JSON.stringify(before)
    && JSON.stringify(inventoryTree(destination)) === JSON.stringify(before), 'COPY_TREE_CHANGED');
  return before;
}
