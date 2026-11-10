/**
 * Genuine, source-bound LIVE read-only Graph development owner.
 *
 * This module is NOT an admission path. The authenticated Graph owner factory
 * must already have authenticated its producer, source-built executables,
 * Linux file limit, Host, original Store and closed native cohort. It keeps
 * a slice of that SAME parent SharedPool; no second pool/grant/retry.
 *
 * The read-only process remains LIVE when returned to Studio. Its sole
 * child and original streams are fully accounted for by close/assertClosed.
 */
import * as fs from 'node:fs';
import path from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import { SharedPool, OriginalWriter, bindRawPair, freshDirectory,
  inside, need, stableRead, GLOBAL_BYTES } from './custody.mjs';
import { assertGraphProducerAdmission } from './producer_guard.mjs';
import { captureOriginalOpenedStoreWitness, prepareOriginalReadOnlyHandoff,
  publishedOracleReference, publishOriginalWriterRpcRoster } from './same_store_reopen.mjs';
import { observeOpenedSQLiteDescriptor } from './opened_store_descriptor.mjs';
import { captureLinuxProcessIdentity, expectedLinuxHeldStoreDigest,
  requireHeldStoreWitness, requireSameOpenedProcessAndStore,
} from './opened_store_identity_join.mjs';

const MiB = 1024 * 1024;
const READ_ENV = Object.freeze({
  PATH:'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
  LANG:'C.UTF-8', LC_ALL:'C.UTF-8', TZ:'UTC',
  PYTHONNOUSERSITE:'1', RUST_BACKTRACE:'1',
  // The source-built ordinary reader lowers both kernel limits before opening
  // SQLite. ScopedChangeService independently observes /proc/<pid>/limits.
  MOTIONWRIGHT_GRAPH_STORE_FILE_BYTES:'4194304',
});
const same = (a,b,code) => need(isDeepStrictEqual(a,b),code);
const immutable = object => Object.freeze({ ...object });
const originalBinary = (factory) => {
  const expected = factory.admission.ordinaryService;
  need(expected && path.isAbsolute(expected.path)
    && /^[0-9a-f]{64}$/.test(expected.sha256)
    && fs.existsSync(expected.path), 'GRAPH_DEV_SOURCE_READONLY_BINARY_MISSING');
  const observed = stableRead(expected.path,GLOBAL_BYTES);
  same(observed.ref,expected,'GRAPH_DEV_SOURCE_READONLY_BINARY_CHANGED');
  fs.accessSync(expected.path,fs.constants.X_OK);
  return expected;
};

/** Immutable one-shot quota reservation BEFORE Native write/initializer. */
export function reserveOriginalDevelopmentReader(factory) {
  assertGraphProducerAdmission(factory?.admission);
  need(factory?.originalProduction === true
    && factory.pool instanceof SharedPool
    && !factory.closed && !factory.sealed
    && !factory.developmentReaderReservation,
    'GRAPH_DEV_READER_SOURCE_PRECHARGE_REQUIRED');
  originalBinary(factory);
  const raw = factory.pool.reserve('graph-dev-live-readonly-raw',
    'post-seal-originals', 2 * MiB, 2);
  const metadata = factory.pool.reserve('graph-dev-live-readonly-evidence',
    'post-seal-originals', 2 * MiB, 24);
  // Mutable consumed bit lives only on the authentic factory object;
  // the actual SharedPool reservations remain immutable and one-shot.
  factory.developmentReaderReservation = { raw, metadata, claimed: false };
  // Existing owner budget includes the future Store/CAS, Raw and cohorts.
  // Additional reader quota is NOT allowed to steal unreserved closure capacity.
  factory.requireFutureCapacity();
  return true;
}

class OriginalLiveReaderCustody {
  constructor(factory, binary, expectedStore) {
    const reservation = factory.developmentReaderReservation;
    need(reservation && reservation.claimed === false && factory.closed === true,
      'GRAPH_DEV_READER_PARENT_RESERVATION_OR_OWNER_CLOSE_MISSING');
    reservation.claimed = true;
    this.factory = factory;
    this.binary = binary;
    this.store = expectedStore;
    this.root = freshDirectory(path.join(factory.root,'graph-dev-live-reader'));
    this.meta = new OriginalWriter(this.root,factory.pool.counter(reservation.metadata));
    this.rawCounter = factory.pool.counter(reservation.raw);
    this.launch = null;
    this.rawSettled = false;
    this.exit = null;
    this.reader = null;
    this.failures = [];
    this.sealed = false;
    this.finished = false;
    this.meta.json(path.join(this.root,'source-intent.original.json'),{
      schema:'motionwright.graph-dev-live-readonly/1',
      source:factory.admission.source, binary, store:expectedStore,
      single_source_built_readonly:true, no_native_callback:true,
      original_parent_shared_pool:true, automatic_retry:false,
      formal_release_acceptance:false,
    });
  }
  taint(error) { this.failures.push(String(error)); }
  validateContext(context) {
    need(this.factory.closed === true && this.factory.failures.length === 0
      && !this.finished && !this.sealed
      && context && Object.isFrozen(context)
      && context.binary_path === this.binary.path
      && context.database_path === this.store
      && context.launch_index === 0
      && typeof context.helper_id === 'string'
      && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(context.helper_id),
      'GRAPH_DEV_READER_SOURCE_LAUNCH_CHANGED');
    same(stableRead(this.store,GLOBAL_BYTES).physical,
      this.factory.initializerPhysical,'GRAPH_DEV_READER_ORIGINAL_STORE_CHANGED');
    originalBinary(this.factory);
  }
  reserveLaunch(context) {
    this.validateContext(context);
    need(!this.launch,'GRAPH_DEV_READER_LAUNCH_REPLAY');
    this.launch = { context, raw:null };
    this.meta.json(path.join(this.root,'launch-intent.original.json'),{
      context, automatic_retry:false,
    });
  }
  async environmentForNewClient(context) {
    this.validateContext(context);
    need(this.launch?.context === context
      && this.launch.raw === null,'GRAPH_DEV_READER_AUTHORITY_MISMATCH');
    return immutable({
      ...READ_ENV,
      HOME:this.root, XDG_RUNTIME_DIR:this.root,
      XDG_STATE_HOME:this.root, XDG_CONFIG_HOME:this.root,
      XDG_CACHE_HOME:this.root, TMPDIR:this.root,
    });
  }
  bind(context, streams) {
    try {
      this.validateContext(context);
      need(this.launch?.context === context && this.launch.raw === null
        && Number.isSafeInteger(streams.process_id) && streams.process_id > 0,
        'GRAPH_DEV_READER_RAW_BINDING_UNKNOWN');
      this.launch.pid = streams.process_id;
      this.meta.json(path.join(this.root,'spawn.original.json'),{
        context, process_id:streams.process_id, automatic_retry:false,
      });
      const raw=bindRawPair({
        root:this.root, prefix:'reader', counter:this.rawCounter,
        streams, streamLimit:MiB, deadlineUnixMs:this.factory.deadline,
        onFailure:error=>this.taint(error),
      });
      this.launch.raw=raw;
      streams.exited.then(exit=>{
        this.exit=exit;
      }, error=>this.taint(error));
      const settled=raw.settled.then(value=>{
        this.rawSettled=true;
        this.launch.rawResult=value;
      }, error=>{this.taint(error);throw error});
      settled.catch(()=>undefined);
      return { settled:settled.then(()=>undefined) };
    } catch(error){this.taint(error);throw error}
  }
  async publish(file,bytes) {
    need(Buffer.isBuffer(bytes),'GRAPH_DEV_READER_EXACT_ORIGINAL_BYTES_REQUIRED');
    return this.meta.publish(inside(this.root,file),bytes);
  }
  async inventory(directory) {
    // Not needed for a live reader. Never expose a mutable Store inventory
    // as an admission or allow a read-only service to copy the original.
    throw Error('GRAPH_DEV_READER_LIVE_INVENTORY_NOT_AUTHORITY');
  }
  async copyClosedStore() {
    throw Error('GRAPH_DEV_READER_CANNOT_COPY_ORIGINAL_STORE');
  }
  sealClientAdmissions(){this.sealed=true}
  async closeAfterClients(){
    throw Error('GRAPH_DEV_READER_CANNOT_FINALIZE_ORIGINAL_WRITER');
  }
  assertClosed(){
    need(this.sealed && this.reader && this.reader.isReadOnly
      && this.reader.processStreams.length===1
      && this.reader.processExits.length===1
      && this.reader.lifecycleTaints.length===0
      && this.launch?.raw && this.rawSettled && this.exit
      && this.exit.code===0 && this.exit.signal===null
      && this.reader.processStreams[0].stdout_ended
      && this.reader.processStreams[0].stderr_ended
      && this.reader.processStreams[0].jsonl_reader_closed
      && this.failures.length===0,'GRAPH_DEV_READER_ACTUAL_TERMINAL_NOT_CLOSED');
    if(!this.finished){
      this.meta.json(path.join(this.root,'reader-terminal.original.json'),{
        source:this.factory.admission.source,
        pid:this.launch.pid, exit:this.exit,
        raw_streams:this.launch.rawResult,
        closed_after_read_only_proof:true,
        automatic_retry:false,
      });
      this.finished=true;
    }
  }
}

export async function openOriginalDevelopmentReadOnly(factory, writer,
  expectedProject, expectedHistory, Service) {
  assertGraphProducerAdmission(factory?.admission);
  need(factory?.developmentReaderReservation
    && factory.developmentReaderReservation.claimed===false
    && !factory.developmentReaderOwner
    && factory.originalProduction===true
    && writer && writer.binaryPath===factory.admission.nativeService.path
    && writer.databasePath===factory.options.databasePath
    && Array.isArray(expectedHistory)
    && typeof expectedProject?.id==='string'
    && expectedHistory.length>0,
    'GRAPH_DEV_READER_EXACT_ORIGINAL_WRITER_REQUIRED');
  // No new original launch on a missing/uncertain writer result.
  const current=await writer.project(expectedProject.id);
  same(current,expectedProject,'GRAPH_DEV_READER_WRITER_PROJECT_CHANGED');
  const actualHistory=await writer.history(
    expectedProject.id,expectedProject.revision-expectedHistory.length);
  same(actualHistory,expectedHistory,'GRAPH_DEV_READER_WRITER_HISTORY_CHANGED');
  let preseal, handoff;
  const outputRoot=inside(factory.root,path.join(factory.root,'graph-dev-closed-store'));
  const finalization=await writer.finalizeClosedStore(outputRoot,expectedProject,[],[
    async ()=>{
      preseal=await captureOriginalOpenedStoreWitness(factory,writer,expectedProject);
      handoff=await prepareOriginalReadOnlyHandoff(factory,writer,preseal);
    },
  ]);
  factory.assertClosed();
  const writerOracleRef=publishedOracleReference(factory,outputRoot,finalization);
  await publishOriginalWriterRpcRoster(factory,writer);
  need(preseal && handoff,'GRAPH_DEV_READER_ORIGINAL_WAL_HANDOFF_MISSING');
  const before=stableRead(factory.options.databasePath,GLOBAL_BYTES).physical;
  same(before,factory.initializerPhysical,'GRAPH_DEV_READER_PHYSICAL_ORIGINAL_REPLACED');
  const binary=originalBinary(factory);
  const custody=new OriginalLiveReaderCustody(factory,binary,factory.options.databasePath);
  factory.developmentReaderOwner=custody;
  let reader;
  try {
    reader=await Service.createReadOnly({
      binaryPath:binary.path,
      databasePath:factory.options.databasePath,
      processOwner:custody, rawStreamCapture:custody,
      evidenceCapture:custody, graphCustodyRequired:true,
      seedFixture:false, desktopInvokeFixture:false, timeoutMs:20000,
    });
    custody.reader=reader;
    need(reader.isReadOnly && reader.binaryPath===binary.path
      && reader.databasePath===factory.options.databasePath
      && reader.processStreams.length===1
      && reader.processStreams[0].process_id===custody.launch.pid
      && reader.processExits.length===0
      && reader.lifecycleTaints.length===0,
      'GRAPH_DEV_READER_SOURCE_BUILT_PROCESS_NOT_LIVE');
    const pid=custody.launch.pid;
    const proc=captureLinuxProcessIdentity(pid);
    const beforeOpen=observeOpenedSQLiteDescriptor({pid,expected:before});
    requireSameOpenedProcessAndStore(beforeOpen,beforeOpen,proc);
    const identity=requireHeldStoreWitness(
      await reader.openedStoreIdentityWitness(),expectedLinuxHeldStoreDigest(before));
    // The existing source-owned collector reads every page from revision zero,
    // rejects gaps/truncation, and rechecks Project stability after the journal.
    // Matching only the last Save would miss loss of earlier persisted events.
    const independentOracle=await reader.observeStoredProject(expectedProject);
    const project=independentOracle.project;
    same(project,finalization.oracle.project,'GRAPH_DEV_READER_INDEPENDENT_PROJECT_CHANGED');
    same(independentOracle.journal,finalization.oracle.journal,
      'GRAPH_DEV_READER_COMPLETE_ORIGINAL_HISTORY_CHANGED');
    const afterRevision=expectedProject.revision-expectedHistory.length;
    same(independentOracle.journal.filter(event=>event.revision>afterRevision),
      expectedHistory,'GRAPH_DEV_READER_INDEPENDENT_HISTORY_CHANGED');
    const afterOpen=observeOpenedSQLiteDescriptor({pid,expected:before});
    requireSameOpenedProcessAndStore(beforeOpen,afterOpen,proc);
    same(stableRead(factory.options.databasePath,GLOBAL_BYTES).physical,
      before,'GRAPH_DEV_READER_SQLITE_REPLACED_DURING_READ');
    // SQLite alone is insufficient: this source-owned Store also has a
    // managed CAS anchor, sibling control and original directory identity.
    // Reuse the exact pre-paid Store+CAS inventory contract after the read.
    factory.verifyStoreAllocation();
    factory.assertClosed();
    await custody.publish(path.join(custody.root,'readback.original.json'),
      Buffer.from(JSON.stringify({
        schema:'motionwright.graph-dev-live-reader-open/1',
        source:factory.admission.source,
        process_id:pid, process:proc, opened_before:beforeOpen,
        opened_after:afterOpen, physical_store:before,
        held_sqlite_pager_witness:identity,
        writer_oracle:writerOracleRef,
        full_project:project, journal:independentOracle.journal,
        journal_after_revision_exclusive:0, journal_complete:true,
        native_callbacks_allowed:false, automatic_retry:false,
        returned_live:true, formal_release_acceptance:false,
      })+'\n'));
    return reader;
  } catch(error) {
    custody.taint(error);
    if(reader){
      try{await reader.close()}catch(cleanup){custody.taint(cleanup)}
    }
    throw error;
  }
}

export function assertOriginalDevelopmentReaderClosed(factory) {
  assertGraphProducerAdmission(factory?.admission);
  const custody=factory.developmentReaderOwner;
  need(custody,
    'GRAPH_DEV_READER_ORIGINAL_OWNER_NOT_READY_FOR_CLOSE');
  custody.sealClientAdmissions();
  custody.assertClosed();
  factory.assertClosed();
}
