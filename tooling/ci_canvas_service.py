#!/usr/bin/env python3
"""Run closed Studio selections with original source and process custody."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import stat
import tarfile

CANVAS_TITLES = (
    "structural removal denial is accessible and preserves authored hierarchy through repeated revisions and reopen",
    "an empty library reaches thirteen authored revisions while object, scene and workspace drafts survive a process restart",
    "an independent peer wins overlapping copy while disjoint geometry and locked drafts are preserved",
    "a late real transform acknowledgement preserves newer invalid raw input without replay after reload",
    "another tab restores foreign input explicitly and changed recovery bytes cannot be overwritten or discarded from an older view",
    "lost reply after real Canvas commit cannot replay the already saved operation",
    "Canvas pointer readback is required before real-service commit and explicit reload restores draft",
    "authored Canvas style persists across two real revisions and reopen without losing uncommitted typography",
    "Canvas hierarchy retains parent and raw Z through two real revisions and a service restart",
    "Canvas relation add and remove preserve stable IDs and source intent through reopen",
    "Canvas removal isolates the real children denial and permits explicit repair after reopen",
    "Canvas removal isolates the real incoming relations denial and permits explicit repair after reopen",
    "Canvas removal stays pending through selection and remount before the real commit response",
    "Canvas removal stays pending through selection and remount after the real commit response",
    "Canvas removal committed with a lost reply stays unknown until real SQLite reopen and never replays",
    "Canvas removal sends nothing when its record cannot be retained and verified",
    "Canvas removal sends nothing when its pointer cannot be retained and verified",
    "saved parented Canvas text stays visible in real editorial study after service reopen",
    "saved localized profile text overrides the editorial SVG across two revisions and reopening",
)
INK_TITLE = "a saved ink token changes the actual SVG text paint across revisions and reopen"
PROGRAM_TITLES = (
    "Timeline Program renders actual saved Canvas text, style and selected locale through revisions and restart",
    "a real saved partial cut excludes an editable Program scene until an explicit full-sequence revision",
    "real SQLite profile cut with stale localized text shows only the excluded original source after reopen",
)
BRIEF_TITLES = (
    "saving the title or brief commits only that group and conserves the other raw inputs",
    "a late real reply preserves newer typing and reload never replays an already committed brief",
    "returning an in-flight field to its original value survives navigation and reload until explicitly saved",
    "a real brief validation failure preserves every input through reload and correction",
    "two real clients resolve only the overlapping brief field by keeping the saved value",
    "two real clients resolve only the overlapping brief field by keeping the draft value",
    "reopening revokes a real grant without changing the revision or clearing the refused brief",
)
BRIEF_RECOVERY_TITLES = (
    "lost brief session writer pointer stops service grant; explicit Restore saves once and survives reopen",
)
BRIEF_UNCERTAINTY_TITLES = (
    "an unrelated r+1 is reviewed without acknowledging or replaying an uncertain Brief",
    "a committed Brief followed by r+2 preserves later input until separate review",
    "Brief record retention failure is NOT_SENT and retry backup permits one explicit save",
    "Brief pointer retention failure is NOT_SENT and retry backup permits one explicit save",
    "a late Brief reply cannot consume the pending state of a new recovery writer",
    "a failed recovery fork pointer keeps the earlier Brief UNKNOWN and sends nothing",
)
ACTIVE_VOICE_TITLES = (
    "active voice selection preserves both measured sources and canonical state through keyboard save and reopen",
    "a held original selection reply blocks duplicate sends across workspace remount",
    "an unrelated r+1 leaves active voice UNKNOWN until separate saved-state review",
    "a committed active voice followed by r+2 remains UNKNOWN after lost reply and reload",
    "active voice record retention failure is NOT_SENT and reload never submits it",
    "active voice pointer retention failure is NOT_SENT and reload never submits it",
)
CAMERA_TITLES = (
    "Camera reframe keeps raw input, commits twice and reopens exact SQLite Project",
    "Camera unconfirmed response cannot replay a real SQLite mutation",
    "a peer upsert with identical saved Camera values cannot acknowledge the held original",
    "a peer detach with identical saved Camera values cannot acknowledge the held original",
    "a same-revision Camera inspection cannot release an original that is still held",
    "a late saved-Camera read for scene A preserves the exact visible author copy of scene B",
    "a successful original Camera reply closes only scene A while scene B keeps its exact author bytes",
)
GESTURE_TITLES = (
    "rapid pointer move and release commits one exact Canvas transform without losing the final delta",
    "pointercancel discards the transient delta and later pointerup cannot apply it",
    "quick release with Auto-key writes a single X/Y motion pair, not a base transform",
    "a pose gesture cannot overwrite a peer after same-view reopen and release",
    "a pose gesture cannot overwrite a peer after same-view reopen and cancel",
    "a keyed gesture cannot overwrite a peer after same-view reopen and release",
    "a keyed gesture cannot overwrite a peer after same-view reopen and cancel",
)
NARRATIVE_TITLES = (
    'Narrative drafts survive actual workspace navigation/reload; separate premise and objective commits survive SQLite restart',
    'a held original premise receipt preserves newer typing across actual workspace remount',
    'a held original objective receipt preserves newer typing across actual workspace remount',
    'an uncertain Narrative unrelated outcome survives reload and is reviewed before a separate intent',
    'an uncertain Narrative committed outcome survives reload and is reviewed before a separate intent',
    'a lost Narrative writer pointer blocks real grants and writes; explicit Restore commits exactly once',
    'a real objective validation refusal preserves both fields until fresh review and correction',
    "an independent service author's overlapping premise is reviewed without consuming a disjoint objective draft",
)
BEAT_DRAFT_TITLES = (
    'beat raw drafts survive actual workspace navigation and reload before one exact SQLite commit and restart',
    'a held original beat receipt preserves later raw edits through actual workspace remount and a separate save',
    'an uncertain beat committed outcome requires real project/history review before any separate intent',
    'an uncertain beat preempted outcome requires real project/history review before any separate intent',
    'a peer retime preserves the local beat draft until explicit text with saved timing is committed',
    'a peer removal retains beat text across reload and only an explicit recreation creates a new event',
    'a shorter saved scene retains invalid beat times until the author explicitly compares and repairs them',
    'a silent beat writer pointer failure blocks real grants and saves until an explicit copy restore',
    'a failed beat ACK checkpoint preserves original UNKNOWN bytes and later text until real fresh review',
    'a real original beat removal keeps later typing through restart without an automatic recreation event',
    'a beat exported during local recovery failure imports without a write and saves exactly once to SQLite after repair',
)

BEAT_TIME_TITLES = (
    'copy-only scene beat edits retain exact fractional frame times in SQLite and after process restart',
    'authored decimal and sample times save exactly while incomplete and out-of-scene inputs cannot commit',
    'Fill timing gap commits the exact shared frame boundary and preserves both beats after restart',
    'a saved sub-microsecond gap remains editable and cannot display exact coverage until filled',
)

DELIVERY_RECIPE_TITLES = (
    'two profiles retain both canonical groups through explicit saves, clear and fresh-process reopen',
    'a committed reply held across reload preserves Sent then Old until fresh history review and explicit conflict resolution',
    'typing across Film profile and mix scopes during a real save retains every unsent input',
    'two real clients reject a stale save, review current history and resolve only the overlapping field with keep saved',
    'two real clients reject a stale save, review current history and resolve only the overlapping field with keep draft',
    'invalid numeric raw survives a real reload and only corrected values reach the service',
    'a full recovery budget preserves memory and blocks service writes until a verified copy fits',
    'an unreadable own copy is retained until explicit discard without losing newer typed input',
    "a new window with copied session storage offers another window's draft only by explicit recovery",
    'project content locks and a real revoked grant retain inputs for a fresh reviewed intent without replay',
    'Film and PCM guards use saved options and expose unsupported combinations without pretending a native result',
    "portable export and verified import preserve both recipes and CAS while isolating the new generation's drafts",
)

RECIPE_AUTHORITY_TITLES = (
    'real film peer values never acknowledge an uncertain recipe request; fresh history review precedes a separate save',
    'real mix peer values never acknowledge an uncertain recipe request; fresh history review precedes a separate save',
    'a real original recipe ACK preserves later raw numeric typing even when it returns to the submitted value',
    'silent recipe data recovery failure prevents a real grant or SQLite write; verified retry saves once',
    'silent recipe pointer recovery failure prevents a real grant or SQLite write; verified retry saves once',
)

HISTORICAL_REVIEW_TITLES = (
    'review raw range survives actual App navigation and reload before one exact SQLite commit and restart',
    'a held original review reply preserves later input before a distinct granted save',
    'a committed but lost review reply remains UNKNOWN through explicit saved-history inspection and a separate intent',
    'denied review custody prevents any SQLite mutation until a confirmed copy and explicit save',
    'an original resolution keeps a later raw reason through actual reopen and new SQLite acknowledgement',
    'an actual saved alternative retains raw rationale and records only an open manual review',
)

REVIEW_ABA_TITLE = 'ABA author input survives lost review reply, inspection, separate save and SQLite restart'
REVIEW_TITLES = (
    *HISTORICAL_REVIEW_TITLES[:3], REVIEW_ABA_TITLE,
    *HISTORICAL_REVIEW_TITLES[3:],
)

INSPECTOR_TITLES = (
    'Inspector raw objective and exact fraction survive actual App navigation before SQLite commits and restart',
    'held original Inspector reply retains later input before a separately granted save',
    'committed lost Inspector reply stays UNKNOWN through inspection and a distinct new intent',
    'denied Inspector custody prevents SQLite mutation until verified recovery and explicit save',
    'incomplete Inspector duration survives actual service restart without mutation',
    'Inspector stale input requires explicit retention against actual saved source',
)

PROFILE_METADATA_TITLES = (
    'raw fields survive A-B-A, an unrelated real revision, in-app reopen and browser reload without a profile write',
    'a delayed real save preserves later A input and selected B, then only an explicit valid save reaches SQLite',
    'an actual stale CAS refusal preserves sent and later raw input without retry before explicit conflict review',
    'an unreadable local recovery copy stays intact while later input is kept and only explicit discard permits saving',
    'a newly created output profile does not replace the active draft when its real response arrives late',
    'a real profile Save stops before its grant when recovery becomes blocked: changed-before-save',
    'a real profile Save stops before its grant when recovery becomes blocked: readback-differs',
    'an unknown own copy blocks actual Save until explicit reading proves absence and preserves the later raw',
    'a known quota failure blocks real Save until verified recovery without changing the authored raw',
    'custom cut stays empty until an explicit full-sequence choice across reload and real revisions',
    'a custom cut typed empty after a real save stays empty through ACK, reload and a peer full-sequence conflict',
    'a locally empty cut does not adopt a peer custom list and reviews repeated saved cuts without losing other raw input',
    'a draft whose profile was removed remains readable and downloadable, then editable after repeated revisions restore that exact profile',
    'a removed profile keeps corrupt recovery bytes and later window input as separate downloadable snapshots without a profile write',
)

PROFILE_AUTHORITY_TITLES = (
    'original profile reply confirms exact raw-derived fields in SQLite and after process restart',
    'held original profile reply retains later identical spelling and incomplete input before a separately granted save',
    'lost committed profile reply retains UNKNOWN through restart and explicit later intent',
    'lost committed profile reply retains UNKNOWN through restart and explicit clean inspection',
    'silent profile writer-pointer failure prevents grants and SQLite changes until verified recovery',
    'profile recovery budget blocks actual transport without evicting any retained original',
)

PROJECTION_NAVIGATION_TITLES = (
    'remembered 65-scene App opening and process restart use bounded native pages without complete reads',
    'explicit small-project browsing pages 129 native nodes and preserves off-page references',
    'a real peer revision returns typed stale-page failure and preserves the view until explicit reload',
    'a lost genuine node-page delivery is reported safely without fallback, replay or lost prior page',
    'bounded observations retain an original committed Review UNKNOWN and later authored text without replay',
    'a genuinely missing selected head preserves preference and allows an explicit healthy page choice',
)

SELECTIONS = (
    ("canvas", "canvas-draft-service.spec.ts", CANVAS_TITLES),
    ("ink", "visual-language-draft-service.spec.ts", (INK_TITLE,)),
    ("program", "program-design-service.spec.ts", PROGRAM_TITLES),
    ("brief", "brief-service.spec.ts", BRIEF_TITLES),
    ("brief-recovery", "brief-recovery-admission-service.spec.ts", BRIEF_RECOVERY_TITLES),
    ("brief-uncertain", "brief-uncertain-outcome-service.spec.ts", BRIEF_UNCERTAINTY_TITLES),
    ("active-voice", "audio-active-voice-service.spec.ts", ACTIVE_VOICE_TITLES),
    ("camera", "camera-reframe-service.spec.ts", CAMERA_TITLES),
    ("gesture", "canvas-gesture-service.spec.ts", GESTURE_TITLES),
    ("narrative", "narrative-service.spec.ts", NARRATIVE_TITLES),
    ('beat-draft', 'scene-beat-draft-service.spec.ts', BEAT_DRAFT_TITLES),
    ('beat-time', 'scene-beat-time-service.spec.ts', BEAT_TIME_TITLES),
    ('delivery-recipe', 'delivery-recipe.spec.ts', DELIVERY_RECIPE_TITLES),
    ('recipe-authority', 'delivery-recipe-authority-service.spec.ts', RECIPE_AUTHORITY_TITLES),
    ('review', 'review-service.spec.ts', REVIEW_TITLES),
    ('inspector', 'scene-inspector-service.spec.ts', INSPECTOR_TITLES),
    ('profile-metadata', 'delivery-profile-preservation.spec.ts', PROFILE_METADATA_TITLES),
    ('profile-authority', 'delivery-profile-authority-service.spec.ts', PROFILE_AUTHORITY_TITLES),
    ('projection-navigation', 'project-projection-service.spec.ts', PROJECTION_NAVIGATION_TITLES),
)
PROJECT_AUTHORING_SELECTIONS = SELECTIONS
HISTORICAL_PROJECT_AUTHORING_SELECTIONS = tuple(
    (label, filename, HISTORICAL_REVIEW_TITLES if label == "review" else titles)
    for label, filename, titles in PROJECT_AUTHORING_SELECTIONS
)
CREATIVE_PRODUCTION_SELECTIONS = (('production-design',
  'production-design-service.spec.ts',
  ('Production plan raw drafts, claim references and content-bound approval survive actual App navigation '
   'and SQLite restart',
   'Production Hero updates retain editable node identities and a human override; detach preserves objects '
   'across SQLite restart',
   'a held original Production component receipt preserves later typing across actual App remount and a '
   'separate save',
   'a held original Production plan receipt preserves later typing across actual App remount and a '
   'separate save',
   'an unknown Production plan committed result survives reload without replay and needs a fresh history '
   'review',
   'an unknown Production plan matching-peer result survives reload without replay and needs a fresh '
   'history review',
   'Production data recovery failure sends no real service grant or change; explicit healthy retry '
   'persists the retained draft',
   'Production pointer recovery failure sends no real service grant or change; explicit healthy retry '
   'persists the retained draft')),
 ('creative-workbench',
  'creative-workbench-service.spec.ts',
  ('stage actual App updates preserve stable editable objects and a human override; detach survives SQLite '
   'restart',
   'procedural actual App updates preserve stable editable objects and a human override; detach survives '
   'SQLite restart',
   'Measured actual source bytes, versioned table and Canvas binding persist through App editing and '
   'SQLite restart',
   'Measured original binding receipt preserves later raw selection and precision across actual App '
   'remount',
   'stage an actual committed response lost in transport remains UNKNOWN through reload and requires fresh '
   'history review',
   'procedural an actual committed response lost in transport remains UNKNOWN through reload and requires '
   'fresh history review',
   'measured an actual committed response lost in transport remains UNKNOWN through reload and requires '
   'fresh history review',
   'creative workbench data backup failure sends no real grant or change; explicit healthy retry saves '
   'retained input',
   'creative workbench pointer backup failure sends no real grant or change; explicit healthy retry saves '
   'retained input')))
BRANCH_CLAIM_SELECTIONS = (('branch-authoring',
  'branch-authoring-service.spec.ts',
  ('causal: a rejected branch creation preserves the exact authored name',
   'causal: a late original branch reply preserves a later name',
   'causal: an unsaved branch name survives App navigation and reload without a mutation',
   'one explicit name creates one editable branch through SQLite and a new service process',
   'UTF-8 branch limit keeps invalid input without sending it',
   'silent branch recovery bytes loss blocks creation until exact readback',
   'silent branch recovery pointer loss blocks creation until exact readback',
   'a committed but lost branch reply survives reopen and explicit inspection without replay',
   'a later A-to-B-to-A branch name stays editable after its original reply and reload',
   'restoring an unconfirmed branch preserves both copies after silent pointer failure',
   'restoring an unconfirmed branch preserves both copies after throw pointer failure',
   'restoring an unconfirmed branch preserves both copies after write-then-throw pointer failure',
   'an ordinary branch name survives a failed first pointer read before one explicit creation',
   'an UNKNOWN branch and a deliberately cleared name survive failed first pointer read without replay',
   'an ordinary branch name survives a failed first copy read before one explicit creation',
   'an UNKNOWN branch and a deliberately cleared name survive failed first copy read without replay',
   'a failed initial pointer read can recover an empty session without discarding newly typed input')),
 ('claim-source',
  'claim-source-service.spec.ts',
  ('raw source revision and inactive source buffers survive App navigation and reload before one valid '
   'commit',
   'same-text source edit invalidates its review and supports a new situated review through SQLite reopen',
   'a committed lost Claim reply stays UNKNOWN through reload and only a separate intent writes again',
   "a held original reply preserves later source input and another claim's independent draft",
   'unconfirmed silent-copy blocks all Claim dispatch until explicit recovery succeeds',
   'unconfirmed silent-pointer blocks all Claim dispatch until explicit recovery succeeds',
   "a real external edit causes a stale save conflict without overwriting either author's work",
   'explicit removal of source and version preserves claim identity through restart',
   'a held Add reply cannot erase the next authored claim in the new-claim editor',
   'retry after initial copy read failure restores untouched raw input before one explicit real save',
   'retry after initial pointer read failure restores untouched raw input before one explicit real save',
   'an edit and return to saved values during initial copy read failure preserves later intent and original '
   'UNKNOWN',
   'an edit and return to saved values during initial pointer read failure preserves later intent and '
   'original UNKNOWN',
   'retry after the first pointer read fails accepts a confirmed empty slot without losing newly authored '
   'raw input')))
BRANCH_CLAIM_UNITS = (('src/branchDraft.test.ts',
  (('branch author intent and exact original result', 'raw name belongs to the exact id'),
   ('branch author intent and exact original result', 'raw name belongs to the exact generation'),
   ('branch author intent and exact original result', 'raw name belongs to the exact active_branch'),
   ('branch author intent and exact original result',
    'incomplete and invalid raw names are retained through the recovery codec'),
   ('branch author intent and exact original result', 'invalid name "" is rejected without rewriting input'),
   ('branch author intent and exact original result',
    'invalid name "   " is rejected without rewriting input'),
   ('branch author intent and exact original result',
    'invalid name "🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂" is rejected without rewriting input'),
   ('branch author intent and exact original result',
    'invalid name "name\\u0000" is rejected without rewriting input'),
   ('branch author intent and exact original result',
    'invalid name "name\x85" is rejected without rewriting input'),
   ('branch author intent and exact original result',
    'native ASCII case and UTF-8 size limits are matched without silent truncation'),
   ('branch author intent and exact original result',
    'matching and unrelated property observations cannot acknowledge UNKNOWN'),
   ('branch author intent and exact original result',
    'only the original callback with an exact added branch can clear its submitted name'),
   ('branch author intent and exact original result',
    'later author input survives the original branch reply'),
   ('branch author intent and exact original result', 'ABA author input survives the original branch reply'),
   ('branch author intent and exact original result',
    'an invalid revision original result keeps the unconfirmed name'),
   ('branch author intent and exact original result',
    'an invalid owner original result keeps the unconfirmed name'),
   ('branch author intent and exact original result',
    'an invalid missing original result keeps the unconfirmed name'),
   ('branch author intent and exact original result',
    'an invalid name original result keeps the unconfirmed name'),
   ('branch author intent and exact original result',
    'an invalid parent original result keeps the unconfirmed name'),
   ('branch author intent and exact original result',
    'an invalid base original result keeps the unconfirmed name'),
   ('branch author intent and exact original result',
    'an invalid workspace original result keeps the unconfirmed name'),
   ('branch author intent and exact original result',
    'an invalid extra original result keeps the unconfirmed name'),
   ('branch author intent and exact original result',
    'a rejection remains UNKNOWN while a proven local no-dispatch failure can be prepared explicitly'),
   ('branch copy readback and concurrent writers', 'raw bytes and the tab pointer must both be confirmed'),
   ('branch copy readback and concurrent writers',
    'a restored UNKNOWN carries its exact source guard through a new writer and reload'),
   ('branch copy readback and concurrent writers',
    'twenty reloads of one UNKNOWN preserve its raw name within the same recovery budget'),
   ('branch copy readback and concurrent writers',
    'silent bytes publication cannot count as a confirmed recovery'),
   ('branch copy readback and concurrent writers',
    'silent pointer publication cannot count as a confirmed recovery'),
   ('branch copy readback and concurrent writers',
    'a peer pointer installed during data publication is retained and blocks publication'),
   ('branch copy readback and concurrent writers',
    'a final byte change after pointer publication remains visible and blocks retry'),
   ('branch copy readback and concurrent writers', "another writer's bytes is never silently replaced"),
   ('branch copy readback and concurrent writers', "another writer's pointer is never silently replaced"),
   ('branch copy readback and concurrent writers',
    'full or corrupt copies stay intact when the bounded catalogue refuses another name'),
   ('branch copy readback and concurrent writers', 'a corrupted scope envelope cannot become a saved name'),
   ('branch copy readback and concurrent writers', 'a corrupted writer envelope cannot become a saved name'),
   ('branch copy readback and concurrent writers', 'a corrupted attempt envelope cannot become a saved name'),
   ('branch copy readback and concurrent writers',
    'a corrupted sequence envelope cannot become a saved name'),
   ('branch copy readback and concurrent writers', 'a corrupted extra envelope cannot become a saved name'),
   ('explicit saved-source review never invents an original acknowledgement',
    'separation preserves UNKNOWN and later ABA=false without replay'),
   ('explicit saved-source review never invents an original acknowledgement',
    'separation preserves UNKNOWN and later ABA=true without replay'),
   ('explicit saved-source review never invents an original acknowledgement',
    'an original changing during bytes publication blocks the reloaded successor'),
   ('explicit saved-source review never invents an original acknowledgement',
    'an original changing during pointer publication blocks the reloaded successor'),
   ('explicit saved-source review never invents an original acknowledgement',
    'gap history cannot allow a separate creation'),
   ('explicit saved-source review never invents an original acknowledgement',
    'empty history cannot allow a separate creation'),
   ('explicit saved-source review never invents an original acknowledgement',
    'duplicate history cannot allow a separate creation'),
   ('explicit saved-source review never invents an original acknowledgement',
    'a source change between project reads invalidates the review'),
   ('branch initial read recovery keeps actual author decisions',
    'adopts an ordinary unread copy, keeping its exact raw name and older source revision'),
   ('branch initial read recovery keeps actual author decisions',
    'adopts untouched UNKNOWN and its exact retained origin without creating another attempt'),
   ('branch initial read recovery keeps actual author decisions',
    'retains later explicit raw name "" above both edit sequences'),
   ('branch initial read recovery keeps actual author decisions',
    'retains later explicit raw name "  later raw branch  " above both edit sequences'),
   ('branch initial read recovery keeps actual author decisions',
    'retains later explicit raw name "  Authored branch A  " above both edit sequences'),
   ('branch initial read recovery keeps actual author decisions',
    'A to B to A remains a later edit even when equal to the recovered raw name'),
   ('branch initial read recovery keeps actual author decisions',
    'a retained NOT_SENT becomes UNKNOWN in a new writer while its original bytes remain unchanged'),
   ('branch initial read recovery keeps actual author decisions',
    'refuses an incompatible uncertain attempt instead of replacing its authority'),
   ('branch initial read recovery keeps actual author decisions', 'refuses a recovered foreign id'),
   ('branch initial read recovery keeps actual author decisions', 'refuses a recovered foreign generation'),
   ('branch initial read recovery keeps actual author decisions',
    'refuses a recovered foreign active_branch'),
   ('branch initial read recovery keeps actual author decisions',
    'deduplicates exact origins and rejects differing bytes for one origin key'),
   ('branch initial read recovery keeps actual author decisions',
    'refuses edit-sequence overflow without replacing the recovered or local raw input'),
   ('branch initial read recovery keeps actual author decisions',
    'adopted work survives a failed successor backup and later typing before its next backup'),
   ('branch recovery pointer observation',
    'a failed first read remains unknown and can anchor the first successful writer'),
   ('branch recovery pointer observation', 'a previously observed pointer null cannot adopt a replacement'),
   ('branch recovery pointer observation',
    'a previously observed pointer "20000000-0000-4000-8000-000000000001" cannot adopt a replacement'),
   ('branch recovery pointer observation',
    'the first successful null anchors absence rather than granting permission for a later writer'),
   ('branch recovery pointer observation',
    'publication rechecks ownership after recovery read and preserves the original bytes'))),
 ('src/claimSourceDraft.test.ts',
  (('Claim author input, identity and exact original response',
    'isolates project, generation, branch and individual claim buffers'),
   ('Claim author input, identity and exact original response',
    'retains incomplete or inexact source revision ""'),
   ('Claim author input, identity and exact original response',
    'retains incomplete or inexact source revision "1e"'),
   ('Claim author input, identity and exact original response',
    'retains incomplete or inexact source revision "-"'),
   ('Claim author input, identity and exact original response',
    'retains incomplete or inexact source revision "01"'),
   ('Claim author input, identity and exact original response',
    'retains incomplete or inexact source revision "1.5"'),
   ('Claim author input, identity and exact original response',
    'retains incomplete or inexact source revision "9007199254740993"'),
   ('Claim author input, identity and exact original response',
    'keeps inactive source buffers and optional empty versus absent pin distinct'),
   ('Claim author input, identity and exact original response',
    'measures source fields and copies in UTF-8 bytes without truncating raw input'),
   ('Claim author input, identity and exact original response',
    'binds a source-only edit to the complete preimage and same claim ID'),
   ('Claim author input, identity and exact original response',
    'rejects a changed preimage even when the project revision matches'),
   ('Claim author input, identity and exact original response',
    'checks the existing project content lock before arming'),
   ('Claim author input, identity and exact original response',
    'checks the existing claim content lock before arming'),
   ('Claim author input, identity and exact original response',
    'rejects a capture-bound source change without rewriting its claim or capture'),
   ('Claim author input, identity and exact original response',
    'does not acknowledge an observed project with identical submitted values'),
   ('Claim author input, identity and exact original response',
    'keeps later input even after typing B and returning to the original submitted A'),
   ('Claim author input, identity and exact original response',
    'rejects an original ACK with wrong project and keeps UNKNOWN'),
   ('Claim author input, identity and exact original response',
    'rejects an original ACK with wrong generation and keeps UNKNOWN'),
   ('Claim author input, identity and exact original response',
    'rejects an original ACK with wrong branch and keeps UNKNOWN'),
   ('Claim author input, identity and exact original response',
    'rejects an original ACK with wrong revision and keeps UNKNOWN'),
   ('Claim author input, identity and exact original response',
    'rejects an original ACK with wrong target source and keeps UNKNOWN'),
   ('Claim author input, identity and exact original response',
    'rejects an original ACK with wrong deleted other claim and keeps UNKNOWN'),
   ('Claim author input, identity and exact original response',
    'rejects an original ACK with wrong changed other claim and keeps UNKNOWN'),
   ('Claim author input, identity and exact original response',
    'rejects an original ACK with wrong reordered claims and keeps UNKNOWN'),
   ('Claim author input, identity and exact original response', 'rejects a reply for another local attempt'),
   ('Claim author input, identity and exact original response',
    'accepts exactly one appended claim while preserving all previous claim fields'),
   ('Claim recovery custody without replay',
    'preserves corrupt and foreign copies and rejects extra execution authority'),
   ('Claim recovery custody without replay',
    'recovers original incomplete input after a read failure without inventing an edit, UNKNOWN=false'),
   ('Claim recovery custody without replay',
    'recovers original incomplete input after a read failure without inventing an edit, UNKNOWN=true'),
   ('Claim recovery custody without replay',
    'preserves an explicit edit made while recovery is blocked, return-to-initial=false'),
   ('Claim recovery custody without replay',
    'preserves an explicit edit made while recovery is blocked, return-to-initial=true'),
   ('Claim recovery custody without replay',
    'keeps a newer ordinary draft active while retaining the earlier ordinary copy'),
   ('Claim recovery custody without replay',
    'two failed original reads do not become author edits before a successful recovery'),
   ('Claim recovery custody without replay',
    'a recovered UNKNOWN is not a later edit when separate-copy publication must be retried'),
   ('Claim recovery custody without replay',
    'refuses a foreign owner or target while recovering a failed read'),
   ('Claim recovery custody without replay',
    'does not replace an active uncertain attempt with a different recovered attempt'),
   ('Claim recovery custody without replay',
    'first pointer read failure remains unobserved until a later valid writer is read'),
   ('Claim recovery custody without replay',
    'a known writer cannot be replaced after its original data read failed'),
   ('Claim recovery custody without replay',
    'observed null is a custody observation rather than a failed pointer read'),
   ('Claim recovery custody without replay',
    'a repeated pointer read failure cannot erase a prior successful observation'),
   ('Claim recovery custody without replay',
    'pointer replacement after a first recovered observation blocks successor publication'),
   ('Claim recovery custody without replay', 'recovers a NOT_SENT checkpoint conservatively as UNKNOWN'),
   ('Claim recovery custody without replay', 'does not confirm custody after data-silent'),
   ('Claim recovery custody without replay', 'does not confirm custody after data-throw'),
   ('Claim recovery custody without replay', 'does not confirm custody after pointer-silent'),
   ('Claim recovery custody without replay', 'does not confirm custody after pointer-throw'),
   ('Claim recovery custody without replay', 'does not confirm custody after pointer-write-throw'),
   ('Claim recovery custody without replay',
    'detects an interleaved data replacement during pointer publication'),
   ('Claim recovery custody without replay',
    'rechecks custody after budget enumeration and preserves the interleaved writer'),
   ('Claim recovery custody without replay',
    'does not overwrite an earlier saved checkpoint with new author input'),
   ('Claim recovery custody without replay',
    'stable read-history-read on the original revision cannot release a future commit'),
   ('Claim recovery custody without replay',
    'requires complete history and a stable second read without acknowledging equal values'),
   ('Claim recovery custody without replay',
    'retains exact old UNKNOWN bytes when separate-copy phase 1 fails'),
   ('Claim recovery custody without replay',
    'retains exact old UNKNOWN bytes when separate-copy phase 2 fails'),
   ('Claim recovery custody without replay',
    'a confirmed separate edit retains the exact UNKNOWN copy and uses the later explicit base'),
   ('Claim recovery custody without replay',
    'rejects a fresh document with the same stamp but changed claim 0'),
   ('Claim recovery custody without replay',
    'rejects a fresh document with the same stamp but changed claim 1'),
   ('Claim recovery custody without replay',
    'cannot release into an unreviewed same-stamp document with changed claim 0'),
   ('Claim recovery custody without replay',
    'cannot release into an unreviewed same-stamp document with changed claim 1'),
   ('Claim recovery custody without replay',
    'explicitly choosing current values preserves the old draft object and does not dispatch'),
   ('Claim pointer publication after a partial local write',
    'same-draft retry publishes its own pointer after silent, earlier-writer=false'),
   ('Claim pointer publication after a partial local write',
    'same-draft retry publishes its own pointer after throw-before, earlier-writer=false'),
   ('Claim pointer publication after a partial local write',
    'same-draft retry publishes its own pointer after silent, earlier-writer=true'),
   ('Claim pointer publication after a partial local write',
    'same-draft retry publishes its own pointer after throw-before, earlier-writer=true'),
   ('Claim pointer publication after a partial local write',
    'same-draft retry remains blocked while pointer publication still silent fails'),
   ('Claim pointer publication after a partial local write',
    'same-draft retry remains blocked while pointer publication still throw-before fails'),
   ('Claim pointer publication after a partial local write',
    'a setter that writes the new pointer then throws cannot grant the active session publication custody'),
   ('Claim pointer publication after a partial local write',
    'same-draft retry cannot overwrite another writer published after the original pointer failure'),
   ('Claim pointer publication after a partial local write',
    'same-draft pointer repair rechecks its data after publication without overwriting an interleaved '
    'replacement'))))
BRANCH_CLAIM_SOURCE_PATHS = ('apps/studio/src/App.tsx',
 'apps/studio/src/BriefWorkspace.tsx',
 'apps/studio/src/ClaimSourceEditor.tsx',
 'apps/studio/src/HistoryWorkspaces.tsx',
 'apps/studio/src/ReviewWorkspace.tsx',
 'apps/studio/src/api.ts',
 'apps/studio/src/branchDraft.test.ts',
 'apps/studio/src/branchDraft.ts',
 'apps/studio/src/claim-source-editor.css',
 'apps/studio/src/claimSourceDraft.test.ts',
 'apps/studio/src/claimSourceDraft.ts',
 'apps/studio/src/reviewDraft.ts',
 'apps/studio/src/types.ts',
 'apps/studio/src/useBranchDraft.tsx',
 'apps/studio/src/useClaimSourceDraft.ts',
 'apps/studio/tests/branch-authoring-service.spec.ts',
 'apps/studio/tests/claim-source-service.spec.ts',
 'crates/domain/src/claim_source_edit.rs',
 'crates/domain/src/history.rs',
 'crates/domain/src/lib.rs',
 'crates/domain/tests/claim_source_edit.rs',
 'crates/service/tests/claim_source_edit.rs')
CLAIM_SOURCE_RUST_CASES = {'domain': ('source_only_edit_preserves_claim_id_order_references_and_unrelated_content',
            'invalid_preimage_id_noop_source_and_utf8_limits_are_atomic',
            'locks_protect_project_or_target_without_blocking_an_unrelated_claim',
            'wire_requires_complete_preimage_and_replacement_but_accepts_explicit_null',
            'source_change_invalidates_resolved_review_even_when_text_does_not_change',
            'new_review_requires_an_existing_claim_target',
            'immutable_capture_source_and_context_reject_edits_without_rebinding_original_evidence'),
 'service': ('exact_source_edit_reopens_with_same_identity_history_and_original_request_receipt',
             'another_client_revision_conflicts_even_when_the_claim_preimage_is_still_exact',
             'current_revision_does_not_authorize_a_stale_claim_preimage',
             'a_source_only_change_invalidates_resolved_active_branch_reviews_and_retains_their_history',
             'content_locks_reject_project_or_target_edits_without_changing_the_saved_project',
             'explicit_source_withdrawal_is_durable_and_a_rejected_noop_adds_no_content_event',
             'immutable_capture_bindings_survive_direct_service_rejections_and_durable_reopen',
             'invalid_fields_assets_future_sources_and_missing_review_targets_have_no_partial_commit')}
SELECTIONS = (*PROJECT_AUTHORING_SELECTIONS, *CREATIVE_PRODUCTION_SELECTIONS, *BRANCH_CLAIM_SELECTIONS)

# Each per-file process limit includes every declared test timeout and the
# configured 120s server startup. Only an original run establishes actual timing.
SELECTION_GROUPS = {
    "branch-claim": {"labels": ("branch-authoring", "claim-source"), "required_tests": 31},
    "project-authoring": {"labels": tuple(row[0] for row in PROJECT_AUTHORING_SELECTIONS),
                          "required_tests": 136},
    "creative-production": {"labels": ("production-design", "creative-workbench"),
                            "required_tests": 17},
    "complete-authoring": {
        "labels": ("beat-draft", "beat-time", "delivery-recipe", "recipe-authority", "review",
                   "inspector", "profile-metadata", "profile-authority"),
        "required_tests": 65,
    },
    "authoring-drafts": {
        "labels": ("beat-draft", "beat-time", "delivery-recipe", "recipe-authority", "review"),
        "required_tests": 39,
    },
}
SELECTION_TIMEOUTS = {
    "branch-authoring": 2220,
    "claim-source": 1860,
    "production-design": 1140,
    "creative-workbench": 1260,
    "canvas": 1800,
    "ink": 180,
    "program": 600,
    "brief": 900,
    "brief-recovery": 180,
    "brief-uncertain": 900,
    "active-voice": 900,
    "camera": 1200,
    "gesture": 900,
    "narrative": 1200,
    'beat-draft': 1500,
    'beat-time': 660,
    'delivery-recipe': 1260,
    'recipe-authority': 780,
    'review': 1020,
    'inspector': 900,
    'profile-metadata': 1500,
    'profile-authority': 900,
    'projection-navigation': 1260,
}


# Synthetic browser scope is separate from every real-service selection.
BROWSER_SELECTIONS = (('browser-asset-integrity',
  'asset-integrity.spec.ts',
  ('Dependencies audits real CAS identities in pages without inventing scene consumers',
   'browser demo cannot claim local CAS integrity or fabricate Graph associations')),
 ('browser-brand-campaign',
  'brand-campaign.spec.ts',
  ('browser with no canonical BrandProfile cannot claim campaign admission',
   'two-scene source-bound brand rollout is reviewed before one canonical mutation',
   'failed synthetic late CAS never claims a successful campaign revision')),
 ('browser-canvas-native-linear-motion',
  'canvas-native-linear-motion.spec.ts',
  ('Canvas issues exactly one source-scoped native linear move and preserves base pose',
   'native move UI blocks beat-constrained scenes and never claims native render in browser demo')),
 ('browser-canvas-native-outcubic',
  'canvas-native-outcubic.spec.ts',
  ('Canvas writes one atomic cubic-out X/Y move through the saved native bridge',
   'browser demo never pretends exact native Film motion is already proven')),
 ('browser-editor',
  'editor.spec.ts',
  ('editor exposes real workspaces and browser-demo mutations',
   'renderer and locks mutate the same visible project revision',
   'keyboard focus is visible and workspace navigation remains operable',
   'theme preference persists without mutating the project revision',
   'scene selection and timeline seeking share one playhead context',
   'beat and cue selection share scene and playhead without mutating project state',
   'design preview transport steps and plays without claiming a project edit',
   'scene beat locks are enforced while unlocked beats stay synchronized with the timeline',
   'offline state is explicit while local editing remains available',
   'workflow intelligence never fabricates canonical evidence in browser demo',
   'model preflight exposes minimal context without dispatch or silent fallback',
   'canvas edits and stored alternatives use project revisions rather than local-only UI state',
   'scene duration ripple reflows the shared timeline and journals one change',
   'visual system commits a new version and project style lock is enforceable',
   'canvas hierarchy style relations and safe removal are versioned',
   'production jobs workspace never fabricates runtime evidence in browser demo mode',
   'portable project delivery is truthful in browser demo mode',
   'delivery profiles are versioned and filesystem claims remain truthful',
   'delivery variants keep lineage, replan alternate aspect ratios and require explicit crop approval',
   'audio workspace keeps measurement claims honest and versions mix intent',
   'optional renderers stay closed until a reviewed project extension is explicitly enabled',
   'Launchwright handoffs use public refs and exact output digests without remote mutation claims',
   'OpenTimelineIO export stays filesystem-gated and never implies lossless interchange in browser mode',
   'audio cue seeking and canvas share the same global editorial clock',
   'canvas scrubbing and keyframe jumps use scene-relative time without splitting the clock',
   'review point anchors capture the global timeline and seek without making an edit',
   'review time ranges are validated and historical anchors do not imply renewed approval',
   'desktop job receipts reread active jobs and preserve explicit manual refresh for terminal receipts')),
 ('browser-evaluator-experiment',
  'evaluator-experiment.spec.ts',
  ('a browser can compare original data but cannot claim trusted candidate admission',
   're-editing a candidate or selecting a different scene invalidates the previous comparison',
   'an approved owner registration sends one scoped canonical evaluator change, never an executable skill')),
 ('browser-film-preflight',
  'film-preflight.spec.ts',
  ('native semantic Film preflight is read-only and cannot be mistaken for real render',
   'unsupported Film projection prevents native dispatch for unchanged inputs',
   'browser demo cannot invent native Film semantic projection readiness')),
 ('browser-generation-journal',
  'generation-journal.spec.ts',
  ('desktop preflight, intent commit, and exact reservation are separate user actions',
   'late preflight cannot become a confirmation for another working scene',
   'browser demo never simulates provider jobs or spending')),
 ('browser-history-pagination',
  'history-pagination.spec.ts',
  ('recent-first committed history exposes all 260 revisions with bounded older pages',
   'an earlier-history read error keeps the recent page and allows retry')),
 ('browser-multi-segment-readiness',
  'multi-segment-readiness.spec.ts',
  ('Deliver checks multi-segment source readiness with only an owner preview token',
   'Browser demo has no native multi-segment readiness source')),
 ('browser-native-av-master',
  'native-av-master.spec.ts',
  ('desktop AV master is linked only to session-native visual evidence and a measured saved voice',
   'UI refuses to offer native mastering for incompatible measured voice duration',
   'browser demo never claims native AV master authority')),
 ('browser-native-multisegment-av',
  'native-multisegment-av.spec.ts',
  ('desktop multisegment final AV is source-token-bound, measured and export-authorized',
   'desktop multisegment captioned MP4 remains review-only')),
 ('browser-native-original-mix-voice-binding',
  'native-original-mix-voice-binding.spec.ts',
  ('an unbound saved profile blocks audition before permission or a native request',
   'a different saved voice binding remains blocked without a native request',
   'a matching saved voice binding enables local confirmation without dispatch')),
 ('browser-native-original-mix',
  'native-original-mix.spec.ts',
  ('browser-only Audio cannot fabricate a mixed WAV or rights receipt',
   'explicit desktop audition reads a saved recipe and leaves that project revision unchanged')),
 ('browser-native-pixel-contrast',
  'native-pixel-contrast.spec.ts',
  ('browser demo without native source never fabricates visual metrics',
   'exact native PNG and two manually chosen opaque colors measure 21:1, never change the project',
   'tampered synthetic PNG is refused before any contrast metric exists')),
 ('browser-native-preview',
  'native-preview.spec.ts',
  ('sampled native PNG readback uses only a session grant and follows the editorial playhead',
   'invalid synthetic frame bytes display a truthful readback error and a retry')),
 ('browser-native-progressive-preview',
  'native-progressive-preview.spec.ts',
  ('rapid editorial seeks coalesce delayed native reads and settle on exact source frame',)),
 ('browser-native-review-repair',
  'native-review-repair.spec.ts',
  ('a sampled native frame becomes a user-authored scoped repair, never an automatic edit',)),
 ('browser-otio-owner-cut',
  'otio-owner-cut.spec.ts',
  ('browser demo cannot admit a forged cut or mutate the active project',
   'owner reviews explicit native changes and accepts one project transaction',
   'explicit loss confirmation does not permit a tampered OTIO scene extension')),
 ('browser-portable-mp4-integrity',
  'portable-mp4-integrity.spec.ts',
  ('local portable MP4 verification is explicit, read-only and supports an independent hash',
   'browser preview cannot claim to verify local files')),
 ('browser-production-history',
  'production-history.spec.ts',
  ('history enumerates all local jobs beyond recent-window limit with no driver mutation',
   'independent status append invalidates history cursor and requires explicit restart',
   'browser demo cannot synthesize historical jobs or a mutable backend',
   'history keeps colliding job references across provider without duplicate identity rows',
   'history keeps colliding job references across generation without duplicate identity rows',
   'history keeps colliding job references across revision without duplicate identity rows',
   'an original Motion Canvas observation never decorates another provider or historical stamp')),
 ('browser-program-av-monitor',
  'program-av-monitor.spec.ts',
  ('Program AV monitor reads exact source token only for matching scene, profile and revision',
   'browser demo cannot invent final AV monitoring from a semantic project')),
 ('browser-project-mutation',
  'project-mutation.spec.ts',
  ('pending asset import shares the ordinary edit latch and publishes its revision',
   'pending voice import shares the ordinary edit latch and publishes its revision',
   'a pending portable restore cannot race source edits or publish into another revision',
   'New profile: failed native CAS does not select an unsaved delivery profile',
   'Derive selected: failed native CAS does not select an unsaved delivery profile',
   "rejected scene creation keeps the editor's draft open until a confirmed CAS",
   'rejected Canvas node creation never selects an object that was not committed',
   'a successful node request in scene A preserves a later scene B selection',
   'a rejected node request in scene A preserves a later scene B selection',
   'a successful create preserves later selection and text',
   'a successful create preserves later text on the same selected node',
   'a rejected create preserves later selection and text',
   'a rejected create preserves later text on the same selected node',
   'a successful create preserves a later transform on the same selected node',
   'a successful create preserves a later motion value on the same selected node',
   'returning to the original selection still supersedes a delayed create',
   'a successful delayed create selects its saved object when the inspector was untouched',
   'a delayed initial project read cannot roll back a project already published by a mutation')),
 ('browser-render-receipt',
  'render-receipt.spec.ts',
  ('synthetic native receipt remains scene/revision scoped across Deliver and Timeline',)),
 ('browser-reproducible-capture',
  'reproducible-capture.spec.ts',
  ('pinned first-party browser scenario yields a repeatable redacted screenshot linked to a source claim',)),
 ('browser-v01-mlt-cleanup-inventory',
  'v01-mlt-cleanup-inventory.spec.ts',
  ('installed Jobs can inspect MLT creation receipts without driver dispatch or consent',
   'foreign source receipt inventory cannot be displayed as current project',
   'browser preview cannot inspect installed MLT receipts or invent cleanup')),
 ('browser-v01-native-recovery',
  'v01-native-recovery.spec.ts',
  ('v0.1 owner sees explicit UNKNOWN recovery path without any automatic retry',
   'browser-only owner Jobs has no fabricated recovery warning')),
 ('browser-v01-original-native-status',
  'v01-original-native-status.spec.ts',
  ('owner explicitly observes only original native job; no render restart or forged media success',
   'foreign response with fake verified media is rejected, never becomes success',
   'historical, other-provider and browser-only jobs cannot trigger original status read')),
 ('browser-v01-owner-first-render',
  'v01-owner-first-render.spec.ts',
  ('v0.1 first owner render requires exact Film and rechecked CLI without faking a provider',
   'browser demo explicitly rejects owner-native dispatch without any grants')),
 ('browser-command-palette',
  'command-palette.spec.ts',
  ('palette navigates workspaces without mutating the shared project',
   'global shortcut works in focused fields, and Escape restores focus',
   'palette consumes transport keys and executes exactly one frame-step action',
   'keyboard guide, zero results, focus trap, and reopen keep the palette usable',
   'palette view actions can toggle light theme without a content edit',
   'keyboard selection follows visible grouped results rather than hidden search-score order',
   'palette footer and help stay reachable at desktop and compact viewport heights')),
 ('browser-scene-beat-preservation',
  'scene-beat-preservation.spec.ts',
  ('all raw beat fields survive workspace remount and page reload, including incomplete fractions and '
   'exponents',
   "equal saved beats under another id cannot adopt the original scope's raw draft",
   "equal saved beats under another generation cannot adopt the original scope's raw draft",
   "equal saved beats under another active_branch cannot adopt the original scope's raw draft",
   "equal saved beats under another sceneId cannot adopt the original scope's raw draft",
   "equal saved beats under another beatId cannot adopt the original scope's raw draft",
   'the original beat reply preserves later raw edits through remount and a second explicit save',
   'returning to the old objective during a save remains a later decision after the original ACK',
   'matching saved props cannot acknowledge an unresolved original beat request',
   'UNKNOWN keeps submitted and later raw values across reload without replay',
   'a silent copy write failure prevents dispatch and explicit retry recovers the draft',
   'a silent pointer write failure prevents dispatch and explicit retry recovers the draft',
   'a pointer failure leaves a readable orphan which can be explicitly restored after reload',
   'a failed ACK checkpoint retains the armed original and later input without treating reload as '
   'confirmation',
   'a concurrent retime keeps raw text until the author explicitly combines it with saved timing',
   'a shortened scene retains an out-of-range raw draft until explicit comparison and repair',
   'a concurrently removed beat keeps raw input and needs explicit recreation before any command',
   'late typing during the original removal stays recoverable without automatically recreating the beat',
   'Fill timing gap uses a retained UNKNOWN draft if its original creation reply is unavailable',
   'two windows retain separate beat writers and never choose the newest foreign copy on reload',
   'a corrupt own beat copy stays intact and cannot silently adopt a readable foreign copy',
   'a cloned tab inherits a readable draft but forks its writer before independent edits',
   "a late original reply cannot replace a different selected owner's beat draft",
   'a downloaded draft survives complete local recovery failure and imports as editable raw input',
   'import cannot overwrite current unbacked input, and foreign-owner files are rejected',
   'explicit catalog restore keeps clean checkpoint A when the saved beat is B',
   'explicit file restore keeps clean checkpoint A when the saved beat is B',
   'restoring a clean checkpoint retains the prior unsent draft as a separately recoverable copy',
   'automatic reload of a clean checkpoint follows the newer saved beat without creating author intent',
   'a closed recovery catalog exposes every retained copy after storage enumeration changes')))
BROWSER_OMITTED_SERVICE_CASES = (('project-mutation.spec.ts',
  'real asset import already persisted holds the Brief latch until its real reply is published'),
 ('project-mutation.spec.ts',
  'real voice import already persisted holds the Brief latch until its real reply is published'),
 ('project-mutation.spec.ts',
  'real import grants reject missing authority, path/effect/scope/generation mismatches, stale sources and '
  'reopen replay'),
 ('project-mutation.spec.ts',
  'real portable export and verified restore rotate generation, preserve CAS and serialize the next Brief '
  'edit'))
BROWSER_DESCRIBE_TITLES = {('project-mutation.spec.ts', 'a successful create preserves later selection and text'): ('Canvas '
                                                                                          'interaction '
                                                                                          'during delayed '
                                                                                          'creation — '
                                                                                          'synthetic UI '
                                                                                          'contract',),
 ('project-mutation.spec.ts', 'a successful create preserves later text on the same selected node'): ('Canvas '
                                                                                                      'interaction '
                                                                                                      'during '
                                                                                                      'delayed '
                                                                                                      'creation '
                                                                                                      '— '
                                                                                                      'synthetic '
                                                                                                      'UI '
                                                                                                      'contract',),
 ('project-mutation.spec.ts', 'a rejected create preserves later selection and text'): ('Canvas interaction '
                                                                                        'during delayed '
                                                                                        'creation — '
                                                                                        'synthetic UI '
                                                                                        'contract',),
 ('project-mutation.spec.ts', 'a rejected create preserves later text on the same selected node'): ('Canvas '
                                                                                                    'interaction '
                                                                                                    'during '
                                                                                                    'delayed '
                                                                                                    'creation '
                                                                                                    '— '
                                                                                                    'synthetic '
                                                                                                    'UI '
                                                                                                    'contract',),
 ('project-mutation.spec.ts', 'a successful create preserves a later transform on the same selected node'): ('Canvas '
                                                                                                             'interaction '
                                                                                                             'during '
                                                                                                             'delayed '
                                                                                                             'creation '
                                                                                                             '— '
                                                                                                             'synthetic '
                                                                                                             'UI '
                                                                                                             'contract',),
 ('project-mutation.spec.ts', 'a successful create preserves a later motion value on the same selected node'): ('Canvas '
                                                                                                                'interaction '
                                                                                                                'during '
                                                                                                                'delayed '
                                                                                                                'creation '
                                                                                                                '— '
                                                                                                                'synthetic '
                                                                                                                'UI '
                                                                                                                'contract',),
 ('project-mutation.spec.ts', 'returning to the original selection still supersedes a delayed create'): ('Canvas '
                                                                                                         'interaction '
                                                                                                         'during '
                                                                                                         'delayed '
                                                                                                         'creation '
                                                                                                         '— '
                                                                                                         'synthetic '
                                                                                                         'UI '
                                                                                                         'contract',),
 ('project-mutation.spec.ts', 'a successful delayed create selects its saved object when the inspector was untouched'): ('Canvas '
                                                                                                                         'interaction '
                                                                                                                         'during '
                                                                                                                         'delayed '
                                                                                                                         'creation '
                                                                                                                         '— '
                                                                                                                         'synthetic '
                                                                                                                         'UI '
                                                                                                                         'contract',),
 ('project-mutation.spec.ts', 'real asset import already persisted holds the Brief latch until its real reply is published'): ('real '
                                                                                                                               'service '
                                                                                                                               'imports '
                                                                                                                               'and '
                                                                                                                               'portable '
                                                                                                                               'restore',),
 ('project-mutation.spec.ts', 'real voice import already persisted holds the Brief latch until its real reply is published'): ('real '
                                                                                                                               'service '
                                                                                                                               'imports '
                                                                                                                               'and '
                                                                                                                               'portable '
                                                                                                                               'restore',),
 ('project-mutation.spec.ts', 'real import grants reject missing authority, path/effect/scope/generation mismatches, stale sources and reopen replay'): ('real '
                                                                                                                                                         'service '
                                                                                                                                                         'imports '
                                                                                                                                                         'and '
                                                                                                                                                         'portable '
                                                                                                                                                         'restore',),
 ('project-mutation.spec.ts', 'real portable export and verified restore rotate generation, preserve CAS and serialize the next Brief edit'): ('real '
                                                                                                                                               'service '
                                                                                                                                               'imports '
                                                                                                                                               'and '
                                                                                                                                               'portable '
                                                                                                                                               'restore',)}
BROWSER_SOURCE_PATHS = ('apps/studio/src/App.tsx',
 'apps/studio/src/AudioWorkspace.tsx',
 'apps/studio/src/BrandCampaignWorkbench.tsx',
 'apps/studio/src/BrandTasteWorkbench.tsx',
 'apps/studio/src/BriefWorkspace.tsx',
 'apps/studio/src/CameraReframeWorkbench.tsx',
 'apps/studio/src/CanvasStructureEditor.tsx',
 'apps/studio/src/CanvasWorkspace.tsx',
 'apps/studio/src/CaptionReadabilityReview.tsx',
 'apps/studio/src/CaptureSourceWorkbench.tsx',
 'apps/studio/src/CommandPalette.tsx',
 'apps/studio/src/ComponentDistillationWorkbench.tsx',
 'apps/studio/src/CompositionStudy.tsx',
 'apps/studio/src/CreativeAlternativesPanel.tsx',
 'apps/studio/src/CreativeEvaluatorWorkbench.tsx',
 'apps/studio/src/CreativePatchWorkbench.tsx',
 'apps/studio/src/CreativeRuntimeDoctorPanel.tsx',
 'apps/studio/src/CreativeWorkspaces.tsx',
 'apps/studio/src/DeliveryProfiles.tsx',
 'apps/studio/src/DeliveryVariantImpactReview.tsx',
 'apps/studio/src/DependenciesWorkspace.tsx',
 'apps/studio/src/EvaluatorExperimentWorkbench.tsx',
 'apps/studio/src/ExpandedStudy.tsx',
 'apps/studio/src/FileImportRecovery.tsx',
 'apps/studio/src/HistoryWorkspaces.tsx',
 'apps/studio/src/IdentityOriginWorkbench.tsx',
 'apps/studio/src/IntegrationsWorkspace.tsx',
 'apps/studio/src/MeasuredDataWorkbench.tsx',
 'apps/studio/src/ModelContextInspector.tsx',
 'apps/studio/src/MusicClockWorkbench.tsx',
 'apps/studio/src/MusicConformWorkbench.tsx',
 'apps/studio/src/NarrationTakeReview.tsx',
 'apps/studio/src/NarrativeWorkspace.tsx',
 'apps/studio/src/NativeAvReview.tsx',
 'apps/studio/src/NativeContrastWorkbench.tsx',
 'apps/studio/src/NativeFrameStage.tsx',
 'apps/studio/src/NativeInspectionWorkbench.tsx',
 'apps/studio/src/NativeOriginalMixReview.tsx',
 'apps/studio/src/NativePropertyFidelityWorkbench.tsx',
 'apps/studio/src/OtioCutReviewWorkbench.tsx',
 'apps/studio/src/PaidGenerationWorkspace.tsx',
 'apps/studio/src/ProceduralFieldWorkbench.tsx',
 'apps/studio/src/ProductStageWorkbench.tsx',
 'apps/studio/src/ProductionDesignWorkspace.tsx',
 'apps/studio/src/ProductionJobs.tsx',
 'apps/studio/src/ProgramDesignStudy.tsx',
 'apps/studio/src/ProjectLibraryDialog.tsx',
 'apps/studio/src/ProjectProjectionWorkspace.tsx',
 'apps/studio/src/RenderTailWorkbench.tsx',
 'apps/studio/src/ReviewWorkspace.tsx',
 'apps/studio/src/SceneBeatEditor.tsx',
 'apps/studio/src/SceneInspector.tsx',
 'apps/studio/src/SemanticSfxWorkbench.tsx',
 'apps/studio/src/TypographySafetyReview.tsx',
 'apps/studio/src/VariantContinuityReview.tsx',
 'apps/studio/src/VisualLanguageEditor.tsx',
 'apps/studio/src/WorkflowWorkspace.tsx',
 'apps/studio/src/api.ts',
 'apps/studio/src/audioDraft.css',
 'apps/studio/src/audioDraft.ts',
 'apps/studio/src/brand-campaign.css',
 'apps/studio/src/brandCampaign.ts',
 'apps/studio/src/brandGovernance.ts',
 'apps/studio/src/briefDraft.ts',
 'apps/studio/src/cameraReframe.ts',
 'apps/studio/src/cameraReframeDraft.ts',
 'apps/studio/src/canvasCamera.ts',
 'apps/studio/src/canvasInheritedInk.ts',
 'apps/studio/src/canvasMotion.ts',
 'apps/studio/src/canvasWorkspaceDraft.ts',
 'apps/studio/src/caption-readability.css',
 'apps/studio/src/captionReadability.ts',
 'apps/studio/src/captureSource.ts',
 'apps/studio/src/command-palette.css',
 'apps/studio/src/componentDistillation.ts',
 'apps/studio/src/creative-alternatives.css',
 'apps/studio/src/creative-runtime-doctor.css',
 'apps/studio/src/creativeAlternativeEvidence.ts',
 'apps/studio/src/creativeDoctor.ts',
 'apps/studio/src/creativeEvaluator.ts',
 'apps/studio/src/creativeProduction.ts',
 'apps/studio/src/creativeUndo.ts',
 'apps/studio/src/delivery-variant-impact.css',
 'apps/studio/src/deliveryProfileDraft.ts',
 'apps/studio/src/deliveryRecipe.ts',
 'apps/studio/src/deliveryRecipeDraft.ts',
 'apps/studio/src/deliveryVariantImpact.ts',
 'apps/studio/src/editorCommandSearch.ts',
 'apps/studio/src/evaluator-experiment.css',
 'apps/studio/src/evaluatorExperiment.ts',
 'apps/studio/src/file-import.css',
 'apps/studio/src/fileImport.ts',
 'apps/studio/src/firstNativeProduction.ts',
 'apps/studio/src/fixture.ts',
 'apps/studio/src/generationJournalApi.ts',
 'apps/studio/src/historyPaging.ts',
 'apps/studio/src/identityOrigin.ts',
 'apps/studio/src/jobRefresh.ts',
 'apps/studio/src/main.tsx',
 'apps/studio/src/measuredData.ts',
 'apps/studio/src/musicClock.ts',
 'apps/studio/src/narration-review.css',
 'apps/studio/src/narrationReviewApi.ts',
 'apps/studio/src/narrationReviewTypes.ts',
 'apps/studio/src/narrativeDraft.ts',
 'apps/studio/src/native-contrast.css',
 'apps/studio/src/nativeFrameSelection.ts',
 'apps/studio/src/nativeInspection.ts',
 'apps/studio/src/nativeMixTypes.ts',
 'apps/studio/src/nativePixelContrast.ts',
 'apps/studio/src/nativeProgressivePreview.ts',
 'apps/studio/src/nativeRecovery.ts',
 'apps/studio/src/nativeReviewRepair.ts',
 'apps/studio/src/otioCutReview.ts',
 'apps/studio/src/otioCutReviewTestFixtures.ts',
 'apps/studio/src/paidGenerationTypes.ts',
 'apps/studio/src/proceduralField.ts',
 'apps/studio/src/productStage.ts',
 'apps/studio/src/production-design.css',
 'apps/studio/src/programMaster.ts',
 'apps/studio/src/project-library.css',
 'apps/studio/src/projectLibrary.ts',
 'apps/studio/src/projectMutation.ts',
 'apps/studio/src/projectProjection.ts',
 'apps/studio/src/renderReceipt.ts',
 'apps/studio/src/renderTailImpact.ts',
 'apps/studio/src/reviewDraft.ts',
 'apps/studio/src/sceneBeatDraft.ts',
 'apps/studio/src/sceneBeatTiming.ts',
 'apps/studio/src/sceneInspectorDraft.ts',
 'apps/studio/src/scopedChangeDraft.ts',
 'apps/studio/src/semanticSfx.ts',
 'apps/studio/src/stageCapture.ts',
 'apps/studio/src/styles.css',
 'apps/studio/src/timecode.ts',
 'apps/studio/src/types.ts',
 'apps/studio/src/typographySafety.css',
 'apps/studio/src/typographySafety.ts',
 'apps/studio/src/variant-continuity.css',
 'apps/studio/src/variantContinuity.ts',
 'apps/studio/src/visualLanguageDraft.ts',
 'apps/studio/tests/asset-integrity.spec.ts',
 'apps/studio/tests/brand-campaign.spec.ts',
 'apps/studio/tests/canvas-native-linear-motion.spec.ts',
 'apps/studio/tests/canvas-native-outcubic.spec.ts',
 'apps/studio/tests/command-palette.spec.ts',
 'apps/studio/tests/editor.spec.ts',
 'apps/studio/tests/evaluator-experiment.spec.ts',
 'apps/studio/tests/film-preflight.spec.ts',
 'apps/studio/tests/fixtures/scene-beat-authority.tsx',
 'apps/studio/tests/generation-journal.spec.ts',
 'apps/studio/tests/history-pagination.spec.ts',
 'apps/studio/tests/multi-segment-readiness.spec.ts',
 'apps/studio/tests/native-av-master.spec.ts',
 'apps/studio/tests/native-multisegment-av.spec.ts',
 'apps/studio/tests/native-original-mix-voice-binding.spec.ts',
 'apps/studio/tests/native-original-mix.spec.ts',
 'apps/studio/tests/native-pixel-contrast.spec.ts',
 'apps/studio/tests/native-preview.spec.ts',
 'apps/studio/tests/native-progressive-preview.spec.ts',
 'apps/studio/tests/native-review-repair.spec.ts',
 'apps/studio/tests/otio-owner-cut.spec.ts',
 'apps/studio/tests/portable-mp4-integrity.spec.ts',
 'apps/studio/tests/production-history.spec.ts',
 'apps/studio/tests/program-av-monitor.spec.ts',
 'apps/studio/tests/project-mutation.spec.ts',
 'apps/studio/tests/render-receipt.spec.ts',
 'apps/studio/tests/reproducible-capture.spec.ts',
 'apps/studio/tests/scene-beat-preservation.spec.ts',
 'apps/studio/tests/support/scopedChangeService.ts',
 'apps/studio/tests/support/selectedSyntheticProject.ts',
 'apps/studio/tests/support/syntheticProjectReads.ts',
 'apps/studio/tests/v01-mlt-cleanup-inventory.spec.ts',
 'apps/studio/tests/v01-native-recovery.spec.ts',
 'apps/studio/tests/v01-original-native-status.spec.ts',
 'apps/studio/tests/v01-owner-first-render.spec.ts',
 'apps/studio/unit/synthetic-project-reads.test.mjs')
HISTORICAL_BROWSER_SELECTIONS = BROWSER_SELECTIONS
CREATIVE_PRODUCTION_BROWSER_SELECTIONS = (('browser-production-design',
  'production-design-preservation.spec.ts',
  ('Production component raw input survives tool navigation, scene changes, remount and reload without a '
   'save',
   'Production plan preserves exact lists, reviewer, note, shot choices and clock through navigation and '
   'reload',
   'Production component drafts cannot cross id and restore the separate author on return',
   'Production component drafts cannot cross generation and restore the separate author on return',
   'Production component drafts cannot cross active_branch and restore the separate author on return',
   'Production plan drafts cannot cross id and restore the separate author on return',
   'Production plan drafts cannot cross generation and restore the separate author on return',
   'Production plan drafts cannot cross active_branch and restore the separate author on return',
   'a deferred Production concept digest cannot overwrite or send later author input',
   'a Production digest completing after owner does not dispatch an obsolete approval',
   'a Production digest completing after revision does not dispatch an obsolete approval',
   'a Production digest completing after unmount does not dispatch an obsolete approval',
   'an original Production component ACK retains later input and permits its separate second save',
   'an original Production plan ACK retains later input and permits its separate second save',
   'observed matching Production component values do not acknowledge an unknown original result',
   'observed matching Production plan values do not acknowledge an unknown original result',
   'silent Production data recovery failure blocks actual Project dispatch and preserves raw input',
   'silent Production pointer recovery failure blocks actual Project dispatch and preserves raw input',
   'a silent Production copy removal reports unconfirmed discard and keeps the original author bytes '
   'visible',
   'an unreadable own Production copy stays warned while typing; an explicit new copy preserves both '
   'buffers',
   'retrying a readable Production recovery retains its UNKNOWN alongside newer author input')),
 ('browser-creative-workbench',
  'creative-workbench-preservation.spec.ts',
  ('stage raw author input survives navigation, scene return, remount and reload without Project writes',
   'stage author input is isolated by id and restores on return',
   'stage author input is isolated by generation and restores on return',
   'stage author input is isolated by active_branch and restores on return',
   'stage incomplete numeric input remains exact and cannot dispatch a Project mutation',
   'stage a reply without the submitted operation cannot clear UNKNOWN',
   'stage silent recovery data loss blocks dispatch and preserves author input',
   'stage matching observed props and a lost original reply remain UNKNOWN after reload',
   'procedural raw author input survives navigation, scene return, remount and reload without Project '
   'writes',
   'procedural author input is isolated by id and restores on return',
   'procedural author input is isolated by generation and restores on return',
   'procedural author input is isolated by active_branch and restores on return',
   'procedural incomplete numeric input remains exact and cannot dispatch a Project mutation',
   'procedural a reply without the submitted operation cannot clear UNKNOWN',
   'procedural silent recovery data loss blocks dispatch and preserves author input',
   'procedural matching observed props and a lost original reply remain UNKNOWN after reload',
   'measured raw author input survives navigation, scene return, remount and reload without Project writes',
   'measured author input is isolated by id and restores on return',
   'measured author input is isolated by generation and restores on return',
   'measured author input is isolated by active_branch and restores on return',
   'measured incomplete numeric input remains exact and cannot dispatch a Project mutation',
   'measured a reply without the submitted operation cannot clear UNKNOWN',
   'measured silent recovery data loss blocks dispatch and preserves author input',
   'measured matching observed props and a lost original reply remain UNKNOWN after reload',
   'Measured table selection preserves each dataset and binding buffer without writes',
   'Measured binding original ACK preserves later Canvas selection and raw precision',
   'Measured pending file typing cannot publish rows, proof or an obsolete error',
   'Measured pending file asset cannot publish rows, proof or an obsolete error',
   'Measured pending file owner cannot publish rows, proof or an obsolete error',
   'Measured pending file unmount cannot publish rows, proof or an obsolete error',
   'Measured pending file late-error cannot publish rows, proof or an obsolete error',
   'Measured completed byte proof tolerates label edits but is never recovered as submit authority',
   'Measured pending file cannot resurrect a buffer after an explicit saved-value choice',
   'Measured pending file cannot replace an explicitly restored copy with the same edit sequence',
   'Measured new-copy action cannot bypass a repaired UNKNOWN after missing reload',
   'Measured new-copy action cannot bypass a repaired UNKNOWN after corrupt reload',
   'ProductStage existing Hero eligibility blocks dispatch while separate scene drafts and valid '
   'authoring remain intact')))
BROWSER_SELECTIONS = (*HISTORICAL_BROWSER_SELECTIONS, *CREATIVE_PRODUCTION_BROWSER_SELECTIONS)
REVIEW_BROWSER_TITLES = ('causal: raw comment and incomplete range survive remount and reload',
 'causal: raw comment belongs to its exact id across A-B-A',
 'causal: raw comment belongs to its exact generation across A-B-A',
 'causal: raw comment belongs to its exact active_branch across A-B-A',
 "causal: scene targets do not adopt each other's raw review",
 'causal: original add acknowledgement preserves a later author edit',
 'causal: r+1 without the submitted review cannot acknowledge or erase the draft',
 'causal: resolution raw text survives workspace remount and reload',
 'causal: alternative rationale and direction survive workspace remount and reload',
 'causal: uncertain rejection preserves input and blocks automatic or manual replay',
 'causal: denied recovery storage blocks submission and retains current raw input',
 'control: a valid original add response clears only the submitted comment',
 'control: a matching observed Project alone leaves the original author input intact',
 'transition causal: inspected UNKNOWN retains its original copy before a separate later intent',
 'transition causal: inspected UNKNOWN retains its original copy before a separate blank review',
 'an exact resolution reply retains later raw reason through reopen and a distinct save',
 'an original alternative receipt retains a later raw editorial rationale',
 'resolution and alternative reasons remain isolated by generation across A-B-A',
 'scene and whole-project review buffers stay separate when the author changes scope',
 'silent bytes write loss blocks mutation until a verified retry keeps the raw input',
 'silent pointer write loss blocks mutation until a verified retry keeps the raw input',
 'a full recovery catalogue retains prior copies and permits faithful export of blocked raw input',
 'a late original response cannot erase another project or silently clear its original UNKNOWN',
 'a later saved revision requires explicit comparison while retaining its earlier raw copy',
 'native UTF-8 comment limits reject an unsaveable request without truncation or transport',
 'range toggle causal: fraction and incomplete bound survives resource and return',
 'range toggle causal: intentionally empty bounds survives resource and return',
 'range toggle causal: fraction and incomplete bound survives playhead and return',
 'range toggle causal: intentionally empty bounds survives playhead and return')
REVIEW_BROWSER_SELECTIONS = (
    ("browser-review", "review-preservation.spec.ts", REVIEW_BROWSER_TITLES),
)
# Explicit recovery checks remain separate from historical browser and service selections.
WORKFLOW_PREVIEW_UNITS = (('src/workflowActionDraft.test.ts',
  (('Original workflow caller custody \u2014 synthetic DTOs only',
    'keeps caller identity exact and rejects whitespace, controls and UTF-8 overflow'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'decodes the closed request without a branch or effect-grant field in its retained source'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'refuses lossy non-JSON arguments while leaving incomplete author input verbatim'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'retains original action, source, args and raw field sequences before dispatch'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'cannot dispatch until the original pending copy, pointer and final readback are confirmed'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'rejects selection changes after permission was requested without changing original identity'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'does not let a saved or reconstructed invocation token acknowledge an original action'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'binds success to caller request, command, project, generation and original revision'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    "accepts the contract's arbitrary JSON authority value without widening the response shape"),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'requires the native success stage completed while keeping failure stages opaque'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'keeps UNKNOWN for malformed or foreign failures, even with a truthy outcome flag'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'decodes only the pinned SDK error codes, with a boolean outcome flag'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'does not turn absent, unavailable or unchecked receipts into permission to send'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'keeps a mismatched recorded failure UNKNOWN instead of borrowing another receipt'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'preserves a known failure of the original live invocation until an explicit local acknowledgment'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'reload cannot upgrade a prior unconfirmed action using a retained known-failure flag'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'only a local pre-invoke Error with this request id can establish NOT_SENT'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'keeps exact diagnostics instead of coercing structured native failures to Object text'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'acknowledges only the original Promise and preserves later edits field by field'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'retains a malformed success as UNKNOWN with all author input'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'uses a new writer on reload or duplicated tabs and never overwrites the inherited copy'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'fails closed on a missing or corrupt pointed copy and retains independent raw bytes'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'detects silent data writes and repairs only the original attempted copy without another identity'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'detects a silently dropped pointer and data corruption at the final readback'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'does not overwrite a changed copy or pointer and keeps later author text exportable'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'stores incomplete replay JSON and slug drafts separately for every candidate'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'merges imported UNKNOWN without dropping later local fields or executing a request'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'refuses importing a different original UNKNOWN over the current one'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'validates selected copy bytes again before restoring and refuses a changed source'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'rejects extra draft fields and forged native grant data rather than retaining executable '
    'authority'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'checks the one-time grant source independently of its opaque token'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'shows a retained prior success as history while a different live action remains UNKNOWN'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'rejects sequence exhaustion without dropping input'),
   ('Imported workflow UNKNOWN custody',
    'keeps the imported original and later text when the first adoption write is silent'),
   ('Imported workflow UNKNOWN custody',
    'keeps the imported original and later text when the first adoption write is throw'),
   ('Imported workflow UNKNOWN custody',
    'keeps the imported original and later text when the first adoption write is write-then-throw'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'retains UNKNOWN after a known rejection with not_checked and after reload'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'decodes a retained FAILED_KNOWN claim with not_checked as UNKNOWN'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'refuses to close a FAILED_KNOWN claim with not_checked'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'retains UNKNOWN after a known rejection with not_observed and after reload'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'decodes a retained FAILED_KNOWN claim with not_observed as UNKNOWN'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'refuses to close a FAILED_KNOWN claim with not_observed'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'retains UNKNOWN after a known rejection with unavailable and after reload'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'decodes a retained FAILED_KNOWN claim with unavailable as UNKNOWN'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'refuses to close a FAILED_KNOWN claim with unavailable'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'retains UNKNOWN after a known rejection with dispatching and after reload'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'decodes a retained FAILED_KNOWN claim with dispatching as UNKNOWN'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'refuses to close a FAILED_KNOWN claim with dispatching'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'retains UNKNOWN after a known rejection with outcome_unknown and after reload'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'decodes a retained FAILED_KNOWN claim with outcome_unknown as UNKNOWN'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'refuses to close a FAILED_KNOWN claim with outcome_unknown'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'retains UNKNOWN after a known rejection with future-stage and after reload'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'decodes a retained FAILED_KNOWN claim with future-stage as UNKNOWN'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'refuses to close a FAILED_KNOWN claim with future-stage'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'retains UNKNOWN after a known rejection with completed and after reload'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'decodes a retained FAILED_KNOWN claim with completed as UNKNOWN'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'refuses to close a FAILED_KNOWN claim with completed'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'does not trust a failed_known receipt when the invocation itself is uncertain'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'retains the original bytes and blocks a legacy retired failure with an uncertain receipt'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'does not decode or close NOT_SENT when a native failure contradicts that local claim'))),
 ('src/previewRecovery.test.ts',
  (('decoded preview recovery envelope',
    'preserves bytes independently and hashes scope, source revision and metadata as well as PNG'),
   ('decoded preview recovery envelope', 'rejects a different projectId and does not mutate its input'),
   ('decoded preview recovery envelope',
    'rejects a different generation and does not mutate its input'),
   ('decoded preview recovery envelope', 'rejects a different branchId and does not mutate its input'),
   ('decoded preview recovery envelope', 'rejects a different sceneId and does not mutate its input'),
   ('decoded preview recovery envelope', 'rejects a different profileId and does not mutate its input'),
   ('decoded preview recovery envelope', 'rejects future revisions and incomplete source metadata'),
   ('decoded preview recovery envelope', 'rejects changed pixels with an otherwise intact envelope'),
   ('decoded preview recovery envelope', 'rejects metadata corruption in sceneName'),
   ('decoded preview recovery envelope', 'rejects metadata corruption in sourceRevision'),
   ('decoded preview recovery envelope', 'rejects metadata corruption in frameIndex'),
   ('decoded preview recovery envelope', 'rejects metadata corruption in pixelWidth'),
   ('decoded preview recovery envelope', 'rejects metadata corruption in capturedAt'),
   ('decoded preview recovery envelope',
    'rejects authority fields instead of serializing a token or authorized path'),
   ('decoded preview recovery envelope',
    'bounds raw bytes, dimensions, frame count and numeric entry'))),
 ('src/programMaster.test.ts',
  (('source-bound Program AV monitor clock projection',
    'maps current visual and audio masters to both eligible source scenes'),
   ('source-bound Program AV monitor clock projection',
    'rejects stale revisions, changed output profiles, unknown tokens and unrelated renderers'),
   ('source-bound Program AV monitor clock projection',
    'rejects silently reinterpreting a different frame rate, scene cut or master length'),
   ('source-bound Program AV monitor clock projection',
    'maps explicitly selected noncontiguous cuts onto authored project offsets'))))
WORKFLOW_PREVIEW_BROWSER_SELECTIONS = (('browser-workflow-recovery',
  'workflow-action-recovery.spec.ts',
  ('raw recorder/compiler and separate candidate inputs survive navigation and reload without dispatch',
   'the actual API sends the retained caller id and exact original args after a confirmed backup',
   'incomplete replay JSON remains raw and cannot request a grant or invoke a workflow',
   'native structured UNKNOWN diagnostics and original identity survive refresh, export and reload',
   'a not-observed receipt cannot clear UNKNOWN or manufacture a retry route',
   'reload while the reply is lost keeps the same operation with no automatic grant or dispatch',
   'a success for another generation cannot acknowledge the retained action',
   'an old unversioned success DTO remains unconfirmed instead of being treated as current authority',
   'a non-completed receipt cannot acknowledge a live action even when every source identity matches',
   'a known failure can be acknowledged explicitly without sending or losing the draft',
   'a retained known-failure flag on reload does not resolve a previous UNKNOWN automatically',
   'permission rejection before invoke is a local NOT_SENT result with the original request preserved',
   'revision drift during permission prevents dispatch without rewriting the original source stamp',
   'leaving the editor while permission is pending prevents a late dispatch',
   "a late reply settles only its original owner and does not replace another generation's input",
   'author changes made while a compile is pending survive acknowledgment and reload',
   'same-turn duplicate clicks create one original request and one grant',
   'a silently dropped pending backup invokes neither permission nor workflow and keeps raw input '
   'exportable',
   'final-copy corruption after pointer write cannot authorize dispatch or be overwritten by repair',
   'importing an original UNKNOWN into another tab merges input and never dispatches it',
   'a corrupt pointed recovery copy stays exportable and blocks new action identity',
   'an import finishing during an original invocation reports that it was not applied and preserves '
   'custody',
   'plan-only uses the same original caller protocol without a mutation grant',
   'a known rejection with not_checked cannot release the original workflow action',
   'a known rejection with not_observed cannot release the original workflow action',
   'a known rejection with unavailable cannot release the original workflow action',
   'a known rejection with dispatching cannot release the original workflow action',
   'a known rejection with outcome_unknown cannot release the original workflow action',
   'a known rejection with future-stage cannot release the original workflow action',
   'a known rejection with completed cannot release the original workflow action',
   'an imported UNKNOWN survives a silent first adoption write and later author edits',
   'an imported UNKNOWN survives a throw first adoption write and later author edits',
   'an imported UNKNOWN survives a write-then-throw first adoption write and later author edits')),
 ('browser-preview-recovery',
  'preview-recovery.spec.ts',
  ('revision changes retain the actually decoded previous PNG and label it not current',
   'a real browser reload restores the stored Blob without reusing a native token',
   'same-revision reopen remains historical and workspace navigation keeps the copy',
   'a pending native read and concurrent revision change preserve the old PNG through disconnect and '
   'reopen',
   "another id cannot display or delete the old scope's preview",
   "another generation cannot display or delete the old scope's preview",
   "another active_branch cannot display or delete the old scope's preview",
   'profile and scene selection never borrow retained pixels from another scope',
   'a late native response after selection changed never becomes a recovery copy',
   'PNG-shaped bytes that the browser cannot decode are never retained',
   'a quota fault preserves the earlier durable copy and keeps the new image recoverably honest',
   'retrying a failed recovery write confirms the latest decoded frame and preserves a peer',
   'corrupt pixels fail readback, preserve raw rows and restore an earlier valid copy',
   'a damaged own copy stays until explicit removal and a new decoded preview can be retained',
   'a late persistence acknowledgement cannot replace a newer decoded image',
   'a duplicated writer with the same timestamp cannot lose its concurrent immutable copy',
   'the real record bound refuses a new copy without deleting other writers',
   'an explicit AV choice and its in-flight component survive a scene boundary within the same master',
   'an explicit AV choice suspends outside its cut without reads and resumes on return',
   'AV authority id A-to-B-to-A never resurrects a previous explicit choice',
   'AV authority generation A-to-B-to-A never resurrects a previous explicit choice',
   'AV authority active_branch A-to-B-to-A never resurrects a previous explicit choice',
   'AV authority revision A-to-B-to-A never resurrects a previous explicit choice',
   'AV authority profile A-to-B-to-A never resurrects a previous explicit choice',
   'AV authority frameToken A-to-B-to-A never resurrects a previous explicit choice',
   'AV authority exportToken A-to-B-to-A never resurrects a previous explicit choice',
   "a late frame from the former scene never becomes the current scene's recovery copy")))
WORKFLOW_PREVIEW_SOURCE_PATHS = ('Cargo.lock',
 'apps/desktop/src-tauri/Cargo.toml',
 'apps/desktop/src-tauri/src/main.rs',
 'apps/desktop/src-tauri/src/workflow_native.rs',
 'apps/studio/src/App.tsx',
 'apps/studio/src/NativeFrameStage.tsx',
 'apps/studio/src/RetainedPreviewFrame.tsx',
 'apps/studio/src/WorkflowActionRecovery.tsx',
 'apps/studio/src/WorkflowWorkspace.tsx',
 'apps/studio/src/api.ts',
 'apps/studio/src/previewRecovery.test.ts',
 'apps/studio/src/previewRecovery.ts',
 'apps/studio/src/programMaster.test.ts',
 'apps/studio/src/programMaster.ts',
 'apps/studio/src/types.ts',
 'apps/studio/src/usePreviewRecovery.ts',
 'apps/studio/src/workflowActionDraft.test.ts',
 'apps/studio/src/workflowActionDraft.ts',
 'apps/studio/tests/fixtures/preview-recovery.html',
 'apps/studio/tests/fixtures/preview-recovery.tsx',
 'apps/studio/tests/fixtures/workflow-action-recovery.html',
 'apps/studio/tests/fixtures/workflow-action-recovery.tsx',
 'apps/studio/tests/preview-recovery.spec.ts',
 'apps/studio/tests/program-av-monitor.spec.ts',
 'apps/studio/tests/workflow-action-recovery.spec.ts',
 'crates/native/src/production.rs',
 'crates/native/tests/desktop_workflow_route.rs',
 'tooling/tests/test_ci_canvas_service.py')
WORKFLOW_PREVIEW_SOURCE_PATHS += ("apps/desktop/src-tauri/src/effect_grants.rs",
                                  "crates/native/Cargo.toml", "Cargo.toml")
BROWSER_DESCRIBE_TITLES.update({('workflow-action-recovery.spec.ts', 'raw recorder/compiler and separate candidate inputs survive navigation and reload without dispatch'): (),
 ('workflow-action-recovery.spec.ts', 'the actual API sends the retained caller id and exact original args after a confirmed backup'): (),
 ('workflow-action-recovery.spec.ts', 'incomplete replay JSON remains raw and cannot request a grant or invoke a workflow'): (),
 ('workflow-action-recovery.spec.ts', 'native structured UNKNOWN diagnostics and original identity survive refresh, export and reload'): (),
 ('workflow-action-recovery.spec.ts', 'a not-observed receipt cannot clear UNKNOWN or manufacture a retry route'): (),
 ('workflow-action-recovery.spec.ts', 'reload while the reply is lost keeps the same operation with no automatic grant or dispatch'): (),
 ('workflow-action-recovery.spec.ts', 'a success for another generation cannot acknowledge the retained action'): (),
 ('workflow-action-recovery.spec.ts', 'an old unversioned success DTO remains unconfirmed instead of being treated as current authority'): (),
 ('workflow-action-recovery.spec.ts', 'a non-completed receipt cannot acknowledge a live action even when every source identity matches'): (),
 ('workflow-action-recovery.spec.ts', 'a known failure can be acknowledged explicitly without sending or losing the draft'): (),
 ('workflow-action-recovery.spec.ts', 'a retained known-failure flag on reload does not resolve a previous UNKNOWN automatically'): (),
 ('workflow-action-recovery.spec.ts', 'permission rejection before invoke is a local NOT_SENT result with the original request preserved'): (),
 ('workflow-action-recovery.spec.ts', 'revision drift during permission prevents dispatch without rewriting the original source stamp'): (),
 ('workflow-action-recovery.spec.ts', 'leaving the editor while permission is pending prevents a late dispatch'): (),
 ('workflow-action-recovery.spec.ts', "a late reply settles only its original owner and does not replace another generation's input"): (),
 ('workflow-action-recovery.spec.ts', 'author changes made while a compile is pending survive acknowledgment and reload'): (),
 ('workflow-action-recovery.spec.ts', 'same-turn duplicate clicks create one original request and one grant'): (),
 ('workflow-action-recovery.spec.ts', 'a silently dropped pending backup invokes neither permission nor workflow and keeps raw input exportable'): (),
 ('workflow-action-recovery.spec.ts', 'final-copy corruption after pointer write cannot authorize dispatch or be overwritten by repair'): (),
 ('workflow-action-recovery.spec.ts', 'importing an original UNKNOWN into another tab merges input and never dispatches it'): (),
 ('workflow-action-recovery.spec.ts', 'a corrupt pointed recovery copy stays exportable and blocks new action identity'): (),
 ('workflow-action-recovery.spec.ts', 'an import finishing during an original invocation reports that it was not applied and preserves custody'): (),
 ('workflow-action-recovery.spec.ts', 'plan-only uses the same original caller protocol without a mutation grant'): (),
 ('workflow-action-recovery.spec.ts', 'a known rejection with not_checked cannot release the original workflow action'): (),
 ('workflow-action-recovery.spec.ts', 'a known rejection with not_observed cannot release the original workflow action'): (),
 ('workflow-action-recovery.spec.ts', 'a known rejection with unavailable cannot release the original workflow action'): (),
 ('workflow-action-recovery.spec.ts', 'a known rejection with dispatching cannot release the original workflow action'): (),
 ('workflow-action-recovery.spec.ts', 'a known rejection with outcome_unknown cannot release the original workflow action'): (),
 ('workflow-action-recovery.spec.ts', 'a known rejection with future-stage cannot release the original workflow action'): (),
 ('workflow-action-recovery.spec.ts', 'a known rejection with completed cannot release the original workflow action'): (),
 ('workflow-action-recovery.spec.ts', 'an imported UNKNOWN survives a silent first adoption write and later author edits'): (),
 ('workflow-action-recovery.spec.ts', 'an imported UNKNOWN survives a throw first adoption write and later author edits'): (),
 ('workflow-action-recovery.spec.ts', 'an imported UNKNOWN survives a write-then-throw first adoption write and later author edits'): (),
 ('preview-recovery.spec.ts', 'revision changes retain the actually decoded previous PNG and label it not current'): (),
 ('preview-recovery.spec.ts', 'a real browser reload restores the stored Blob without reusing a native token'): (),
 ('preview-recovery.spec.ts', 'same-revision reopen remains historical and workspace navigation keeps the copy'): (),
 ('preview-recovery.spec.ts', 'a pending native read and concurrent revision change preserve the old PNG through disconnect and reopen'): (),
 ('preview-recovery.spec.ts', "another id cannot display or delete the old scope's preview"): (),
 ('preview-recovery.spec.ts', "another generation cannot display or delete the old scope's preview"): (),
 ('preview-recovery.spec.ts', "another active_branch cannot display or delete the old scope's preview"): (),
 ('preview-recovery.spec.ts', 'profile and scene selection never borrow retained pixels from another scope'): (),
 ('preview-recovery.spec.ts', 'a late native response after selection changed never becomes a recovery copy'): (),
 ('preview-recovery.spec.ts', 'PNG-shaped bytes that the browser cannot decode are never retained'): (),
 ('preview-recovery.spec.ts', 'a quota fault preserves the earlier durable copy and keeps the new image recoverably honest'): (),
 ('preview-recovery.spec.ts', 'retrying a failed recovery write confirms the latest decoded frame and preserves a peer'): (),
 ('preview-recovery.spec.ts', 'corrupt pixels fail readback, preserve raw rows and restore an earlier valid copy'): (),
 ('preview-recovery.spec.ts', 'a damaged own copy stays until explicit removal and a new decoded preview can be retained'): (),
 ('preview-recovery.spec.ts', 'a late persistence acknowledgement cannot replace a newer decoded image'): (),
 ('preview-recovery.spec.ts', 'a duplicated writer with the same timestamp cannot lose its concurrent immutable copy'): (),
 ('preview-recovery.spec.ts', 'the real record bound refuses a new copy without deleting other writers'): (),
 ('preview-recovery.spec.ts', 'an explicit AV choice and its in-flight component survive a scene boundary within the same master'): (),
 ('preview-recovery.spec.ts', 'an explicit AV choice suspends outside its cut without reads and resumes on return'): (),
 ('preview-recovery.spec.ts', 'AV authority id A-to-B-to-A never resurrects a previous explicit choice'): (),
 ('preview-recovery.spec.ts', 'AV authority generation A-to-B-to-A never resurrects a previous explicit choice'): (),
 ('preview-recovery.spec.ts', 'AV authority active_branch A-to-B-to-A never resurrects a previous explicit choice'): (),
 ('preview-recovery.spec.ts', 'AV authority revision A-to-B-to-A never resurrects a previous explicit choice'): (),
 ('preview-recovery.spec.ts', 'AV authority profile A-to-B-to-A never resurrects a previous explicit choice'): (),
 ('preview-recovery.spec.ts', 'AV authority frameToken A-to-B-to-A never resurrects a previous explicit choice'): (),
 ('preview-recovery.spec.ts', 'AV authority exportToken A-to-B-to-A never resurrects a previous explicit choice'): (),
 ('preview-recovery.spec.ts', "a late frame from the former scene never becomes the current scene's recovery copy"): (),
 ('program-av-monitor.spec.ts', 'Program AV monitor reads exact source token only for matching scene, profile and revision'): (),
 ('program-av-monitor.spec.ts', 'browser demo cannot invent final AV monitoring from a semantic project'): ()})

BROWSER_SCOPES = {
    "browser-contracts": tuple(row[0] for row in BROWSER_SELECTIONS),
    "creative-production-browser": ("browser-production-design", "browser-creative-workbench"),
    "review-browser": ("browser-review",),
    "workflow-preview-browser": ("browser-program-av-monitor", "browser-workflow-recovery", "browser-preview-recovery"),
}
CREATIVE_PRODUCTION_SOURCE_PATHS = ('apps/studio/src/App.tsx',
 'apps/studio/src/CompositionStudy.tsx',
 'apps/studio/src/MeasuredDataWorkbench.tsx',
 'apps/studio/src/ProceduralFieldWorkbench.tsx',
 'apps/studio/src/ProductStageWorkbench.tsx',
 'apps/studio/src/ProductionDesignWorkspace.tsx',
 'apps/studio/src/api.ts',
 'apps/studio/src/brandGovernance.ts',
 'apps/studio/src/cameraReframe.ts',
 'apps/studio/src/canvasInheritedInk.ts',
 'apps/studio/src/captureSource.ts',
 'apps/studio/src/componentDistillation.ts',
 'apps/studio/src/creativeDoctor.ts',
 'apps/studio/src/creativeEvaluator.ts',
 'apps/studio/src/creativeProduction.ts',
 'apps/studio/src/creativeUndo.ts',
 'apps/studio/src/creativeWorkbenchDraft.test.ts',
 'apps/studio/src/creativeWorkbenchDraft.ts',
 'apps/studio/src/deliveryRecipe.ts',
 'apps/studio/src/deliveryRecipeDraft.ts',
 'apps/studio/src/fileImport.ts',
 'apps/studio/src/fixture.ts',
 'apps/studio/src/generationJournalApi.ts',
 'apps/studio/src/identityOrigin.ts',
 'apps/studio/src/measuredData.ts',
 'apps/studio/src/musicClock.ts',
 'apps/studio/src/narrationReviewApi.ts',
 'apps/studio/src/proceduralField.ts',
 'apps/studio/src/productStage.ts',
 'apps/studio/src/productionDesignDraft.test.ts',
 'apps/studio/src/productionDesignDraft.ts',
 'apps/studio/src/projectLibrary.ts',
 'apps/studio/src/projectMutation.ts',
 'apps/studio/src/semanticSfx.ts',
 'apps/studio/src/stageCapture.ts',
 'apps/studio/src/types.ts',
 'apps/studio/src/useCreativeWorkbenchDraft.tsx',
 'apps/studio/tests/creative-workbench-preservation.spec.ts',
 'apps/studio/tests/creative-workbench-service.spec.ts',
 'apps/studio/tests/fixtures/creative-workbench-authority.html',
 'apps/studio/tests/fixtures/creative-workbench-authority.tsx',
 'apps/studio/tests/fixtures/production-design-authority.html',
 'apps/studio/tests/fixtures/production-design-authority.tsx',
 'apps/studio/tests/production-design-preservation.spec.ts',
 'apps/studio/tests/production-design-service.spec.ts',
 'apps/studio/tests/support/scopedChangeService.ts')

BROWSER_SELECTION_TIMEOUTS = {label: 180 + 30 * len(titles) for label, _, titles in BROWSER_SELECTIONS}
# Retain the Workbench budget, including startup and every per-case bound.
BROWSER_SELECTION_TIMEOUTS["browser-creative-workbench"] = 1260
BROWSER_SELECTION_TIMEOUTS["browser-review"] = 1050
BROWSER_SELECTION_TIMEOUTS.update({label: 180 + 30 * len(titles)
                                   for label, _, titles in WORKFLOW_PREVIEW_BROWSER_SELECTIONS})


CREATIVE_PRODUCTION_UNITS = (('src/productionDesignDraft.test.ts',
  (('Production author input and original response authority',
    'scopes component and plan input by project, generation, branch and component scene'),
   ('Production author input and original response authority',
    'retains exact plan text, empty lines, reviewer, source references and clock without cleaning author '
    'input'),
   ('Production author input and original response authority',
    'an observed identical hero value at r+1 never consumes its pending request'),
   ('Production author input and original response authority',
    'an observed identical plan value at r+1 never consumes its pending request'),
   ('Production author input and original response authority',
    'the original hero reply preserves later raw input including a return to the old value'),
   ('Production author input and original response authority',
    'the original plan reply preserves later raw input including a return to the old value'),
   ('Production author input and original response authority',
    'rejects an original response with the wrong id without consuming raw input'),
   ('Production author input and original response authority',
    'rejects an original response with the wrong generation without consuming raw input'),
   ('Production author input and original response authority',
    'rejects an original response with the wrong active_branch without consuming raw input'),
   ('Production author input and original response authority',
    'rejects an original response with the wrong revision without consuming raw input'),
   ('Production author input and original response authority',
    'rejects an original response with the wrong value without consuming raw input'),
   ('Production author input and original response authority',
    'treats an unrelated observed revision as a reviewable conflict rather than silently rebasing authored '
    'intent'),
   ('Production author input and original response authority',
    'invalidates a prepared digest when input, owner, revision, pending attempt, or source target changes'),
   ('Production author input and original response authority',
    'cleans only a plan submission and binds approval to that exact content while retaining author bytes'),
   ('Production author input and original response authority',
    'keeps editable scene objects on detach and rejects a receipt that changes them'),
   ('Production author input and original response authority',
    'will not dispatch plan or component submissions through content locks'),
   ('Production durable custody',
    'recovers NOT_SENT conservatively as UNKNOWN and exports complete raw input without write authority'),
   ('Production durable custody',
    'rejects foreign scope, writer and authority fields without altering any stored bytes'),
   ('Production durable custody',
    'requires confirmed data storage before treating a Production backup as durable'),
   ('Production durable custody',
    'requires confirmed pointer storage before treating a Production backup as durable'),
   ('Production durable custody',
    'rechecks data custody after budget enumeration before replacing any author bytes'),
   ('Production durable custody',
    'rechecks pointer custody after budget enumeration before replacing any author bytes'),
   ('Production durable custody',
    'rereads the data after pointer publication and never adopts the interleaved replacement'),
   ('Production durable custody', 'retains corrupt copies and refuses to mutate a clean checkpoint'),
   ('Production durable custody',
    "refuses a full recovery budget without deleting older copies or the live author's input"),
   ('Production UNKNOWN review without inferred acknowledgement or replay',
    'reads two current snapshots and complete history; matching values remain UNKNOWN until explicit '
    'separate intent'),
   ('Production UNKNOWN review without inferred acknowledgement or replay',
    'rejects truncated history and a project that changes during inspection'),
   ('Production UNKNOWN review without inferred acknowledgement or replay',
    'retains the old UNKNOWN through a verified two-phase new writer before releasing a separate intent'),
   ('Production UNKNOWN review without inferred acknowledgement or replay',
    'a failed second fork phase leaves a recoverable UNKNOWN and the earlier author bytes intact'))),
 ('src/creativeWorkbenchDraft.test.ts',
  (('creative workbench raw input and original response authority',
    'keeps raw integer grammar distinct from numeric coercion'),
   ('creative workbench raw input and original response authority',
    'scopes all workbenches by project, generation, branch, scene and editor kind'),
   ('creative workbench raw input and original response authority',
    'stage observed matching values never acknowledge an original pending attempt'),
   ('creative workbench raw input and original response authority',
    'procedural observed matching values never acknowledge an original pending attempt'),
   ('creative workbench raw input and original response authority',
    'measured observed matching values never acknowledge an original pending attempt'),
   ('creative workbench raw input and original response authority',
    'stage the original ACK preserves later raw input and independent table buffers'),
   ('creative workbench raw input and original response authority',
    'procedural the original ACK preserves later raw input and independent table buffers'),
   ('creative workbench raw input and original response authority',
    'measured the original ACK preserves later raw input and independent table buffers'),
   ('creative workbench raw input and original response authority',
    'stage raw copy roundtrip does not carry execution grants or byte verification'),
   ('creative workbench raw input and original response authority',
    'procedural raw copy roundtrip does not carry execution grants or byte verification'),
   ('creative workbench raw input and original response authority',
    'measured raw copy roundtrip does not carry execution grants or byte verification'),
   ('creative workbench raw input and original response authority',
    'rejects the original ACK with wrong id without consuming UNKNOWN'),
   ('creative workbench raw input and original response authority',
    'rejects the original ACK with wrong generation without consuming UNKNOWN'),
   ('creative workbench raw input and original response authority',
    'rejects the original ACK with wrong active_branch without consuming UNKNOWN'),
   ('creative workbench raw input and original response authority',
    'rejects the original ACK with wrong revision without consuming UNKNOWN'),
   ('creative workbench raw input and original response authority',
    'rejects the original ACK with wrong value without consuming UNKNOWN'),
   ('creative workbench raw input and original response authority',
    'stage detach requires the original receipt to preserve every editable node'),
   ('creative workbench raw input and original response authority',
    'procedural detach requires the original receipt to preserve every editable node'),
   ('creative workbench raw input and original response authority',
    'an external revision needs an explicit base choice and never clears pending intent'),
   ('creative workbench raw input and original response authority',
    'invalidates asynchronous preparation when owner, revision, input or attempt changes'),
   ('creative workbench raw input and original response authority',
    'Measured keeps exact independent table, node, precision and axis buffers when selecting another '
    'table'),
   ('creative workbench raw input and original response authority',
    'Measured ACK for one table cannot mark a different hidden author buffer as saved'),
   ('creative workbench raw input and original response authority',
    'Measured reviewed new alias cannot overwrite a separately authored buffer at its saved table ID'),
   ('creative workbench raw input and original response authority',
    'Measured exact byte proof is ephemeral, tolerates label changes and rejects another source or '
    'recovered authority'),
   ('creative workbench raw input and original response authority',
    'Measured binding ACK checks the original table, datum, view and projected Canvas value'),
   ('creative workbench verified custody and uncertain outcome review',
    'requires verified data readback before a recovery write succeeds'),
   ('creative workbench verified custody and uncertain outcome review',
    'requires verified pointer readback before a recovery write succeeds'),
   ('creative workbench verified custody and uncertain outcome review',
    'rechecks data custody after the recovery budget scan'),
   ('creative workbench verified custody and uncertain outcome review',
    'rechecks pointer custody after the recovery budget scan'),
   ('creative workbench verified custody and uncertain outcome review',
    'rereads data after pointer publication and rejects an interleaved replacement'),
   ('creative workbench verified custody and uncertain outcome review',
    'preserves corrupt bytes, rejects foreign scope and refuses clean checkpoint replacement'),
   ('creative workbench verified custody and uncertain outcome review',
    'a full recovery budget blocks saving without deleting any previous author bytes'),
   ('creative workbench verified custody and uncertain outcome review',
    'recovering a NOT_SENT checkpoint conservatively retains UNKNOWN and rejects extra authority fields'),
   ('creative workbench verified custody and uncertain outcome review',
    'requires fresh read-history-read and retains matching values as comparison rather than ACK'),
   ('creative workbench verified custody and uncertain outcome review',
    'rejects incomplete history and a project changed during outcome inspection'),
   ('creative workbench verified custody and uncertain outcome review',
    'a separate reviewed intent verifies both records and preserves the exact earlier UNKNOWN'),
   ('ProductStage existing project eligibility before mutation dispatch',
    'rejects an existing Hero collision before arming an attempt and retains the exact author input'),
   ('ProductStage existing project eligibility before mutation dispatch',
    'rejects creation at the existing sixteen-Stage limit while a bound Stage can still update'))))


REVIEW_UNITS = (('src/reviewDraft.test.ts',
  (('exact review owner and raw author input', 'separates equal raw drafts under a different id'),
   ('exact review owner and raw author input',
    'separates equal raw drafts under a different generation'),
   ('exact review owner and raw author input',
    'separates equal raw drafts under a different active_branch'),
   ('exact review owner and raw author input',
    'separates comment, resolution and alternative targets'),
   ('exact review owner and raw author input',
    'preserves whitespace and incomplete input without numeric coercion'),
   ('exact review owner and raw author input', 'uses exact fractions for an authored time range'),
   ('exact review owner and raw author input',
    'incomplete or invalid time "1e" remains unsaveable and unchanged'),
   ('exact review owner and raw author input',
    'incomplete or invalid time "1001/" remains unsaveable and unchanged'),
   ('exact review owner and raw author input',
    'incomplete or invalid time "" remains unsaveable and unchanged'),
   ('exact review owner and raw author input',
    'incomplete or invalid time "0x1" remains unsaveable and unchanged'),
   ('exact review owner and raw author input',
    'incomplete or invalid time "Infinity" remains unsaveable and unchanged'),
   ('exact review owner and raw author input', 'range -1 to 1 cannot leave its selected scene'),
   ('exact review owner and raw author input', 'range 2 to 1 cannot leave its selected scene'),
   ('exact review owner and raw author input', 'range 0 to 8 cannot leave its selected scene'),
   ('exact review owner and raw author input',
    'clean observations update only the base while active raw intent retains its source'),
   ('original callback acknowledgement and uncertainty',
    'a valid original add receipt clears only its unchanged submitted comment'),
   ('original callback acknowledgement and uncertainty',
    'an equal observed Project cannot acknowledge or clear the original pending input'),
   ('original callback acknowledgement and uncertainty',
    'an unrelated callback identity cannot acknowledge this original request'),
   ('original callback acknowledgement and uncertainty',
    'a later body edit survives the exact original acknowledgement'),
   ('original callback acknowledgement and uncertainty',
    'a later rangeStart edit survives the exact original acknowledgement'),
   ('original callback acknowledgement and uncertainty',
    'an original reply for another id retains UNKNOWN'),
   ('original callback acknowledgement and uncertainty',
    'an original reply for another generation retains UNKNOWN'),
   ('original callback acknowledgement and uncertainty',
    'an original reply for another active_branch retains UNKNOWN'),
   ('original callback acknowledgement and uncertainty',
    'revision offset 0 cannot replace the exact original receipt'),
   ('original callback acknowledgement and uncertainty',
    'revision offset 2 cannot replace the exact original receipt'),
   ('original callback acknowledgement and uncertainty',
    'a missing review mismatch cannot authorize clearing'),
   ('original callback acknowledgement and uncertainty',
    'a body review mismatch cannot authorize clearing'),
   ('original callback acknowledgement and uncertainty',
    'a anchor review mismatch cannot authorize clearing'),
   ('original callback acknowledgement and uncertainty',
    'a duplicate review mismatch cannot authorize clearing'),
   ('original callback acknowledgement and uncertainty',
    'uncertain failure and reload preserve the original attempt and prohibit replay'),
   ('original callback acknowledgement and uncertainty',
    'a proven unsent storage failure can be submitted only through a new explicit attempt'),
   ('original callback acknowledgement and uncertainty',
    'resolution acknowledgement checks the exact item, anchor and authored reason'),
   ('original callback acknowledgement and uncertainty',
    'a later resolution survives both resolution and explicit reopen receipts'),
   ('original callback acknowledgement and uncertainty',
    'an authored draft cannot silently move to a later source or missing scene'),
   ('original callback acknowledgement and uncertainty',
    'the request must preserve the exact raw range and resource binding'),
   ('verified recovery custody and bounded storage',
    'a write is accepted only with exact bytes and a matching tab pointer readback'),
   ('verified recovery custody and bounded storage',
    'a cloned tab forks its writer and never rewrites the earlier copy'),
   ('verified recovery custody and bounded storage',
    'a peer byte change is preserved and blocks an overwrite'),
   ('verified recovery custody and bounded storage',
    'a peer pointer change is preserved and blocks an overwrite'),
   ('verified recovery custody and bounded storage',
    'silent write loss cannot count as confirmed custody'),
   ('verified recovery custody and bounded storage',
    'silent pointer loss keeps the written copy but cannot acknowledge custody'),
   ('verified recovery custody and bounded storage',
    'the copy-count budget blocks a new write without eviction or author loss'),
   ('verified recovery custody and bounded storage',
    'the byte budget blocks a new write without rewriting earlier recovery'),
   ('verified recovery custody and bounded storage',
    'oversized raw input remains in memory when a recovery record cannot be encoded'),
   ('verified recovery custody and bounded storage',
    'a clean acknowledged checkpoint cannot be rewritten by a later intent'),
   ('verified recovery custody and bounded storage',
    'a corrupt or foreign schema recovery is rejected without normalization'),
   ('verified recovery custody and bounded storage',
    'a corrupt or foreign scope recovery is rejected without normalization'),
   ('verified recovery custody and bounded storage',
    'a corrupt or foreign writer recovery is rejected without normalization'),
   ('verified recovery custody and bounded storage',
    'a corrupt or foreign shape recovery is rejected without normalization'),
   ('verified recovery custody and bounded storage',
    'a corrupt or foreign attempt recovery is rejected without normalization'),
   ('explicit UNKNOWN inspection and separate author intent',
    'two exact source reads and contiguous history are observations, never original '
    'acknowledgement'),
   ('explicit UNKNOWN inspection and separate author intent',
    'unchanged submitted text starts blank after explicit inspection rather than replaying '
    'add_review'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a later raw intent uses a new attempt identity without consuming or changing the original '
    'UNKNOWN'),
   ('explicit UNKNOWN inspection and separate author intent',
    'later ABA body input survives explicit UNKNOWN separation'),
   ('explicit UNKNOWN inspection and separate author intent',
    'later ABA kind input survives explicit UNKNOWN separation'),
   ('explicit UNKNOWN inspection and separate author intent',
    'later ABA timeMode input survives explicit UNKNOWN separation'),
   ('explicit UNKNOWN inspection and separate author intent',
    'later ABA rangeStart input survives explicit UNKNOWN separation'),
   ('explicit UNKNOWN inspection and separate author intent',
    'later ABA rangeEnd input survives explicit UNKNOWN separation'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a later ABA resolution reason survives separate intent after actual-source inspection'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a gap journal cannot release UNKNOWN'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a empty journal cannot release UNKNOWN'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a duplicate journal cannot release UNKNOWN'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a beyond journal cannot release UNKNOWN'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a changed second source read invalidates the inspection'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a foreign identity or a different pending attempt cannot reuse the inspection'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a zero-event inspection of unchanged saved state still retains UNKNOWN'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a separate writer retains the original exact UNKNOWN bytes and verifies the new pointer'),
   ('explicit UNKNOWN inspection and separate author intent',
    'failure after staging the new writer leaves an UNKNOWN checkpoint and preserves every '
    'original byte'),
   ('explicit UNKNOWN inspection and separate author intent',
    'verifying identical retained input never rewrites its original timestamp or bytes'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a known unsent draft can be explicitly reviewed against a newer source without weakening '
    'UNKNOWN'),
   ('explicit UNKNOWN inspection and separate author intent',
    'native UTF-8 text budgets are checked before invoking a request without truncating raw input'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a dismissed review cannot become a submitted resolution attempt'))),)


def selected_unit_inventory(selection):
    require(selection in ("creative-production", "review", "workflow-preview", "branch-claim"), "unknown selected unit scope")
    rows, expected_files, expected_tests = {
        "creative-production": (CREATIVE_PRODUCTION_UNITS, 2, 67),
        "review": (REVIEW_UNITS, 1, 72),
        "workflow-preview": (WORKFLOW_PREVIEW_UNITS, 3, 79),
        "branch-claim": (BRANCH_CLAIM_UNITS, 2, 134),
    }[selection]
    require(isinstance(rows, tuple) and len(rows) == expected_files
            and len({name for name, _ in rows}) == expected_files
            and sum(len(titles) for _, titles in rows) == expected_tests,
            "selected unit inventory differs")
    require(all(isinstance(titles, tuple) and titles and len(titles) == len(set(titles))
                and all(isinstance(pair, tuple) and len(pair) == 2
                        and all(isinstance(part, str) and part for part in pair)
                        for pair in titles)
                for _, titles in rows), "selected unit identities differ")
    return rows


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def is_browser_scope(selection):
    return isinstance(selection, str) and selection in BROWSER_SCOPES


def selected_selections(selection):
    """Return a closed scope; an explicit subset never claims the full roster."""
    require(isinstance(selection, str), "selection must be a single named scope")
    if is_browser_scope(selection):
        return browser_selections(selection)
    require(SELECTIONS == (*PROJECT_AUTHORING_SELECTIONS, *CREATIVE_PRODUCTION_SELECTIONS, *BRANCH_CLAIM_SELECTIONS)
            and len(PROJECT_AUTHORING_SELECTIONS) == 19
            and sum(len(row[2]) for row in PROJECT_AUTHORING_SELECTIONS) == 136
            and len(HISTORICAL_PROJECT_AUTHORING_SELECTIONS) == 19
            and sum(len(row[2]) for row in HISTORICAL_PROJECT_AUTHORING_SELECTIONS) == 135
            and len(CREATIVE_PRODUCTION_SELECTIONS) == 2
            and sum(len(row[2]) for row in CREATIVE_PRODUCTION_SELECTIONS) == 17
            and len(BRANCH_CLAIM_SELECTIONS) == 2
            and tuple((label, filename, len(titles)) for label, filename, titles in BRANCH_CLAIM_SELECTIONS) == (
                ("branch-authoring", "branch-authoring-service.spec.ts", 17),
                ("claim-source", "claim-source-service.spec.ts", 14))
            and all(isinstance(titles, tuple) and len(set(titles)) == len(titles)
                    and all(isinstance(title, str) and title for title in titles)
                    for _, _, titles in BRANCH_CLAIM_SELECTIONS),
            "service catalogue differs from its closed historical and composed scopes")
    if selection == "all":
        return SELECTIONS
    if selection in SELECTION_GROUPS:
        group = SELECTION_GROUPS[selection]
        labels, expected = group.get("labels"), group.get("required_tests")
        require(isinstance(labels, tuple) and labels and all(isinstance(x, str) for x in labels),
                "group must declare a nonempty tuple of file selections")
        require(len(set(labels)) == len(labels), "group repeats a file selection")
        require(type(expected) is int and expected > 0, "group must declare its exact case count")
        chosen = tuple(row for row in SELECTIONS if row[0] in labels)
        require(len(chosen) == len(labels), "group contains an unknown or duplicate file selection")
        require(sum(len(titles) for _, _, titles in chosen) == expected,
                "group case count differs from its declared closed scope")
        return chosen
    chosen = tuple(row for row in SELECTIONS if row[0] == selection)
    require(len(chosen) == 1, "unknown or empty service selection")
    return chosen


def selection_plan(selection):
    chosen = selected_selections(selection)
    labels = {row[0] for row in chosen}
    if is_browser_scope(selection):
        selected, excluded = browser_discovery_inventory(selection)
        return {
            "requested": selection, "scope": "EXPLICIT_SYNTHETIC_BROWSER_CONTRACTS",
            "required": [{"label": label, "file": filename, "titles": list(titles)}
                         for label, filename, titles in chosen],
            "not_selected": [{"label": label, "file": filename, "titles": list(titles),
                             "status": "NOT_SELECTED", "technical_credit": "NONE"}
                             for label, filename, titles in (*SELECTIONS, *BROWSER_SELECTIONS, *REVIEW_BROWSER_SELECTIONS,
                                                            *WORKFLOW_PREVIEW_BROWSER_SELECTIONS)
                             if label not in labels],
            "excluded_service_cases": [{"file": filename, "title": title,
                                       "status": "NOT_SELECTED", "technical_credit": "NONE"}
                                      for filename, title in BROWSER_OMITTED_SERVICE_CASES
                                      if (filename, title) in excluded],
            "required_tests": len(selected), "discovery_tests": len(selected | excluded),
            "full_default_tests": sum(len(titles) for _, _, titles in SELECTIONS),
            "formal_acceptance": False,
        }
    return {
        "requested": selection,
        "scope": "ALL_DEFINED_SERVICE_CASES" if selection == "all" else "EXPLICIT_SELECTED_SERVICE_CASES",
        "required": [{"label": label, "file": filename, "titles": list(titles)}
                     for label, filename, titles in chosen],
        "not_selected": [{"label": label, "file": filename, "titles": list(titles),
                          "status": "NOT_SELECTED", "technical_credit": "NONE"}
                         for label, filename, titles in SELECTIONS if label not in labels],
        "required_tests": sum(len(titles) for _, _, titles in chosen),
        "full_default_tests": sum(len(titles) for _, _, titles in SELECTIONS),
        "formal_acceptance": False,
    }


def validate_report(document, filename, titles):
    """Reject empty, omitted, repeated, retried or expected-failure selections."""
    require(isinstance(document, dict), "report must be an object")
    require(document.get("errors") == [], "report has missing or nonempty global errors")
    stats = document.get("stats", {})
    require(stats.get("expected") == len(titles), "selected success count differs")
    for name in ("skipped", "unexpected", "flaky"):
        require(stats.get(name) == 0, "selected " + name + " count is not zero")
    found = []

    def visit(suites):
        require(isinstance(suites, list), "suite list is missing")
        for suite in suites:
            require(isinstance(suite, dict), "suite must be an object")
            for spec in suite.get("specs", []):
                require(spec.get("file") == filename, "unexpected test file")
                require(spec.get("ok") is True, "test specification did not pass")
                title = spec.get("title")
                require(title in titles and title not in found, "unknown or duplicate test title")
                found.append(title)
                tests = spec.get("tests", [])
                require(len(tests) == 1, "each selected case must execute once")
                test = tests[0]
                require(test.get("projectId") == "chromium", "unexpected browser project")
                require(test.get("expectedStatus") == "passed", "expected failure or skip is forbidden")
                require(test.get("status") == "expected", "test is not an ordinary pass")
                results = test.get("results", [])
                require(len(results) == 1, "retry or missing original result")
                result = results[0]
                require(result.get("status") == "passed" and result.get("retry") == 0,
                        "original result must pass without retry")
                require(not result.get("error") and not result.get("errors"),
                        "original result contains an error")
            visit(suite.get("suites", []))

    visit(document.get("suites"))
    require(set(found) == set(titles) and len(found) == len(titles), "selected inventory differs")
    return {"passed": len(found), "skipped": 0, "retries": 0, "titles": found}


def digest(path):
    require(path.is_file() and not path.is_symlink(), "regular file required: " + str(path))
    with path.open("rb") as stream:
        value = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"bytes": path.stat().st_size, "sha256": value}


def write_new(path, value):
    body = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with path.open("xb") as stream:
        require(stream.write(body) == len(body), "short evidence write")
        stream.flush()
        os.fsync(stream.fileno())


def source_observation(repo, selection=None):
    require(selection is None or is_browser_scope(selection), "unknown source observation scope")
    def git(*arguments):
        return subprocess.check_output(["git", *arguments], cwd=repo, timeout=30).decode().strip()

    head = git("rev-parse", "HEAD")
    require(re.fullmatch("[0-9a-f]{40}", head) is not None, "invalid checkout SHA")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        require(head == os.environ.get("GITHUB_SHA"), "checkout differs from the selected CI source")
    require(git("diff", "--name-only", "HEAD", "--") == "", "tracked source has changed")
    paths = (
        ".github/workflows/ci.yml", ".github/workflows/studio-service-verification.yml",
        "Cargo.lock", "apps/studio/package-lock.json",
        "crates/service/examples/studio_review_session.rs",
        "apps/studio/playwright.config.ts",
        "apps/studio/tests/support/scopedChangeService.ts",
        "apps/studio/tests/canvas-draft-service.spec.ts",
        "apps/studio/tests/visual-language-draft-service.spec.ts",
        "apps/studio/tests/program-design-service.spec.ts",
        "apps/studio/tests/brief-service.spec.ts",
        "apps/studio/tests/brief-recovery-admission-service.spec.ts",
        "apps/studio/tests/brief-uncertain-outcome-service.spec.ts",
        "apps/studio/src/App.tsx",
        "apps/studio/src/CreativeWorkspaces.tsx",
        "apps/studio/src/NarrativeWorkspace.tsx",
        "apps/studio/src/narrativeDraft.ts",
        "apps/studio/tests/narrative-service.spec.ts",
        "apps/studio/src/BriefWorkspace.tsx",
        "apps/studio/src/briefDraft.ts",
        "apps/studio/src/AudioWorkspace.tsx",
        "apps/studio/src/audioDraft.ts",
        "apps/studio/tests/audio-active-voice-service.spec.ts",
        "apps/studio/src/ProductionDesignWorkspace.tsx",
        "apps/studio/src/CameraReframeWorkbench.tsx",
        "apps/studio/src/cameraReframeDraft.ts",
        "apps/studio/src/cameraReframe.ts",
        "apps/studio/src/projectMutation.ts",
        "apps/studio/src/CanvasWorkspace.tsx",
        "apps/studio/src/canvasCamera.ts",
        "apps/studio/tests/camera-reframe-service.spec.ts",
        "apps/studio/tests/canvas-gesture-service.spec.ts",
        "apps/studio/src/CreativeAlternativesPanel.tsx",
        "apps/studio/src/DeliveryProfiles.tsx",
        "apps/studio/src/HistoryWorkspaces.tsx",
        "apps/studio/src/NativeOriginalMixReview.tsx",
        "apps/studio/src/ReviewWorkspace.tsx",
        "apps/studio/src/SceneBeatEditor.tsx",
        "apps/studio/src/audioNarrationProtectedMarkup.test.ts",
        "apps/studio/src/deliveryRecipe.test.ts",
        "apps/studio/src/deliveryRecipe.ts",
        "apps/studio/src/deliveryRecipeDraft.test.ts",
        "apps/studio/src/deliveryRecipeDraft.ts",
        "apps/studio/src/deliveryRecipeRecoveryReview.test.ts",
        "apps/studio/src/reviewDraft.test.ts",
        "apps/studio/src/reviewDraft.ts",
        "apps/studio/src/sceneBeatDraft.test.ts",
        "apps/studio/src/sceneBeatDraft.ts",
        "apps/studio/src/sceneBeatTiming.test.ts",
        "apps/studio/src/sceneBeatTiming.ts",
        "apps/studio/tests/delivery-recipe-authority-service.spec.ts",
        "apps/studio/tests/delivery-recipe-authority.spec.ts",
        "apps/studio/tests/delivery-recipe-recovery-review.spec.ts",
        "apps/studio/tests/delivery-recipe.spec.ts",
        "apps/studio/tests/fixtures/delivery-recipe-authority.html",
        "apps/studio/tests/fixtures/delivery-recipe-authority.tsx",
        "apps/studio/tests/fixtures/review-authority.html",
        "apps/studio/tests/fixtures/review-authority.tsx",
        "apps/studio/tests/fixtures/scene-beat-authority.html",
        "apps/studio/tests/fixtures/scene-beat-authority.tsx",
        "apps/studio/tests/native-original-mix-voice-binding.spec.ts",
        "apps/studio/tests/native-original-mix.spec.ts",
        "apps/studio/tests/review-preservation.spec.ts",
        "apps/studio/tests/review-service.spec.ts",
        "apps/studio/tests/scene-beat-draft-service.spec.ts",
        "apps/studio/tests/scene-beat-preservation.spec.ts",
        "apps/studio/tests/scene-beat-time-service.spec.ts",
        "apps/studio/src/SceneInspector.tsx",
        "apps/studio/src/deliveryProfileDraft.test.ts",
        "apps/studio/src/deliveryProfileDraft.ts",
        "apps/studio/src/sceneInspectorDraft.test.ts",
        "apps/studio/src/sceneInspectorDraft.ts",
        "apps/studio/tests/delivery-profile-authority-service.spec.ts",
        "apps/studio/tests/delivery-profile-authority.spec.ts",
        "apps/studio/tests/delivery-profile-preservation.spec.ts",
        "apps/studio/tests/fixtures/delivery-profile-authority.html",
        "apps/studio/tests/fixtures/delivery-profile-authority.tsx",
        "apps/studio/tests/fixtures/scene-inspector-authority.html",
        "apps/studio/tests/fixtures/scene-inspector-authority.tsx",
        "apps/studio/tests/scene-inspector-preservation.spec.ts",
        "apps/studio/tests/scene-inspector-service.spec.ts",
        "Cargo.toml",
        "SOURCE_LOCK.json",
        "rust-toolchain.toml",
        "crates/domain/Cargo.toml",
        "crates/storage/Cargo.toml",
        "crates/service/Cargo.toml",
        "apps/desktop/src-tauri/Cargo.toml",
        "apps/studio/package.json",
        "apps/studio/src/api.ts",
        "apps/studio/src/types.ts",
        "apps/studio/index.html",
        "apps/studio/vite.config.ts",
        "apps/studio/tsconfig.json",
        "apps/studio/tsconfig.app.json",
        "apps/studio/src/main.tsx",
        "apps/studio/src/styles.css",
        "apps/studio/src/fixture.ts",
        "apps/studio/src/ProjectLibraryDialog.tsx",
        "apps/studio/src/projectLibrary.ts",
        "apps/studio/src/project-library.css",
        "apps/studio/src/ProjectProjectionWorkspace.tsx",
        "apps/studio/src/projectProjection.ts",
        "apps/studio/src/projectProjection.test.ts",
        "apps/studio/src/projectMutation.test.ts",
        "apps/studio/tests/project-projection-service.spec.ts",
        "apps/studio/tests/project-projection.spec.ts",
        "apps/studio/tests/fixtures/project-projection.html",
        "apps/studio/tests/fixtures/project-projection.tsx",
        "apps/desktop/src-tauri/src/effect_grants.rs",
        "apps/desktop/src-tauri/src/project_library.rs",
        "apps/desktop/src-tauri/src/project_projection.rs",
        "apps/desktop/src-tauri/src/caption_export.rs",
        "apps/desktop/src-tauri/src/file_import.rs",
        "crates/domain/src/lib.rs",
        "crates/domain/src/canvas.rs",
        "crates/domain/src/history.rs",
        "crates/domain/src/creative.rs",
        "crates/service/src/lib.rs",
        "crates/service/src/project_creation.rs",
        "crates/service/src/project_projection.rs",
        "crates/storage/src/lib.rs",
        "crates/storage/src/project_creation.rs",
        "crates/storage/src/project_projection.rs",
        "tooling/ci_canvas_service.py",
        "tooling/tests/test_ci_canvas_service.py",
    )
    paths = tuple(dict.fromkeys((*paths, *CREATIVE_PRODUCTION_SOURCE_PATHS, *BRANCH_CLAIM_SOURCE_PATHS,
                                 "tooling/vite_editor_security_probe.mjs")))
    if is_browser_scope(selection):
        paths = tuple(dict.fromkeys((*paths, *BROWSER_SOURCE_PATHS)))
    if selection == "workflow-preview-browser":
        paths = tuple(dict.fromkeys((*paths, *WORKFLOW_PREVIEW_SOURCE_PATHS)))
    files = {}
    for name in paths:
        original = subprocess.check_output(["git", "show", head + ":" + name], cwd=repo, timeout=30)
        # Reject an aliased parent even when its current bytes happen to match.
        archive_ancestors(repo / name)
        observed = digest(repo / name)
        require(observed == {"bytes": len(original), "sha256": hashlib.sha256(original).hexdigest()},
                "source differs from Git: " + name)
        files[name] = observed
    return {"checkout": head, "tree": git("rev-parse", "HEAD^{tree}"), "files": files}


PROCESS_ENVIRONMENT_KEYS = ("CARGO_TARGET_DIR", "PLAYWRIGHT_JSON_OUTPUT_FILE",
                            "MOTIONWRIGHT_SERVICE_TEST_BIN", "MOTIONWRIGHT_SERVICE_TEST_TMPDIR")
PROCESS_OBSERVATION_SCHEMA = "motionwright.studio-command-process-observation/2"


class UnknownObservation(ValueError):
    def __init__(self, message, command):
        super().__init__(message)
        self.command = command


def execution_unknown(command):
    value = str(command.get("observation", ""))
    return value.startswith("UNKNOWN") or value == "TIMED_OUT_NO_RETRY"


def owned_process_group_state(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return "ABSENT"
    return "PRESENT"


def stop_observed_process(process, owned_group):
    errors = []
    for force in (False, True):
        try:
            if owned_group:
                os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            elif process.poll() is None:
                process.kill() if force else process.terminate()
        except ProcessLookupError:
            pass
        except BaseException as error:
            errors.append(type(error).__name__ + ": " + str(error))
        try:
            process.wait(timeout=2)
        except BaseException as error:
            errors.append(type(error).__name__ + ": " + str(error))
        try:
            if process.poll() is not None and (not owned_group or owned_process_group_state(process.pid) == "ABSENT"):
                break
        except BaseException as error:
            errors.append(type(error).__name__ + ": " + str(error))
    return errors


def require_observed_command(command):
    if execution_unknown(command):
        raise UnknownObservation("uncertain command; no further commands or readback", command)
    require(command.get("process_observation_schema") == PROCESS_OBSERVATION_SCHEMA
            and command.get("process_boundary") == "OWNED_POSIX_SESSION_GROUP"
            and type(command.get("pid")) is int and command["pid"] > 0
            and command.get("owned_group_after_wait") == "ABSENT"
            and command.get("escaped_descendants") == "NOT_OBSERVED"
            and command.get("diagnostic_custody") == "DIRECT_PROCESS_EXIT_OBSERVED"
            and type(command.get("exit_code")) is int and not command.get("observation"),
            "owned process group exit was not observed; no closed execution credit")


def execute(argv, cwd, env, output, label, timeout):
    owned_group = os.name == "posix"
    command = {"argv": argv, "cwd": str(cwd), "started_utc": datetime.now(timezone.utc).isoformat(),
               "process_observation_schema": PROCESS_OBSERVATION_SCHEMA,
               "process_boundary": "OWNED_POSIX_SESSION_GROUP" if owned_group else "DIRECT_PROCESS_ONLY",
               "pid": None, "owned_group_after_wait": "NOT_OBSERVED", "escaped_descendants": "NOT_OBSERVED",
               "selected_environment": {key: env.get(key) for key in PROCESS_ENVIRONMENT_KEYS},
               "cleanup_errors": []}
    process = None
    try:
        with (output / (label + ".stdout.log")).open("xb") as out, (output / (label + ".stderr.log")).open("xb") as err:
            try:
                process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=out, stderr=err,
                                           start_new_session=owned_group)
            except (FileNotFoundError, PermissionError) as error:
                command.update(exit_code=None, observation="NOT_STARTED_LAUNCH_ERROR", error=str(error))
            except OSError as error:
                command.update(exit_code=None, observation="UNKNOWN_PROCESS_NO_RETRY",
                               uncertainty_reason="SPAWN_HANDLE_NOT_OBSERVED", error=type(error).__name__ + ": " + str(error))
            else:
                command["pid"] = process.pid
                try:
                    command["exit_code"] = process.wait(timeout=timeout)
                    if owned_group:
                        command["owned_group_after_wait"] = owned_process_group_state(process.pid)
                        if command["owned_group_after_wait"] != "ABSENT":
                            command.update(observation="UNKNOWN_PROCESS_NO_RETRY",
                                           uncertainty_reason="OWNED_GROUP_PRESENT_AFTER_DIRECT_EXIT")
                    if not execution_unknown(command):
                        command["diagnostic_custody"] = "DIRECT_PROCESS_EXIT_OBSERVED"
                except subprocess.TimeoutExpired:
                    command.update(exit_code=None, observation="UNKNOWN_TIMEOUT_NO_RETRY",
                                   uncertainty_reason="DIRECT_WAIT_TIMEOUT", timeout_seconds=timeout)
                except BaseException as error:
                    command.setdefault("exit_code", None)
                    command.update(observation="UNKNOWN_PROCESS_NO_RETRY",
                                   uncertainty_reason="POST_LAUNCH_OBSERVATION_ERROR",
                                   error=type(error).__name__ + ": " + str(error))
                if execution_unknown(command):
                    try:
                        command["cleanup_errors"] = stop_observed_process(process, owned_group)
                    except BaseException as error:
                        command["cleanup_errors"].append(type(error).__name__ + ": " + str(error))
    except BaseException as error:
        if process is None and isinstance(error, OSError) and not execution_unknown(command):
            raise
        if execution_unknown(command):
            command.setdefault("secondary_observation_errors", []).append(type(error).__name__ + ": " + str(error))
        else:
            command.setdefault("exit_code", None)
            command.update(observation="UNKNOWN_PROCESS_NO_RETRY",
                           uncertainty_reason="OBSERVATION_OR_STREAM_FINALIZATION_ERROR",
                           error=type(error).__name__ + ": " + str(error))
        if process is not None:
            try:
                command["cleanup_errors"].extend(stop_observed_process(process, owned_group))
            except BaseException as cleanup_error:
                command["cleanup_errors"].append(type(cleanup_error).__name__ + ": " + str(cleanup_error))
    command["finished_utc"] = datetime.now(timezone.utc).isoformat()
    for stream in ("stdout", "stderr"):
        path = output / (label + "." + stream + ".log")
        try:
            command[stream] = archive_file_digest(path, ARCHIVE_LIMITS["file_bytes"])
        except BaseException as error:
            command[stream] = {"custody_error": type(error).__name__ + ": " + str(error)}
            if not execution_unknown(command):
                command.update(observation="UNKNOWN_CUSTODY_NO_RETRY", uncertainty_reason="STREAM_FINGERPRINT_FAILED")
    if execution_unknown(command):
        command["diagnostic_custody"] = "UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED"
    try:
        write_new(output / (label + ".command.json"), command)
    except BaseException as error:
        if not execution_unknown(command):
            command.update(observation="UNKNOWN_CUSTODY_NO_RETRY", uncertainty_reason="COMMAND_RECEIPT_PERSISTENCE_FAILED")
        command.update(diagnostic_custody="UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED",
                       receipt_error=type(error).__name__ + ": " + str(error))
        raise UnknownObservation("command receipt could not be retained; no retry", command) from error
    return command



ARCHIVE_ROOTS = ("e", "tools")
ARCHIVE_LIMITS = {"members": 20000, "file_bytes": 512 * 1024 * 1024,
                  "total_bytes": 2 * 1024 * 1024 * 1024, "path_bytes": 1024}
ARCHIVE_CHUNK = 1024 * 1024


def archive_member_name(name):
    require(isinstance(name, str) and name and len(name.encode("utf-8")) <= ARCHIVE_LIMITS["path_bytes"],
            "archive member name exceeds bound")
    parts = name.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    require(len(parts) <= 64 and parts[0] in ARCHIVE_ROOTS
            and all(part not in ("", ".", "..") and not part.endswith((" ", "."))
                    and part.split(".")[0].upper() not in reserved for part in parts)
            and "\\" not in name and ":" not in name
            and all(ord(c) >= 32 and ord(c) != 127 for c in name),
            "unsafe archive member name")
    return name


def archive_stat(path):
    value = path.lstat()
    require(not stat.S_ISLNK(value.st_mode)
            and not getattr(value, "st_file_attributes", 0) & 0x400,
            "archive symlink or reparse point forbidden")
    require(stat.S_ISDIR(value.st_mode) or stat.S_ISREG(value.st_mode),
            "archive nonregular input forbidden")
    if stat.S_ISREG(value.st_mode):
        require(value.st_nlink == 1, "archive hard-linked input forbidden")
    return value


def archive_identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns, value.st_nlink)


def archive_ancestors(path):
    for parent in (path, *path.parents):
        if parent.exists() or parent.is_symlink():
            archive_stat(parent)


def archive_file_digest(path, limit):
    archive_ancestors(path)
    before = archive_stat(path)
    require(stat.S_ISREG(before.st_mode) and before.st_size <= limit,
            "archive file exceeds bound")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        require(archive_identity(os.fstat(stream.fileno())) == archive_identity(before),
                "archive file changed before read")
        count, value = 0, hashlib.sha256()
        while block := stream.read(min(ARCHIVE_CHUNK, limit - count + 1)):
            count += len(block)
            require(count <= limit, "archive file grew beyond bound")
            value.update(block)
        require(archive_identity(os.fstat(stream.fileno())) == archive_identity(before),
                "archive open file changed during read")
    require(count == before.st_size and archive_identity(archive_stat(path)) == archive_identity(before),
            "archive file changed during read")
    archive_ancestors(path)
    return {"bytes": count, "sha256": value.hexdigest()}


def archive_inventory(root):
    archive_ancestors(root)
    require(root.is_dir(), "archive evidence directory missing")
    entries, identities, folded, total = {}, {}, set(), 0
    pending = [root / name for name in reversed(ARCHIVE_ROOTS)]
    while pending:
        path = pending.pop()
        name = archive_member_name(path.relative_to(root).as_posix())
        require(len(entries) < ARCHIVE_LIMITS["members"] and name.casefold() not in folded,
                "archive member bound or alias collision")
        folded.add(name.casefold())
        value = archive_stat(path)
        identity = archive_identity(value)
        item = {"name": name, "kind": "directory" if stat.S_ISDIR(value.st_mode) else "file",
                "mode": stat.S_IMODE(value.st_mode)}
        if item["kind"] == "directory":
            children = []
            with os.scandir(path) as stream:
                for child in stream:
                    children.append(Path(child.path))
                    require(len(entries) + len(pending) + len(children) <= ARCHIVE_LIMITS["members"],
                            "archive member bound")
            require(archive_identity(archive_stat(path)) == identity, "archive directory changed")
            pending.extend(sorted(children, key=lambda p: p.name, reverse=True))
        else:
            item.update(archive_file_digest(path, ARCHIVE_LIMITS["file_bytes"]))
            total += item["bytes"]
            require(total <= ARCHIVE_LIMITS["total_bytes"], "archive total byte bound")
        entries[name], identities[name] = item, identity
    require(all(entries.get(name, {}).get("kind") == "directory" for name in ARCHIVE_ROOTS),
            "archive e/tools directories required")
    return {"entries": dict(sorted(entries.items())), "identities": identities, "file_bytes": total}


def archive_json(root, inventory, name):
    item = inventory["entries"].get(name)
    require(item is not None and item["kind"] == "file" and item["bytes"] <= 64 * 1024 * 1024,
            "bounded original JSON required: " + name)
    path = root / name
    require(archive_file_digest(path, 64 * 1024 * 1024) == {k: item[k] for k in ("bytes", "sha256")},
            "original JSON changed")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        require(archive_identity(os.fstat(stream.fileno())) == inventory["identities"][name],
                "original JSON replaced before parse")
        raw = stream.read(item["bytes"] + 1)
        require(archive_identity(os.fstat(stream.fileno())) == inventory["identities"][name],
                "original JSON changed during parse")
    require(len(raw) == item["bytes"] and hashlib.sha256(raw).hexdigest() == item["sha256"],
            "original JSON changed during parse")
    return json.loads(raw)



def selected_service_store_bindings(root, inventory, selections, captures, binary_path, required_labels):
    """Bind every new service case to its original closed store and final oracle."""
    known = {row["manifest"] for row in captures}
    require(isinstance(required_labels, set) and required_labels
            and required_labels <= ({row[0] for row in CREATIVE_PRODUCTION_SELECTIONS} | {"review", "branch-authoring", "claim-source"}),
            "unknown case/store binding scope")
    bound, used = [], set()
    for selection in selections:
        label = selection["label"]
        if label not in required_labels:
            continue
        document = archive_json(root, inventory, "e/" + label + ".json")
        cases = []

        def collect(suites):
            for suite in suites:
                cases.extend(suite.get("specs", []))
                collect(suite.get("suites", []))

        collect(document["suites"])
        for case in cases:
            attachments = case["tests"][0]["results"][0].get("attachments", [])
            evidence_name = "branch-authoring-evidence" if label == "branch-authoring" else label + "-service-evidence"
            originals = {}
            for name in ("closed-store-outcomes", evidence_name):
                found = [item for item in attachments if item.get("name") == name]
                require(len(found) == 1 and found[0].get("contentType") == "application/json",
                        "service case original attachment missing or duplicate")
                path = Path(found[0].get("path", ""))
                relative = archive_member_name(path.relative_to(root).as_posix())
                parts = relative.split("/")
                require(parts[:2] == ["e", label + "-test-results"],
                        "service case attachment outside its output")
                if len(parts) == 5 and parts[3] == "attachments":
                    require(re.fullmatch(re.escape(name) + r"-[0-9a-f]{40}\.json", parts[4]) is not None,
                            "service case reporter attachment name differs")
                    original_name = "/".join((*parts[:3], name + ".json"))
                else:
                    require(len(parts) == 4 and parts[3] == name + ".json",
                            "service case attachment outside its output")
                    original_name = relative
                value = archive_json(root, inventory, relative)
                if original_name != relative:
                    original = archive_json(root, inventory, original_name)
                    require(all(inventory["entries"][relative][key] == inventory["entries"][original_name][key]
                                for key in ("bytes", "sha256")) and value == original,
                            "service case reporter copy differs from original")
                    value = original
                originals[name] = (original_name, value)
            outcome_name, outcome = originals["closed-store-outcomes"]
            case_root = outcome_name.rsplit("/", 1)[0]
            manifest_name = case_root + "/closed-store/manifest.json"
            require(manifest_name in known and manifest_name not in used,
                    "service case closed store missing or shared by different cases")
            used.add(manifest_name)
            manifest = archive_json(root, inventory, manifest_name)
            oracle = archive_json(root, inventory, case_root + "/closed-store/oracle.json")
            require(outcome.get("schemaVersion") == 1 and outcome.get("errors") == []
                    and outcome.get("capture") == manifest and outcome.get("oracle") == oracle,
                    "service case finalization differs from original closed store")
            preparation, closures = outcome.get("preparation"), outcome.get("closures")
            require(isinstance(preparation, list) and preparation
                    and all(item.get("status") == "fulfilled" and not item.get("error")
                            for item in preparation), "service case preparation incomplete")
            require(isinstance(closures, list) and closures
                    and all(item.get("status") == "fulfilled" and not item.get("error")
                            and item.get("taints") == [] for item in closures)
                    and [{key: item.get(key) for key in ("databasePath", "exits", "taints")}
                         for item in closures] == manifest.get("lifecycle"),
                    "service case closures differ from captured helper lifecycle")
            require(oracle.get("schemaVersion") == 1 and oracle.get("journalComplete") is True
                    and oracle.get("journalAfterRevisionExclusive") == 0
                    and isinstance(oracle.get("journal"), list) and isinstance(oracle.get("project"), dict),
                    "service case complete stored oracle missing")
            evidence_path, evidence = originals[evidence_name]
            require(evidence_path.rsplit("/", 1)[0] == case_root
                    and evidence.get("primaryFailure") is None and evidence.get("failures") == []
                    and evidence.get("canonicalOracle") == oracle and evidence.get("final") == oracle["project"],
                    "service case final Project or original failure differs")
            # These specs explicitly choose info.outputPath('service', 'studio.sqlite3').
            # Their configured TMPDIR is retained in the invocation, but is not the DB location.
            source_directory = root / case_root / "service"
            database_path = str(source_directory / "studio.sqlite3")
            require(manifest.get("sourceDirectory") == str(source_directory)
                    and manifest.get("databaseRelativePath") == "studio.sqlite3",
                    "service case captured source directory or database differs")
            peer_titles = {
                "an unknown Production plan committed result survives reload without replay and needs a fresh history review",
                "an unknown Production plan matching-peer result survives reload without replay and needs a fresh history review",
            }
            participants = 2 if label == "production-design" and case["title"] in peer_titles else 1
            if label in ("review", "branch-authoring", "claim-source"):
                require(isinstance(evidence.get("service"), dict) and "services" not in evidence,
                        "Review original singular service authority differs")
                services = [evidence["service"]]
            else:
                services = evidence.get("services")
            require(isinstance(services, list) and len(services) == participants
                    and all(isinstance(item, dict) and item.get("binaryPath") == str(binary_path)
                            and item.get("databasePath") == database_path for item in services),
                    "service case participant inventory or binary/database authority differs")
            declared_lifecycle = [{"databasePath": item["databasePath"], "exits": item.get("processExits"),
                                   "taints": item.get("lifecycleTaints")} for item in services]
            require(declared_lifecycle == manifest.get("lifecycle"),
                    "service case original process exits or taints differ from captured lifecycle")
            bound.append({"label": label, "file": case["file"], "title": case["title"],
                          "manifest": manifest_name, "outcomes": outcome_name,
                          "original_evidence": evidence_path, "services": participants,
                          "source_directory": str(source_directory), "database_path": database_path,
                          "binary_path": str(binary_path),
                          "technical_scope": "ORIGINAL_CLOSED_STORE_AND_DECLARED_ORACLE; NO_ARCHIVE_REOPEN"})
    require(len(bound) == sum(item["expected_tests"] for item in selections if item["label"] in required_labels),
            "service closed store case inventory differs")
    return bound


def creative_production_store_bindings(root, inventory, selections, captures, binary_path):
    return selected_service_store_bindings(
        root, inventory, selections, captures, binary_path,
        {row[0] for row in CREATIVE_PRODUCTION_SELECTIONS})


def archive_closed_bindings(root, inventory, source):
    receipt = archive_json(root, inventory, "e/receipt.json")
    browser_mode = is_browser_scope(receipt.get("selection_plan", {}).get("requested"))
    tools = ("node", "npm", "python") if browser_mode else ("rustc", "cargo", "node", "npm", "python")
    for tool in tools:
        identity = inventory["entries"].get("tools/" + tool + ".txt", {})
        require(identity.get("kind") == "file" and identity.get("bytes", 0) > 0,
                "archive tool identity missing")
    require(receipt.get("status") == "PASS", "unsealed failed or incomplete service execution")
    require(receipt.get("source_before") == receipt.get("source_after") == source
            and archive_json(root, inventory, "e/source-before.json") == source,
            "archive source binding differs")
    plan = receipt.get("selection_plan", {})
    require(plan == selection_plan(plan.get("requested")), "archive selection binding differs")
    required = selected_selections(plan["requested"])
    selections = receipt.get("selections", [])
    require([x.get("label") for x in selections] == [x[0] for x in required],
            "archive selected roster differs")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for field, variable in (("run_id", "GITHUB_RUN_ID"), ("run_attempt", "GITHUB_RUN_ATTEMPT"),
                                ("job", "GITHUB_JOB")):
            require(receipt.get(field) == os.environ.get(variable) and receipt.get(field),
                    "archive run identity differs")

    def command_closed(command, label):
        require_observed_command(command)
        require(type(command.get("exit_code")) is int and command["exit_code"] == 0
                and command.get("diagnostic_custody") == "DIRECT_PROCESS_EXIT_OBSERVED"
                and not command.get("observation"),
                "unsealed command or unobserved descendants")
        require(archive_json(root, inventory, "e/" + label + ".command.json") == command,
                "archive original command differs")
        for stream in ("stdout", "stderr"):
            item = inventory["entries"].get("e/" + label + "." + stream + ".log", {})
            require(item.get("kind") == "file"
                    and command.get(stream) == {k: item.get(k) for k in ("bytes", "sha256")},
                    "archive original stream differs")

    if browser_mode:
        require(receipt.get("execution_scope") == "SYNTHETIC_BROWSER_CONTRACTS"
                and receipt.get("service_runtime") == receipt.get("service_build") == "NOT_RUN"
                and receipt.get("service_target_absent") is True
                and not any(key in receipt for key in ("build", "service_binary", "retained_service_binary")),
                "browser receipt claims service execution")
        require(receipt.get("service_environment") == {
            "MOTIONWRIGHT_SERVICE_TEST_BIN": "ABSENT", "MOTIONWRIGHT_SERVICE_TEST_TMPDIR": "ABSENT"},
            "browser service authority was not excluded")
        require(all(receipt.get(key) == "NOT_RUN" for key in
                    ("installed_tauri", "native_render", "human_acceptance")),
                "browser receipt claims native or installed execution")
        repo = root.parent / "g"
        cwd = str(repo / "apps/studio")
        node = receipt.get("node", {}).get("path")
        cli = repo / "apps/studio/node_modules/@playwright/test/cli.js"
        require(isinstance(node, str) and node and receipt.get("playwright_cli", {}).get("path") == str(cli),
                "browser tool paths differ")
        for identity in (receipt.get("node", {}), receipt.get("playwright_cli", {})):
            require(type(identity.get("bytes")) is int and identity["bytes"] > 0
                    and re.fullmatch("[0-9a-f]{64}", identity.get("sha256", "")) is not None,
                    "browser executable identity missing")
        discovery = receipt.get("discovery", {})
        command_closed(discovery.get("command", {}), "browser-discovery")
        require(discovery.get("status") == "LISTED_NOT_EXECUTED"
                and discovery["command"].get("argv") == browser_command(node, cli, root / "e", scope=plan["requested"])
                and discovery["command"].get("cwd") == cwd
                and discovery["command"].get("selected_environment") == {
                    **dict.fromkeys(PROCESS_ENVIRONMENT_KEYS),
                    "PLAYWRIGHT_JSON_OUTPUT_FILE": str(root / "e/browser-discovery.json")},
                "browser discovery command differs")
        original = inventory["entries"].get("e/browser-discovery.json", {})
        require(discovery.get("original_report") == {key: original.get(key) for key in ("bytes", "sha256")}
                and discovery.get("verified") == validate_browser_discovery(
                    archive_json(root, inventory, "e/browser-discovery.json"), plan["requested"]),
                "browser discovery original differs")
    else:
        repo, target = root.parent / "g", root / "t"
        cwd = str(repo / "apps/studio")
        cli = repo / "apps/studio/node_modules/@playwright/test/cli.js"
        cargo, node = receipt.get("cargo", {}).get("path"), receipt.get("node", {}).get("path")
        require(receipt.get("target_directory") == str(target), "service target directory differs")
        for name in ("cargo", "node"):
            identity = receipt.get(name, {})
            require(isinstance(identity.get("path"), str) and Path(identity["path"]).is_absolute(),
                    "service tool path missing: " + name)
            require(observed_tool(Path(identity["path"])) == identity, "service tool identity differs: " + name)
        require(receipt.get("playwright_cli") == digest(cli), "service Playwright CLI identity differs")
        binary_path = target / "debug/examples/studio_review_session"
        require(receipt.get("service_binary", {}).get("path") == str(binary_path), "service binary path differs")
        build = receipt.get("build", {})
        command_closed(build, "build")
        require(build.get("argv") == [cargo, "build", "--locked", "-p", "motionwright-service",
                                       "--example", "studio_review_session"]
                and build.get("cwd") == str(repo)
                and build.get("selected_environment") == service_environment_binding(target),
                "service build invocation differs")
    for selection, (_, filename, titles) in zip(selections, required):
        label = selection["label"]
        command_closed(selection.get("command", {}), label)
        if browser_mode:
            require(selection["command"].get("argv") == browser_command(
                node, cli, root / "e", (label, filename, titles), scope=plan["requested"])
                and selection["command"].get("cwd") == cwd
                and selection["command"].get("selected_environment") == {
                    **dict.fromkeys(PROCESS_ENVIRONMENT_KEYS),
                    "PLAYWRIGHT_JSON_OUTPUT_FILE": str(root / "e" / (label + ".json"))},
                "browser selected command differs")
        else:
            require(selection["command"].get("argv") == service_command(
                node, cli, root / "e", (label, filename, titles))
                and selection["command"].get("cwd") == cwd
                and selection["command"].get("selected_environment") == service_environment_binding(
                    target, root / "e", label, binary_path), "service selected invocation differs")
        original = inventory["entries"].get("e/" + label + ".json", {})
        require(selection.get("status") == "PASS" and selection.get("expected_tests") == len(titles)
                and selection.get("original_report") == {k: original.get(k) for k in ("bytes", "sha256")},
                "archive report binding differs")
        verify = validate_browser_execution if browser_mode else validate_report
        require(selection.get("verified") == verify(
            archive_json(root, inventory, "e/" + label + ".json"), filename, titles),
            "archive original selected results differ")
    if browser_mode:
        for name in inventory["entries"]:
            parts = name.split("/")
            require(not any(part.endswith("-stores") or part.startswith("executed-studio-service") for part in parts)
                    and not name.endswith((".sqlite", ".sqlite3", ".db", ".sqlite-wal", ".sqlite-shm")),
                    "browser archive contains service storage or executable evidence")
            if name.endswith("/manifest.json"):
                manifest = archive_json(root, inventory, name)
                require(not any(key in manifest for key in ("snapshotDirectory", "snapshotSha256", "databaseRelativePath")),
                        "browser archive contains a closed-service store claim")
        return {"receipt": {key: inventory["entries"]["e/receipt.json"][key] for key in ("bytes", "sha256")},
                "selection_plan": plan, "discovery": discovery["verified"], "captures": [],
                "service_runtime": "NOT_RUN"}
    binary = receipt.get("retained_service_binary", {})
    require(binary.get("path") in ("executed-studio-service", "executed-studio-service.exe"),
            "archive retained executable name differs")
    retained = inventory["entries"].get("e/" + binary["path"], {})
    require(retained.get("kind") == "file"
            and {k: retained.get(k) for k in ("bytes", "sha256")} == {k: binary.get(k) for k in ("bytes", "sha256")}
            == {k: receipt.get("service_binary", {}).get(k) for k in ("bytes", "sha256")},
            "archive retained executable differs")
    captures = []
    for name in inventory["entries"]:
        if not name.endswith("/manifest.json"):
            continue
        manifest = archive_json(root, inventory, name)
        if not any(key in manifest for key in ("entries", "snapshotDirectory", "snapshotSha256")):
            continue
        entries = manifest.get("entries")
        require(manifest.get("schemaVersion") == 1 and isinstance(entries, list)
                and 0 < len(entries) <= ARCHIVE_LIMITS["members"], "invalid closed-store manifest")
        aggregate = hashlib.sha256(json.dumps(entries, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        require(manifest.get("sourceBeforeSha256") == manifest.get("sourceAfterSha256")
                == manifest.get("snapshotSha256") == aggregate, "closed-store aggregate differs")
        prefix = name.rsplit("/", 1)[0] + "/store"
        require(Path(manifest.get("snapshotDirectory", "")) == root / prefix,
                "closed-store snapshot path differs")
        actual = {}
        for member_name, item in inventory["entries"].items():
            if member_name != prefix and not member_name.startswith(prefix + "/"):
                continue
            relative = member_name[len(prefix):].lstrip("/")
            entry = {"path": relative, "kind": item["kind"]}
            if item["kind"] == "file":
                entry.update(sizeBytes=item["bytes"], sha256=item["sha256"])
            actual[relative] = entry
        require(all(isinstance(e, dict) and isinstance(e.get("path"), str) for e in entries),
                "closed-store entry malformed")
        expected = {e["path"]: e for e in entries}
        require(len(expected) == len(entries) and actual == expected,
                "complete closed-store files/directories differ")
        database = archive_member_name(prefix + "/" + manifest.get("databaseRelativePath", ""))
        require(inventory["entries"].get(database, {}).get("kind") == "file",
                "closed-store database missing")
        lifecycle = manifest.get("lifecycle")
        require(isinstance(lifecycle, list) and lifecycle, "closed-store lifecycle missing")
        for process in lifecycle:
            exits = process.get("exits")
            require(process.get("taints") == [] and isinstance(exits, list) and exits
                    and all(type(x.get("processId")) is int and x["processId"] > 0
                            and type(x.get("code")) is int and x["code"] == 0
                            and x.get("signal") is None for x in exits),
                    "unsealed closed-store process lifecycle")
        captures.append({"manifest": name, "sha256": inventory["entries"][name]["sha256"],
                         "snapshot_sha256": aggregate, "entries": len(entries),
                         "evidence_scope": manifest.get("evidenceScope")})
    result = {"receipt": {k: inventory["entries"]["e/receipt.json"][k] for k in ("bytes", "sha256")},
              "selection_plan": plan, "captures": captures}
    if any(row["label"] in {item[0] for item in CREATIVE_PRODUCTION_SELECTIONS} for row in selections):
        result["creative_production_cases"] = creative_production_store_bindings(
            root, inventory, selections, captures, binary_path)
    if any(row["label"] == "review" for row in selections):
        result["review_cases"] = selected_service_store_bindings(
            root, inventory, selections, captures, binary_path, {"review"})
    if any(row["label"] in {item[0] for item in BRANCH_CLAIM_SELECTIONS} for row in selections):
        result["branch_claim_cases"] = selected_service_store_bindings(
            root, inventory, selections, captures, binary_path, {row[0] for row in BRANCH_CLAIM_SELECTIONS})
    return result


def write_closed_tar(root, target, inventory):
    with target.open("xb") as stream:
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name, item in inventory["entries"].items():
                path = root / name
                require(archive_identity(archive_stat(path)) == inventory["identities"][name],
                        "archive source changed before copy")
                info = tarfile.TarInfo(name)
                info.mode, info.mtime = item["mode"], 0
                info.type = tarfile.DIRTYPE if item["kind"] == "directory" else tarfile.REGTYPE
                if item["kind"] == "directory":
                    archive.addfile(info)
                else:
                    archive_ancestors(path)
                    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                    with os.fdopen(os.open(path, flags), "rb") as original:
                        require(archive_identity(os.fstat(original.fileno())) == inventory["identities"][name],
                                "archive source replaced before copy")
                        info.size = item["bytes"]
                        archive.addfile(info, original)
                        require(archive_identity(os.fstat(original.fileno())) == inventory["identities"][name],
                                "archive source changed during copy")
        stream.flush()
        os.fsync(stream.fileno())



def archive_header_bounds(path):
    """Check physical TAR headers before tarfile can allocate extended metadata."""
    last_end, count, pending_pax = 0, 0, False
    with path.open("rb") as stream:
        while True:
            header = stream.read(512)
            require(len(header) == 512, "truncated archive header")
            if header == bytes(512):
                require(not pending_pax and stream.read(512) == bytes(512), "invalid archive end marker")
                tail = stream.read(11265)
                require(len(tail) <= 10240 and not any(tail) and not stream.read(1),
                        "archive trailing data exceeds bound")
                return last_end
            member = tarfile.TarInfo.frombuf(header, encoding="utf-8", errors="strict")
            count += 1
            require(count <= ARCHIVE_LIMITS["members"] * 2 and member.size >= 0,
                    "archive physical header bound")
            if member.type == tarfile.XHDTYPE:
                require(not pending_pax and member.size <= 4096, "archive extended metadata exceeds bound")
                pending_pax = True
            else:
                require(member.type in (tarfile.DIRTYPE, tarfile.REGTYPE)
                        and member.size <= ARCHIVE_LIMITS["file_bytes"]
                        and (member.type != tarfile.DIRTYPE or member.size == 0),
                        "archive physical member type or size")
                pending_pax = False
            stream.seek(((member.size + 511) // 512) * 512, 1)
            require(stream.tell() <= path.stat().st_size, "truncated archive payload")
            last_end = stream.tell()


def verify_closed_tar(path, inventory):
    archive_ancestors(path)
    before = archive_identity(archive_stat(path))
    size = archive_stat(path).st_size
    require(size <= ARCHIVE_LIMITS["total_bytes"] + ARCHIVE_LIMITS["members"] * 4096 + 11264,
            "archive envelope exceeds bound")
    physical_end = archive_header_bounds(path)
    expected, seen, folded, total, last_end = inventory["entries"], set(), set(), 0, 0
    with tarfile.open(path, mode="r|") as archive:
        for member in archive:
            name = archive_member_name(member.name)
            require(len(seen) < ARCHIVE_LIMITS["members"] and name not in seen
                    and name.casefold() not in folded and name in expected,
                    "duplicate, unexpected or excessive archive member")
            seen.add(name)
            folded.add(name.casefold())
            item = expected[name]
            require(member.type == (tarfile.DIRTYPE if item["kind"] == "directory" else tarfile.REGTYPE)
                    and not member.linkname and member.sparse is None
                    and set(member.pax_headers) <= {"path"},
                    "archive member type or extension differs")
            require(member.mode == item["mode"] and member.uid == member.gid == 0
                    and member.uname == member.gname == "" and member.mtime == 0,
                    "archive member metadata differs")
            require(member.size == item.get("bytes", 0) and member.size <= ARCHIVE_LIMITS["file_bytes"],
                    "archive member size differs")
            if item["kind"] == "file":
                value, count = hashlib.sha256(), 0
                with archive.extractfile(member) as original:
                    while block := original.read(min(ARCHIVE_CHUNK, member.size - count + 1)):
                        count += len(block)
                        require(count <= member.size, "archive member grew beyond declared size")
                        value.update(block)
                require(count == member.size and value.hexdigest() == item["sha256"],
                        "archive member digest differs")
                total += count
                require(total <= ARCHIVE_LIMITS["total_bytes"], "archive total byte bound")
            last_end = member.offset_data + ((member.size + 511) // 512) * 512
    require(seen == set(expected) and total == inventory["file_bytes"],
            "archive complete member structure differs")
    require(physical_end == last_end and 1024 <= size - last_end <= 11264 and (size - last_end) % 512 == 0,
            "archive end marker or padding differs")
    with path.open("rb") as stream:
        stream.seek(last_end)
        require(not any(stream.read()), "archive trailing nonzero data")
    observed = archive_file_digest(path, ARCHIVE_LIMITS["total_bytes"] + ARCHIVE_LIMITS["members"] * 4096 + 11264)
    require(archive_identity(archive_stat(path)) == before, "archive changed during verification")
    return {"members": len(seen), "file_bytes": total, "complete_structure": True, "archive_digest": observed}


def retain_closed_archive(repo, root):
    output = root / "retained"
    archive_ancestors(output)
    require(not output.exists(), "fresh archive output directory required")
    output.mkdir()
    report = {"schema": "motionwright.studio-service-archive/1", "status": "FAIL",
              "custody": "UNSEALED_DIAGNOSTICS", "source": None,
              "human_acceptance": False, "formal_acceptance": False,
              "scope": "Original e/tools tree, direct command exits and declared helper closures; no additional app reopen"}
    try:
        # Observe the bounded receipt before traversing streams that an uncertain
        # descendant may still be changing. No source command or copy is safe yet.
        receipt_name = "e/receipt.json"
        receipt_path = root / receipt_name
        receipt_identity = archive_identity(archive_stat(receipt_path))
        receipt_digest = archive_file_digest(receipt_path, 64 * 1024 * 1024)
        receipt_inventory = {"entries": {receipt_name: {"kind": "file", **receipt_digest}},
                             "identities": {receipt_name: receipt_identity}}
        original_receipt = archive_json(root, receipt_inventory, receipt_name)
        if original_receipt.get("status") == "UNKNOWN":
            raise UnknownObservation("original execution is UNKNOWN; no source commands or archive copy",
                                     original_receipt.get("unsealed_command", {}))
        commands = [original_receipt.get("build", {}), original_receipt.get("discovery", {}).get("command", {}),
                    *(row.get("command", {}) for row in original_receipt.get("selections", []))]
        for command in commands:
            if execution_unknown(command):
                raise UnknownObservation("original command is UNKNOWN; no source commands or archive copy", command)
        require(original_receipt.get("status") == "PASS", "original execution is not complete")
        before = archive_inventory(root)
        require(before["identities"].get(receipt_name) == receipt_identity
                and archive_json(root, before, receipt_name) == original_receipt,
                "original receipt changed before archive admission")
        selected = original_receipt.get("selection_plan", {}).get("requested")
        source_mode = selected if is_browser_scope(selected) else None
        source = source_observation(repo, source_mode) if source_mode else source_observation(repo)
        report["source"] = source
        report["bindings"] = archive_closed_bindings(root, before, source)
        write_new(output / "input-inventory.json", before)
        archive = output / "closed-evidence.tar"
        write_closed_tar(root, archive, before)
        report["verified"] = verify_closed_tar(archive, before)
        require(archive_inventory(root) == before, "original evidence changed during retention")
        after = source_observation(repo, source_mode) if source_mode else source_observation(repo)
        require(after == source, "source changed during retention")
        require(archive_file_digest(archive, ARCHIVE_LIMITS["total_bytes"] + ARCHIVE_LIMITS["members"] * 4096 + 11264)
                == report["verified"]["archive_digest"], "retained archive changed")
        report["archive"] = {"path": archive.name, **report["verified"]["archive_digest"]}
        report["input_inventory"] = digest(output / "input-inventory.json")
        report["originals_equal_before_after"] = True
        report["status"], report["custody"] = "PASS", "VERIFIED_ORIGINAL_TREE_COPY"
    except UnknownObservation as error:
        report.update(status="UNKNOWN", error=str(error), unsealed_command=error.command)
    except Exception as error:
        report["error_type"], report["error"] = type(error).__name__, str(error)
    write_new(output / "archive-receipt.json", report)
    return report


def archive_main(arguments):
    parser = argparse.ArgumentParser(description="Retain the complete synthetic CI evidence tree in a verified TAR.")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(arguments)
    repo, root = (Path(os.path.abspath(p)) for p in (args.repo, args.evidence))
    require(os.environ.get("GITHUB_ACTIONS") == "true", "archive command is restricted to its synthetic CI job")
    require(repo.name == "g" and root == repo.parent / "v"
            and Path(os.environ.get("GITHUB_WORKSPACE", "")) == repo.parent,
            "archive command requires the isolated CI g/v layout")
    report = retain_closed_archive(repo, root)
    print(json.dumps({"status": report["status"], "custody": report["custody"],
                      "receipt": str(root / "retained/archive-receipt.json")}))
    return 0 if report["status"] == "PASS" else 1


def browser_selections(scope="browser-contracts"):
    require(BROWSER_SELECTIONS == (*HISTORICAL_BROWSER_SELECTIONS,
                                   *CREATIVE_PRODUCTION_BROWSER_SELECTIONS)
            and len(HISTORICAL_BROWSER_SELECTIONS) == 31
            and sum(len(row[2]) for row in HISTORICAL_BROWSER_SELECTIONS) == 148,
            "historical browser inventory differs")
    require(len(BROWSER_SELECTIONS) == 33, "browser file count differs")
    labels, files = set(), set()
    count = 0
    for label, filename, titles in BROWSER_SELECTIONS:
        require(label.startswith("browser-") and label not in labels, "duplicate browser selection")
        require(filename.endswith(".spec.ts") and "/" not in filename and filename not in files,
                "invalid or duplicate browser filename")
        require(isinstance(titles, tuple) and titles and all(isinstance(x, str) and x for x in titles)
                and len(set(titles)) == len(titles), "invalid browser title inventory")
        labels.add(label)
        files.add(filename)
        count += len(titles)
    require(count == 206, "browser case count differs")
    require(is_browser_scope(scope), "unknown named browser scope")
    selected_labels = BROWSER_SCOPES[scope]
    require(REVIEW_BROWSER_SELECTIONS == (
                ("browser-review", "review-preservation.spec.ts", REVIEW_BROWSER_TITLES),)
            and isinstance(REVIEW_BROWSER_TITLES, tuple)
            and len(REVIEW_BROWSER_TITLES) == len(set(REVIEW_BROWSER_TITLES)) == 29
            and all(isinstance(title, str) and title for title in REVIEW_BROWSER_TITLES)
            and "browser-review" not in labels
            and "review-preservation.spec.ts" not in files,
            "Review browser inventory differs")
    require(len(WORKFLOW_PREVIEW_BROWSER_SELECTIONS) == 2
            and tuple((label, filename, len(titles)) for label, filename, titles
                      in WORKFLOW_PREVIEW_BROWSER_SELECTIONS) == (
                ("browser-workflow-recovery", "workflow-action-recovery.spec.ts", 33),
                ("browser-preview-recovery", "preview-recovery.spec.ts", 27)),
            "workflow and preview browser inventory differs")
    for label, filename, titles in WORKFLOW_PREVIEW_BROWSER_SELECTIONS:
        require(label not in labels and filename not in files
                and isinstance(titles, tuple) and len(set(titles)) == len(titles)
                and all(isinstance(title, str) and title for title in titles),
                "workflow and preview browser identities differ")
        labels.add(label)
        files.add(filename)
    expected_labels, expected_count = {
        "browser-contracts": (tuple(row[0] for row in BROWSER_SELECTIONS), 206),
        "creative-production-browser": (("browser-production-design", "browser-creative-workbench"), 58),
        "review-browser": (("browser-review",), 29),
        "workflow-preview-browser": (("browser-program-av-monitor", "browser-workflow-recovery", "browser-preview-recovery"), 62),
    }[scope]
    require(selected_labels == expected_labels, "browser scope inventory differs")
    chosen = tuple(row for row in (*BROWSER_SELECTIONS, *REVIEW_BROWSER_SELECTIONS,
                                    *WORKFLOW_PREVIEW_BROWSER_SELECTIONS)
                   if row[0] in selected_labels)
    require(len(chosen) == len(selected_labels), "browser scope has unknown or duplicate files")
    require(sum(len(row[2]) for row in chosen) == expected_count,
            "browser scope case count differs")
    return chosen


def browser_discovery_inventory(scope="browser-contracts"):
    chosen = browser_selections(scope)
    selected = {(filename, title) for _, filename, titles in chosen for title in titles}
    exclusions = set(BROWSER_OMITTED_SERVICE_CASES)
    require(len(exclusions) == 4
            and all(filename == "project-mutation.spec.ts" for filename, _ in exclusions)
            and not selected.intersection(exclusions), "invalid excluded service inventory")
    files = {filename for _, filename, _ in chosen}
    excluded = {(filename, title) for filename, title in exclusions if filename in files}
    return selected, excluded


def browser_full_title_path(filename, title):
    return ("chromium", filename, *BROWSER_DESCRIBE_TITLES.get((filename, title), ()), title)


def browser_result_identities(document):
    found = []

    def visit(suites, parents=()):
        require(isinstance(suites, list), "browser suite list missing")
        for suite in suites:
            require(isinstance(suite, dict), "browser suite must be an object")
            hierarchy = (*parents, suite.get("title"))
            require(all(isinstance(name, str) and name for name in hierarchy), "browser suite title missing")
            for spec in suite.get("specs", []):
                require(isinstance(spec, dict), "browser case must be an object")
                pair = (spec.get("file"), spec.get("title"))
                require(all(isinstance(name, str) and name for name in pair), "browser case identity missing")
                tests = spec.get("tests", [])
                require(len(tests) == 1 and isinstance(tests[0], dict), "browser project count differs")
                case = tests[0]
                require(case.get("projectId") == case.get("projectName") == "chromium"
                        and spec.get("tags") == [], "browser project name or tags differ")
                require(("chromium", *hierarchy, pair[1]) == browser_full_title_path(*pair),
                        "browser full title path differs")
                found.append((pair, case))
            visit(suite.get("suites", []), hierarchy)

    visit(document.get("suites"))
    return found


def validate_browser_discovery(document, scope="browser-contracts"):
    """Validate names without interpreting list-only results as executed passes."""
    require(isinstance(document, dict) and document.get("errors") == [],
            "discovery has missing or nonempty global errors")
    selected, excluded = browser_discovery_inventory(scope)
    expected = selected | excluded
    stats = document.get("stats", {})
    require(stats.get("expected") == 0 and stats.get("skipped") == len(expected)
            and stats.get("unexpected") == 0 and stats.get("flaky") == 0,
            "discovery must contain only unexecuted list results")
    found = set()
    for pair, case in browser_result_identities(document):
        require(pair in expected and pair not in found, "unknown or duplicate discovered case")
        require(case.get("results") == [], "discovery contains an execution result")
        require(case.get("expectedStatus") == ("skipped" if pair in excluded else "passed"),
                "discovery skip or expected failure differs from the closed scope")
        found.add(pair)
    require(found == expected, "discovered browser inventory differs")
    return {"status": "LISTED_NOT_EXECUTED", "listed": len(expected), "selected": len(selected),
            "excluded_service": len(excluded), "execution_results": 0}


def validate_browser_execution(document, filename, titles):
    verified = validate_report(document, filename, titles)
    identities = [pair for pair, _ in browser_result_identities(document)]
    require(set(identities) == {(filename, title) for title in titles}
            and len(identities) == len(titles), "browser executed full identity differs")
    return verified


def observed_tool(path):
    path = Path(os.path.abspath(path))
    resolved = path.resolve(strict=True)
    return {"path": str(path), "resolved_path": str(resolved), **digest(resolved)}


def service_environment_binding(target, output=None, label=None, binary=None):
    return {"CARGO_TARGET_DIR": str(target),
            "PLAYWRIGHT_JSON_OUTPUT_FILE": str(output / (label + ".json")) if label is not None else None,
            "MOTIONWRIGHT_SERVICE_TEST_BIN": str(binary) if label is not None else None,
            "MOTIONWRIGHT_SERVICE_TEST_TMPDIR": str(output / (label + "-stores")) if label is not None else None}


def service_command(node, cli, output, selection):
    label, filename, _titles = selection
    argv = [node, str(cli), "test", "tests/" + filename, "--project=chromium",
            "--workers=1", "--retries=0", "--reporter=json",
            "--output=" + str(output / (label + "-test-results"))]
    if label == "ink":
        argv += ["--grep", re.escape(INK_TITLE) + "$"]
    return argv


def browser_command(node, cli, output, selection=None, *, scope="browser-contracts"):
    if selection is None:
        return [node, str(cli), "test",
                *("tests/" + filename for _, filename, _ in browser_selections(scope)),
                "--project=chromium", "--list", "--reporter=json"]
    label, filename, titles = selection
    require(selection in browser_selections(scope), "unknown browser file selection")
    pattern = "^(?:" + "|".join(re.escape(" ".join(browser_full_title_path(filename, title)))
                               for title in titles) + ")$"
    return [node, str(cli), "test", "tests/" + filename, "--project=chromium",
            "--workers=1", "--retries=0", "--reporter=json",
            "--output=" + str(output / (label + "-test-results")), "--grep", pattern]


def browser_report_original(path):
    with path.open("rb") as stream:
        raw = stream.read(64 * 1024 * 1024 + 1)
    require(len(raw) <= 64 * 1024 * 1024, "browser report exceeds bound")
    observed = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    require(digest(path) == observed, "browser original report changed")
    return observed, json.loads(raw)


def run_browser_selection(repo, output, target, scope="browser-contracts"):
    """Run synthetic browser contracts through the canonical process observer."""
    chosen = browser_selections(scope)
    repo, output, target = (path.resolve() for path in (repo, output, target))
    require(repo.is_dir(), "repository missing")
    require(not output.exists() and not target.exists(), "fresh evidence and build directories required")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "motionwright.canvas-service-ci/1", "status": "FAIL",
              "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
              "job": os.environ.get("GITHUB_JOB"), "selections": [],
              "selection_plan": selection_plan(scope),
              "execution_scope": "SYNTHETIC_BROWSER_CONTRACTS",
              "service_runtime": "NOT_RUN", "service_build": "NOT_RUN",
              "installed_tauri": "NOT_RUN", "native_render": "NOT_RUN", "human_acceptance": "NOT_RUN"}
    try:
        before = source_observation(repo, scope)
        report["source_before"] = before
        write_new(output / "source-before.json", before)
        node = shutil.which("node")
        require(node is not None, "node is required for browser contracts")
        cli = repo / "apps/studio/node_modules/@playwright/test/cli.js"
        report["playwright_cli"] = {"path": str(cli), **digest(cli)}
        report["node"] = {"path": node, **digest(Path(node))}
        env = dict(os.environ)
        for variable in PROCESS_ENVIRONMENT_KEYS:
            env.pop(variable, None)
        report["service_environment"] = {"MOTIONWRIGHT_SERVICE_TEST_BIN": "ABSENT",
                                         "MOTIONWRIGHT_SERVICE_TEST_TMPDIR": "ABSENT"}
        discovery_env = dict(env)
        discovery_env["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(output / "browser-discovery.json")
        command = execute(browser_command(node, cli, output, scope=scope), repo / "apps/studio",
                          discovery_env, output, "browser-discovery", 180)
        report["discovery"] = {"command": command, "status": "UNADMITTED"}
        require_observed_command(command)
        require(command["exit_code"] == 0, "browser discovery process did not exit zero")
        original, document = browser_report_original(output / "browser-discovery.json")
        report["discovery"].update(original_report=original, verified=validate_browser_discovery(document, scope),
                                   status="LISTED_NOT_EXECUTED")
        for selection in chosen:
            label, filename, titles = selection
            case_env = dict(env)
            case_env["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(output / (label + ".json"))
            command = execute(browser_command(node, cli, output, selection, scope=scope),
                              repo / "apps/studio", case_env, output, label,
                              BROWSER_SELECTION_TIMEOUTS[label])
            observed = {"label": label, "expected_tests": len(titles), "command": command, "status": "FAIL"}
            report["selections"].append(observed)
            require_observed_command(command)
            try:
                original, document = browser_report_original(output / (label + ".json"))
                observed["original_report"] = original
                observed["verified"] = validate_browser_execution(document, filename, titles)
                require(command["exit_code"] == 0, "selected browser process did not exit zero")
                observed["status"] = "PASS"
            except Exception as error:
                observed["error"] = str(error)
            if command["exit_code"] is None:
                break
        report["source_after"] = source_observation(repo, scope)
        require(report["source_after"] == before, "source changed during browser execution")
        require(digest(cli) == {key: report["playwright_cli"][key] for key in ("bytes", "sha256")},
                "Playwright CLI changed")
        require(digest(Path(node)) == {key: report["node"][key] for key in ("bytes", "sha256")},
                "Node executable changed")
        require(not target.exists(), "browser contracts created a service build target")
        report["service_target_absent"] = True
        require(len(report["selections"]) == len(chosen)
                and all(item["status"] == "PASS" for item in report["selections"]),
                "required browser selection failed")
        report["status"] = "PASS"
    except UnknownObservation as error:
        report.update(status="UNKNOWN", error=str(error), unsealed_command=error.command,
                      terminal_custody="UNSEALED_NO_FURTHER_COMMANDS")
    except Exception as error:
        report["error"] = str(error)
    write_new(output / "receipt.json", report)
    print(json.dumps({"status": report["status"],
                      "selected_tests": sum(item["expected_tests"] for item in report["selections"]),
                      "receipt": str(output / "receipt.json")}))
    return 0 if report["status"] == "PASS" else 1



def validate_creative_production_units(document, studio, selection="creative-production"):
    """Admit only the exact selected original assertions from the locked CLI."""
    rows = selected_unit_inventory(selection)
    expected_count = sum(len(titles) for _, titles in rows)
    require(isinstance(document, dict) and document.get("success") is True,
            "unit report is missing or unsuccessful")
    require(document.get("numTotalTests") == document.get("numPassedTests") == expected_count,
            "unit test census differs")
    for key in ("numFailedTests", "numPendingTests", "numTodoTests", "numFailedTestSuites",
                "numPendingTestSuites"):
        require(document.get(key) == 0, "unit omitted or failing results: " + key)
    require(document.get("wasInterrupted", False) is False
            and not document.get("unhandledErrors") and not document.get("errors")
            and not document.get("testExecError"), "unit global execution error")
    expected = {str(studio / name): titles for name, titles in rows}
    suites = document.get("testResults")
    require(isinstance(suites, list) and len(suites) == len(rows), "unit file inventory differs")
    found, files = [], set()
    for suite in suites:
        name = suite.get("name")
        require(name in expected and name not in files and suite.get("status") == "passed"
                and not suite.get("message") and not suite.get("testExecError"),
                "unit suite identity or result differs")
        files.add(name)
        wanted, observed = set(expected[name]), set()
        assertions = suite.get("assertionResults")
        require(isinstance(assertions, list) and len(assertions) == len(wanted),
                "unit assertion count differs")
        for assertion in assertions:
            ancestors = assertion.get("ancestorTitles")
            require(isinstance(ancestors, list) and len(ancestors) == 1,
                    "unit suite hierarchy differs")
            pair = (ancestors[0], assertion.get("title"))
            require(pair in wanted and pair not in observed
                    and assertion.get("fullName") == " ".join(pair), "unit exact title differs")
            require(assertion.get("status") == "passed" and assertion.get("failureMessages") == []
                    and not assertion.get("failureDetails") and not assertion.get("retryReasons")
                    and assertion.get("retryCount", 0) == 0,
                    "unit assertion is not an original ordinary pass")
            observed.add(pair)
            found.append({"file": str(Path(name).relative_to(studio)), "suite": pair[0], "title": pair[1]})
        require(observed == wanted, "unit exact assertion inventory differs")
    require(files == set(expected), "unit file omitted")
    return {"status": "PASS", "passed": expected_count, "files": len(rows), "skipped": 0, "retries": 0,
            "assertions": found, "evidence_scope": "UNIT_ASSERTIONS_ONLY", "formal_acceptance": False}


def run_creative_production_units(repo, output, selection="creative-production"):
    """Run the selected Vitest files and production build once for a composed SHA."""
    units = selected_unit_inventory(selection)
    source_scope = {"review": "review-browser", "creative-production": "creative-production-browser",
                    "workflow-preview": "workflow-preview-browser", "branch-claim": None}[selection]
    repo, output = repo.resolve(), output.resolve()
    require(repo.is_dir() and not output.exists(), "fresh unit evidence and existing source required")
    output.mkdir(parents=True, exist_ok=False)
    studio = repo / "apps/studio"
    report = {"schema": "motionwright.studio-unit-build-ci/1", "status": "FAIL",
              "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
              "job": os.environ.get("GITHUB_JOB"), "commands": [], "requested_selection": selection,
              "selection": [{"file": name, "titles": [list(pair) for pair in titles]}
                            for name, titles in units],
              "service_runtime": "NOT_RUN", "browser_runtime": "NOT_RUN", "native_render": "NOT_RUN",
              "installed_tauri": "NOT_RUN", "human_acceptance": False, "formal_acceptance": False}
    try:
        before = source_observation(repo, source_scope)
        report["source_before"] = before
        write_new(output / "source-before.json", before)
        node, npm = shutil.which("node"), shutil.which("npm")
        require(node is not None and npm is not None, "Node and npm required for selected unit/build verification")
        node, npm = str(Path(node).resolve(strict=True)), str(Path(npm).resolve(strict=True))
        cli = studio / "node_modules/vitest/vitest.mjs"
        probe = repo / "tooling/vite_editor_security_probe.mjs"
        report["tools"] = {name: {"path": str(path), **digest(Path(path))}
                           for name, path in (("node", node), ("npm", npm), ("vitest_cli", cli), ("security_probe", probe))}
        env = dict(os.environ)
        for variable in PROCESS_ENVIRONMENT_KEYS:
            env.pop(variable, None)
        # An existing output cannot be mistaken for a build of this source.
        dist = studio / "dist"
        require(not dist.exists(), "fresh production build output required")
        commands = (
            ("security-probe", [node, str(probe)], 60),
            ("vitest", [node, str(cli), "run", *(name for name, _ in units),
                        "--retry=0", "--reporter=json", "--outputFile=" + str(output / "vitest.json")], 300),
            ("build", [npm, "run", "build"], 600),
        )
        for label, argv, timeout in commands:
            command = execute(argv, studio, env, output, label, timeout)
            report["commands"].append({"label": label, **command})
            require_observed_command(command)
            require(command["exit_code"] == 0 and command.get("diagnostic_custody") == "DIRECT_PROCESS_EXIT_OBSERVED"
                    and not command.get("observation"), "selected unit/build command incomplete: " + label)
            if label == "vitest":
                original, document = browser_report_original(output / "vitest.json")
                report["vitest_original"] = original
                report["vitest"] = validate_creative_production_units(document, studio, selection)
        report["source_after"] = source_observation(repo, source_scope)
        require(report["source_after"] == before, "source changed during selected unit/build execution")
        for identity in report["tools"].values():
            require(digest(Path(identity["path"])) == {key: identity[key] for key in ("bytes", "sha256")},
                    "selected unit/build executable or probe changed")
        require((dist / "index.html").is_file(), "production build index missing")
        files = {}
        for path in sorted(dist.rglob("*")):
            archive_ancestors(path)
            if path.is_file():
                require(len(files) < 2000 and path.stat().st_size <= 64 * 1024 * 1024,
                        "production build artifact bound exceeded")
                files[path.relative_to(dist).as_posix()] = digest(path)
        require(any(name.endswith(".js") for name in files), "production build JavaScript missing")
        report["production_build"] = {"status": "PASS", "files": files,
                                       "scope": "BUILD_OUTPUT_ONLY; NO_BROWSER_OR_SERVICE_EXECUTION"}
        retained = output / "production-build"
        shutil.copytree(dist, retained)
        require({path.relative_to(retained).as_posix(): digest(path) for path in sorted(retained.rglob("*"))
                 if path.is_file()} == files, "retained production build differs")
        report["status"] = "PASS"
    except UnknownObservation as error:
        report.update(status="UNKNOWN", error=str(error), unsealed_command=error.command,
                      terminal_custody="UNSEALED_NO_FURTHER_COMMANDS")
    except Exception as error:
        report["error_type"], report["error"] = type(error).__name__, str(error)
    write_new(output / "receipt.json", report)
    print(json.dumps({"status": report["status"], "receipt": str(output / "receipt.json")}))
    return 0 if report["status"] == "PASS" else 1


WORKFLOW_ROUTE_CASES = ('effect_grants::tests::creation_cannot_cross_workspace_registry_window_key_or_original_title',
 'effect_grants::tests::creation_expiry_and_previous_receipt_wire_shape_remain_explicit',
 'effect_grants::tests::creation_is_exactly_one_time_in_its_trusted_workspace_session',
 'effect_grants::tests::creation_scope_never_grants_an_existing_project_or_another_effect',
 'effect_grants::tests::creation_subject_preserves_structural_boundaries_and_rejects_invalid_identity',
 'effect_grants::tests::exact_grant_is_one_time',
 'effect_grants::tests::expired_grant_fails_closed_without_sleeping',
 'effect_grants::tests::grant_never_crosses_effect_boundaries_and_mismatch_consumes_it',
 'effect_grants::tests::project_generation_revision_and_subject_are_exact',
 'effect_grants::tests::project_import_is_the_only_supported_unscoped_grant',
 'effect_grants::tests::unsupported_external_effects_cannot_be_granted',
 'routed::ambiguous_route_reopens_and_refuses_same_key_without_second_effect',
 'routed::cancelled_route_retains_dispatch_intent_and_refuses_after_reopen',
 'routed::completed_route_replays_local_identity_and_retains_separate_broker_id',
 'routed::foreign_connection_and_invalid_identity_never_dispatch',
 'routed::invalid_grant_and_stale_scope_are_rejected_before_dispatch',
 'routed::known_native_rejection_preserves_code_without_provider_message',
 'routed::readonly_plan_uses_current_scope_without_a_mutation_grant',
 'routed::terminal_append_refusal_keeps_original_after_helper_return_without_public_disclosure',
 'workflow_wire_requires_original_request_identity_and_rejects_unknown_fields')


def validate_workflow_route_output(exit_code, output):
    """Admit the complete Linux route target, including its eleven grant tests."""
    require(type(exit_code) is int and exit_code == 0 and isinstance(output, str),
            "workflow route process must exit successfully")
    require(len(WORKFLOW_ROUTE_CASES) == 20 and len(set(WORKFLOW_ROUTE_CASES)) == 20,
            "workflow route inventory differs")
    rows = re.findall(r"^test (\S+) \.\.\. (\S+)[^\n]*$", output, re.MULTILINE)
    summaries = re.findall(r"^test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored; "
                           r"(\d+) measured; (\d+) filtered out;(?:[^\n]*)$", output, re.MULTILINE)
    require(len(rows) == 20 and len({name for name, _ in rows}) == 20
            and {name for name, _ in rows} == set(WORKFLOW_ROUTE_CASES)
            and all(status == "ok" for _, status in rows)
            and summaries == [("ok", "20", "0", "0", "0", "0")],
            "workflow route original closed result differs")
    return {"status": "PASS", "cases": sorted(name for name, _ in rows),
            "passed": 20, "ignored": 0,
            "scope": "NINE_REAL_ROUTE_CONTRACTS_AND_ELEVEN_GRANTS; BOUNDED_TEST_TRANSPORT_ONLY",
            "native_application_execution": "NOT_RUN", "formal_acceptance": False}


def run_workflow_route(repo, output):
    repo, output = repo.resolve(), output.resolve()
    require(repo.is_dir() and not output.exists() and not output.is_relative_to(repo),
            "fresh isolated workflow route output required")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "motionwright.workflow-route-ci/1", "status": "FAIL", "commands": [],
              "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
              "job": os.environ.get("GITHUB_JOB"), "native_render": "NOT_RUN", "installed_tauri": "NOT_RUN",
              "human_acceptance": False, "formal_acceptance": False}
    try:
        require(sys.platform.startswith("linux"), "closed workflow route verification requires Linux")
        before = source_observation(repo, "workflow-preview-browser")
        report["source_before"] = before
        write_new(output / "source-before.json", before)
        cargo, rustc = shutil.which("cargo"), shutil.which("rustc")
        require(cargo is not None and rustc is not None, "cargo and rustc required")
        # Preserve rustup's invocation basename while fingerprinting the resolved executable.
        tools = {name: {"invoked_path": str(Path(path).absolute()),
                        "path": str(Path(path).resolve(strict=True)),
                        **digest(Path(path).resolve(strict=True))}
                 for name, path in (("cargo", cargo), ("rustc", rustc))}
        report["tools"] = tools
        env = dict(os.environ)
        for variable in PROCESS_ENVIRONMENT_KEYS:
            env.pop(variable, None)
        env.update(CARGO_TERM_COLOR="never", RUST_BACKTRACE="0")
        commands = (("rustc-identity", [rustc, "-Vv"], 60),
                    ("cargo-identity", [cargo, "-V"], 60),
                    ("route-contracts", [cargo, "test", "--locked", "-p", "motionwright-native",
                                         "--test", "desktop_workflow_route", "--", "--test-threads=1"], 1200),
                    ("desktop-check", [cargo, "check", "--locked", "-p", "motionwright-desktop"], 1200))
        for label, argv, timeout in commands:
            command = execute(argv, repo, env, output, label, timeout)
            report["commands"].append({"label": label, **command})
            require_observed_command(command)
            require(command["exit_code"] == 0, "workflow route command failed: " + label)
            if label == "route-contracts":
                path = output / (label + ".stdout.log")
                require(digest(path) == {key: command["stdout"][key] for key in ("bytes", "sha256")},
                        "original workflow route log changed")
                report["route_contracts"] = validate_workflow_route_output(
                    command["exit_code"], path.read_text(encoding="utf-8"))
        report["source_after"] = source_observation(repo, "workflow-preview-browser")
        require(report["source_after"] == before, "workflow route source changed")
        for identity in tools.values():
            require(digest(Path(identity["path"])) == {key: identity[key] for key in ("bytes", "sha256")},
                    "workflow route executable changed")
        report.update(status="PASS", desktop_shell="COMPILED_ONLY_NOT_LAUNCHED")
    except UnknownObservation as error:
        report.update(status="UNKNOWN", error=str(error), unsealed_command=error.command,
                      terminal_custody="UNSEALED_NO_FURTHER_COMMANDS")
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error))
    write_new(output / "receipt.json", report)
    print(json.dumps({"status": report["status"], "receipt": str(output / "receipt.json")}))
    return 0 if report["status"] == "PASS" else 1


def workflow_route_main(arguments):
    parser = argparse.ArgumentParser(description="Verify the exact desktop workflow route and shell compile.")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(arguments)
    return run_workflow_route(args.repo, args.output)


def unit_build_main(arguments):
    parser = argparse.ArgumentParser(description="Verify an exact selected unit inventory and production build.")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selection", choices=("creative-production", "review", "workflow-preview", "branch-claim"),
                        default="creative-production")
    args = parser.parse_args(arguments)
    return run_creative_production_units(args.repo, args.output, args.selection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--selection", default="all", choices=["all", *SELECTION_GROUPS, *(row[0] for row in SELECTIONS), *BROWSER_SCOPES])
    args = parser.parse_args()
    if is_browser_scope(args.selection):
        return run_browser_selection(args.repo, args.output, args.target, args.selection)
    selections = selected_selections(args.selection)
    repo, output, target = (p.resolve() for p in (args.repo, args.output, args.target))
    require(repo.is_dir(), "repository missing")
    require(not output.exists() and not target.exists(), "fresh evidence and build directories required")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "motionwright.canvas-service-ci/1", "status": "FAIL",
              "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
              "job": os.environ.get("GITHUB_JOB"), "selections": [],
              "selection_plan": selection_plan(args.selection),
              "installed_tauri": "NOT_RUN", "native_render": "NOT_RUN", "human_acceptance": "NOT_RUN"}
    try:
        before = source_observation(repo)
        report["source_before"] = before
        write_new(output / "source-before.json", before)
        cargo, node = shutil.which("cargo"), shutil.which("node")
        require(cargo is not None and node is not None, "cargo and node are required")
        report["cargo"], report["node"] = observed_tool(Path(cargo)), observed_tool(Path(node))
        cargo, node = report["cargo"]["path"], report["node"]["path"]
        report["target_directory"] = str(target)
        env = dict(os.environ)
        for variable in PROCESS_ENVIRONMENT_KEYS:
            env.pop(variable, None)
        env["CARGO_TARGET_DIR"] = str(target)
        build = execute([cargo, "build", "--locked", "-p", "motionwright-service",
                         "--example", "studio_review_session"], repo, env, output, "build", 1200)
        report["build"] = build
        require_observed_command(build)
        require(build["exit_code"] == 0, "real service build failed")
        binary = target / "debug/examples/studio_review_session"
        if os.name == "nt":
            binary = binary.with_suffix(".exe")
        report["service_binary"] = {"path": str(binary), **digest(binary)}
        retained_binary = output / ("executed-studio-service.exe" if os.name == "nt" else "executed-studio-service")
        shutil.copyfile(binary, retained_binary)
        report["retained_service_binary"] = {"path": retained_binary.name, **digest(retained_binary)}
        require(digest(retained_binary) == digest(binary), "retained service binary differs")
        require(source_observation(repo) == before, "source changed during service compilation")
        cli = repo / "apps/studio/node_modules/@playwright/test/cli.js"
        report["playwright_cli"] = digest(cli)
        for label, filename, titles in selections:
            case_env = dict(env)
            stores = output / (label + "-stores")
            stores.mkdir()
            case_env["MOTIONWRIGHT_SERVICE_TEST_BIN"] = str(binary)
            case_env["MOTIONWRIGHT_SERVICE_TEST_TMPDIR"] = str(stores)
            case_env["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(output / (label + ".json"))
            argv = service_command(node, cli, output, (label, filename, titles))
            command = execute(argv, repo / "apps/studio", case_env, output, label,
                              SELECTION_TIMEOUTS[label])
            selection = {"label": label, "expected_tests": len(titles), "command": command, "status": "FAIL"}
            report["selections"].append(selection)
            require_observed_command(command)
            try:
                result_path = output / (label + ".json")
                selection["original_report"] = digest(result_path)
                require(result_path.stat().st_size <= 64 * 1024 * 1024, "report exceeds bound")
                document = json.loads(result_path.read_bytes())
                selection["verified"] = validate_report(document, filename, titles)
                require(command["exit_code"] == 0, "selected process did not exit zero")
                selection["status"] = "PASS"
            except Exception as error:
                selection["error"] = str(error)
            if command["exit_code"] is None:
                break
        report["source_after"] = source_observation(repo)
        require(report["source_after"] == before, "source changed during browser execution")
        require(digest(binary) == {k: report["service_binary"][k] for k in ("bytes", "sha256")},
                "service executable changed")
        require(digest(cli) == report["playwright_cli"], "Playwright CLI changed")
        for name in ("cargo", "node"):
            require(observed_tool(Path(report[name]["path"])) == report[name], "service tool changed: " + name)
        require(digest(retained_binary) == digest(binary), "retained service binary changed")
        require(len(report["selections"]) == len(selections)
                and all(x["status"] == "PASS" for x in report["selections"]), "required selection failed")
        report["status"] = "PASS"
    except UnknownObservation as error:
        report.update(status="UNKNOWN", error=str(error), unsealed_command=error.command,
                      terminal_custody="UNSEALED_NO_FURTHER_COMMANDS")
    except Exception as error:
        report["error"] = str(error)
    write_new(output / "receipt.json", report)
    print(json.dumps({"status": report["status"],
                      "selected_tests": sum(x["expected_tests"] for x in report["selections"]),
                      "receipt": str(output / "receipt.json")}))
    return 0 if report["status"] == "PASS" else 1





def validate_claim_source_output(exit_code, output, package):
    """Require each original domain/service assertion, not just success totals."""
    require(package in ("domain", "service"), "unknown Claim contract target")
    cases = CLAIM_SOURCE_RUST_CASES[package]
    expected = 7 if package == "domain" else 8
    require(isinstance(cases, tuple) and len(cases) == len(set(cases)) == expected,
            "Claim contract inventory differs")
    require(type(exit_code) is int and exit_code == 0 and isinstance(output, str),
            "Claim contract process must exit successfully")
    rows = re.findall(r"^test (\S+) \.\.\. (\S+)[^\n]*$", output, re.MULTILINE)
    summaries = re.findall(r"^test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored; "
                           r"(\d+) measured; (\d+) filtered out;(?:[^\n]*)$", output, re.MULTILINE)
    require(len(rows) == len({name for name, _ in rows}) == expected
            and {name for name, _ in rows} == set(cases)
            and all(status == "ok" for _, status in rows)
            and summaries == [("ok", str(expected), "0", "0", "0", "0")],
            "Claim original closed result differs")
    return {"status": "PASS", "package": package, "cases": sorted(cases),
            "passed": expected, "ignored": 0, "formal_acceptance": False}


def run_claim_source_contracts(repo, output):
    repo, output = repo.resolve(), output.resolve()
    require(repo.is_dir() and not output.exists() and not output.is_relative_to(repo),
            "fresh isolated Claim contract output required")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "motionwright.claim-source-contract-ci/1", "status": "FAIL", "commands": [],
              "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
              "job": os.environ.get("GITHUB_JOB"), "native_render": "NOT_RUN", "installed_tauri": "NOT_RUN",
              "human_acceptance": False, "formal_acceptance": False}
    try:
        require(sys.platform.startswith("linux"), "closed Claim contract verification requires Linux")
        before = source_observation(repo)
        report["source_before"] = before
        write_new(output / "source-before.json", before)
        cargo, rustc = shutil.which("cargo"), shutil.which("rustc")
        require(cargo is not None and rustc is not None, "cargo and rustc required")
        # Preserve rustup's invocation basename while fingerprinting the resolved executable.
        tools = {name: {"invoked_path": str(Path(path).absolute()),
                        "path": str(Path(path).resolve(strict=True)),
                        **digest(Path(path).resolve(strict=True))}
                 for name, path in (("cargo", cargo), ("rustc", rustc))}
        report["tools"] = tools
        env = dict(os.environ)
        for variable in PROCESS_ENVIRONMENT_KEYS:
            env.pop(variable, None)
        env.update(CARGO_TERM_COLOR="never", RUST_BACKTRACE="0")
        commands = (("rustc-identity", [rustc, "-Vv"], 60),
                    ("cargo-identity", [cargo, "-V"], 60),
                    ("domain", [cargo, "test", "--locked", "-p", "motionwright-domain",
                                "--test", "claim_source_edit", "--", "--test-threads=1"], 1200),
                    ("service", [cargo, "test", "--locked", "-p", "motionwright-service",
                                 "--test", "claim_source_edit", "--", "--test-threads=1"], 1200),
                    ("native-consumer-check", [cargo, "check", "--locked", "-p", "motionwright-native",
                                               "--all-targets"], 1200))
        for label, argv, timeout in commands:
            command = execute(argv, repo, env, output, label, timeout)
            report["commands"].append({"label": label, **command})
            require_observed_command(command)
            require(command["exit_code"] == 0, "Claim contract command failed: " + label)
            if label in ("domain", "service"):
                path = output / (label + ".stdout.log")
                require(digest(path) == {key: command["stdout"][key] for key in ("bytes", "sha256")},
                        "original Claim contract log changed")
                report[label] = validate_claim_source_output(
                    command["exit_code"], path.read_text(encoding="utf-8"), label)
        report["source_after"] = source_observation(repo)
        require(report["source_after"] == before, "Claim contract source changed")
        for identity in tools.values():
            require(digest(Path(identity["path"])) == {key: identity[key] for key in ("bytes", "sha256")},
                    "Claim contract executable changed")
        report.update(status="PASS", native_consumer="COMPILED_ONLY_NOT_EXECUTED")
    except UnknownObservation as error:
        report.update(status="UNKNOWN", error=str(error), unsealed_command=error.command,
                      terminal_custody="UNSEALED_NO_FURTHER_COMMANDS")
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error))
    write_new(output / "receipt.json", report)
    print(json.dumps({"status": report["status"], "receipt": str(output / "receipt.json")}))
    return 0 if report["status"] == "PASS" else 1

def claim_source_main(arguments):
    parser = argparse.ArgumentParser(description="Verify exact Claim domain/service contracts and native consumer compilation.")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(arguments)
    return run_claim_source_contracts(args.repo, args.output)



# This closed profile is additive; historical selectors retain their own scope.
STUDIO68_SOURCE = "278f6777b999275e1d7c69e46e91a6439c172e90"
STUDIO68_TREE = "c6a1eea4ae15e6b041b18e1880dbec97975d0c08"
STUDIO68_SDK = "01d4a99a8473273fda5c61b7220d6da1bee94786"
STUDIO68_SCOPE = "studio-service-68"
STUDIO68_WORKFLOW = ".github/workflows/studio-service-verification.yml"
# The eight admitted phase caps total 18660 seconds. Preparation and transition
# custody consume the remaining 1140 seconds of the same original deadline.
STUDIO68_PHASE_SECONDS = {"product_g0": 1200, "dependencies": 900, "clippy": 900,
                          "build": 1200, "list": 120, "execution": 8460,
                          "readback": 5400, "retention": 480}
STUDIO68_SECONDS = 19800
STUDIO68_PREPARATION_SECONDS = 300
STUDIO68_CLEANUP_RESERVE = 30
STUDIO68_FILES = ("branch-authoring-service.spec.ts", "brand-authoring-service.spec.ts",
                  "component-distillation-service.spec.ts", "review-service.spec.ts",
                  "production-design-service.spec.ts")
STUDIO68_CASES = (('branch-authoring-service.spec.ts',
  'causal: a rejected branch creation preserves the exact authored name',
  104,
  1,
  '90d81b47503d2bbf2302-03dd744d82a4d188a156'),
 ('branch-authoring-service.spec.ts',
  'causal: a late original branch reply preserves a later name',
  132,
  1,
  '90d81b47503d2bbf2302-69a8ce3e7f07f59c290a'),
 ('branch-authoring-service.spec.ts',
  'causal: an unsaved branch name survives App navigation and reload without a mutation',
  144,
  1,
  '90d81b47503d2bbf2302-837b206a282f92967e52'),
 ('branch-authoring-service.spec.ts',
  'one explicit name creates one editable branch through SQLite and a new service process',
  156,
  1,
  '90d81b47503d2bbf2302-7fcc1b9afdac0ec5f3cd'),
 ('branch-authoring-service.spec.ts',
  'UTF-8 branch limit keeps invalid input without sending it',
  178,
  1,
  '90d81b47503d2bbf2302-07269cc82a6392981ee0'),
 ('branch-authoring-service.spec.ts',
  'silent branch recovery bytes loss blocks creation until exact readback',
  189,
  3,
  '90d81b47503d2bbf2302-9183ca5389583e9959f2'),
 ('branch-authoring-service.spec.ts',
  'silent branch recovery pointer loss blocks creation until exact readback',
  189,
  3,
  '90d81b47503d2bbf2302-7b9660954a25d7d77b16'),
 ('branch-authoring-service.spec.ts',
  'a committed but lost branch reply survives reopen and explicit inspection without replay',
  212,
  1,
  '90d81b47503d2bbf2302-3c96405ee60004b548fc'),
 ('branch-authoring-service.spec.ts',
  'a later A-to-B-to-A branch name stays editable after its original reply and reload',
  252,
  1,
  '90d81b47503d2bbf2302-e8acd27fe1e6e8050909'),
 ('branch-authoring-service.spec.ts',
  'restoring an unconfirmed branch preserves both copies after silent pointer failure',
  268,
  3,
  '90d81b47503d2bbf2302-395b85b46216f36d0b14'),
 ('branch-authoring-service.spec.ts',
  'restoring an unconfirmed branch preserves both copies after throw pointer failure',
  268,
  3,
  '90d81b47503d2bbf2302-a997a89b17615cfe9218'),
 ('branch-authoring-service.spec.ts',
  'restoring an unconfirmed branch preserves both copies after write-then-throw pointer failure',
  268,
  3,
  '90d81b47503d2bbf2302-477df73200843765aae1'),
 ('branch-authoring-service.spec.ts',
  'an ordinary branch name survives a failed first pointer read before one explicit creation',
  432,
  3,
  '90d81b47503d2bbf2302-f9d90bc2f8a9a17f4812'),
 ('branch-authoring-service.spec.ts',
  'an UNKNOWN branch and a deliberately cleared name survive failed first pointer read without replay',
  475,
  3,
  '90d81b47503d2bbf2302-455f5cb85b33c39db7c0'),
 ('branch-authoring-service.spec.ts',
  'an ordinary branch name survives a failed first copy read before one explicit creation',
  432,
  3,
  '90d81b47503d2bbf2302-32dcb94264ce18bc3d5c'),
 ('branch-authoring-service.spec.ts',
  'an UNKNOWN branch and a deliberately cleared name survive failed first copy read without replay',
  475,
  3,
  '90d81b47503d2bbf2302-9e3d078d772fde68ad7d'),
 ('branch-authoring-service.spec.ts',
  'a failed initial pointer read can recover an empty session without discarding newly typed input',
  534,
  1,
  '90d81b47503d2bbf2302-74b8225d18c9fbf996b5'),
 ('branch-authoring-service.spec.ts',
  'an unchanged SQLite branch base stays UNKNOWN until a real peer advances it, preserving later input without replay',
  573,
  1,
  '90d81b47503d2bbf2302-47a82a0a62a6289bc25e'),
 ('brand-authoring-service.spec.ts',
  'Brand policy, taste and decision saves preserve other raw fields through actual App navigation and SQLite restart',
  255,
  1,
  '06cc3d3c07c1041c9a27-51f7b6f623b36767da68'),
 ('brand-authoring-service.spec.ts',
  'a real taste commit with a lost original reply stays UNKNOWN through later typing, reload and saved-history '
  'inspection',
  324,
  1,
  '06cc3d3c07c1041c9a27-9465e47deb86aa6a2397'),
 ('brand-authoring-service.spec.ts',
  'a silently dropped pending brand copy issues zero grants and Retry only confirms bytes before a separate Save',
  391,
  1,
  '06cc3d3c07c1041c9a27-849b356225084e762e2b'),
 ('brand-authoring-service.spec.ts',
  'a failed original UNKNOWN recovery read cannot be escaped by restoring a different ordinary copy',
  441,
  1,
  '06cc3d3c07c1041c9a27-a68edfe56d68f13b33ef'),
 ('brand-authoring-service.spec.ts',
  'a reviewed selected campaign changes the real semantic Hero while preserving other scenes and proposal bytes '
  'through SQLite restart',
  515,
  1,
  '06cc3d3c07c1041c9a27-e1c75d2eccd5f3dd37b8'),
 ('brand-authoring-service.spec.ts',
  'Brand governance retains its first observed UNKNOWN bytes when a failed recovery read is followed by a same-writer '
  'replacement',
  610,
  3,
  '06cc3d3c07c1041c9a27-6d676bb999dd2eed0f61'),
 ('brand-authoring-service.spec.ts',
  'Brand campaign retains its first observed UNKNOWN bytes when a failed recovery read is followed by a same-writer '
  'replacement',
  610,
  3,
  '06cc3d3c07c1041c9a27-53c2351f2f0d5225d6b3'),
 ('brand-authoring-service.spec.ts',
  'an explicitly cleared brand rule issues zero grants and remains empty after a real SQLite restart',
  724,
  1,
  '06cc3d3c07c1041c9a27-bd6593450d22b9821a8a'),
 ('component-distillation-service.spec.ts',
  'Distillation actual App restores author input after its first pointer read fails and commits frozen samples plus '
  'independent review through SQLite restart',
  227,
  1,
  '980e3c140a1ad4daf80a-a0a5921a0ff77decc2e8'),
 ('component-distillation-service.spec.ts',
  'Distillation actual original proposal receipt preserves later author text through App remount',
  273,
  1,
  '980e3c140a1ad4daf80a-609b0fd344367726b15c'),
 ('component-distillation-service.spec.ts',
  "Distillation actual review receipt cannot erase another proposal's authoring buffer",
  290,
  1,
  '980e3c140a1ad4daf80a-c3beec29dbc70240706f'),
 ('component-distillation-service.spec.ts',
  'Distillation later rationale survives its actual immutable review receipt and cannot rewrite the saved review',
  308,
  1,
  '980e3c140a1ad4daf80a-d39e7c6c2f37f6583a1b'),
 ('component-distillation-service.spec.ts',
  'Distillation author actual reply loss retains UNKNOWN and later cleared input after a first pointer read failure, '
  'reload and current-history review',
  326,
  3,
  '980e3c140a1ad4daf80a-b4e89ac7373d28743f60'),
 ('component-distillation-service.spec.ts',
  'Distillation review actual reply loss retains UNKNOWN and later cleared input after a first pointer read failure, '
  'reload and current-history review',
  326,
  3,
  '980e3c140a1ad4daf80a-0ab52fca11b2408cb9bc'),
 ('component-distillation-service.spec.ts',
  'Distillation author pre-dispatch transport interruption keeps UNKNOWN at the saved base until a real peer advances '
  'it, without replay',
  381,
  3,
  '980e3c140a1ad4daf80a-9d4ab473013aa402ad0d'),
 ('component-distillation-service.spec.ts',
  'Distillation review pre-dispatch transport interruption keeps UNKNOWN at the saved base until a real peer advances '
  'it, without replay',
  381,
  3,
  '980e3c140a1ad4daf80a-720931aa5dc13550890a'),
 ('component-distillation-service.spec.ts',
  'Distillation data recovery failure sends no actual grant or write; healthy explicit retry retains source choices',
  479,
  3,
  '980e3c140a1ad4daf80a-91ce109d25991c55fec6'),
 ('component-distillation-service.spec.ts',
  'Distillation pointer recovery failure sends no actual grant or write; healthy explicit retry retains source choices',
  479,
  3,
  '980e3c140a1ad4daf80a-2c027117de6f56c1004b'),
 ('component-distillation-service.spec.ts',
  'Distillation author retains its first observed UNKNOWN bytes when a failed recovery read is followed by a '
  'same-writer replacement',
  507,
  3,
  '980e3c140a1ad4daf80a-bb0947a50f156b5ef4bc'),
 ('component-distillation-service.spec.ts',
  'Distillation review retains its first observed UNKNOWN bytes when a failed recovery read is followed by a '
  'same-writer replacement',
  507,
  3,
  '980e3c140a1ad4daf80a-14367a4c72c616cf5762'),
 ('production-design-service.spec.ts',
  'Production plan raw drafts, claim references and content-bound approval survive actual App navigation and SQLite '
  'restart',
  132,
  1,
  '67037f4586e4a2ca8702-8ad929248f0fd87ebc6d'),
 ('production-design-service.spec.ts',
  'Production Hero updates retain editable node identities and a human override; detach preserves objects across '
  'SQLite restart',
  172,
  1,
  '67037f4586e4a2ca8702-4ed7cb9d8f75baba6f1c'),
 ('production-design-service.spec.ts',
  'a held original Production component receipt preserves later typing across actual App remount and a separate save',
  206,
  3,
  '67037f4586e4a2ca8702-a34558b890b00165d4b3'),
 ('production-design-service.spec.ts',
  'a held original Production plan receipt preserves later typing across actual App remount and a separate save',
  206,
  3,
  '67037f4586e4a2ca8702-5fc397580d6aa396fb9d'),
 ('production-design-service.spec.ts',
  'an unknown Production plan committed result survives reload without replay and needs a fresh history review',
  239,
  3,
  '67037f4586e4a2ca8702-1f3efa40f9876dca293f'),
 ('production-design-service.spec.ts',
  'an unknown Production plan matching-peer result survives reload without replay and needs a fresh history review',
  239,
  3,
  '67037f4586e4a2ca8702-9c2e3445611ec4a8fba7'),
 ('production-design-service.spec.ts',
  'Production data recovery failure sends no real service grant or change; explicit healthy retry persists the '
  'retained draft',
  277,
  3,
  '67037f4586e4a2ca8702-a0802d6a3dfe9da72ff3'),
 ('production-design-service.spec.ts',
  'Production pointer recovery failure sends no real service grant or change; explicit healthy retry persists the '
  'retained draft',
  277,
  3,
  '67037f4586e4a2ca8702-8c9ecdfab5d436769aca'),
 ('production-design-service.spec.ts',
  'Production retries an initial pointer read for ordinary input without replacing raw intent or replaying a real '
  'operation',
  344,
  3,
  '67037f4586e4a2ca8702-394bae365fdaaf21214f'),
 ('production-design-service.spec.ts',
  'Production retries an initial pointer read for UNKNOWN input without replacing raw intent or replaying a real '
  'operation',
  344,
  3,
  '67037f4586e4a2ca8702-ebd4aded24cc5d685a18'),
 ('production-design-service.spec.ts',
  'Production retries an initial copy read for ordinary input without replacing raw intent or replaying a real '
  'operation',
  344,
  3,
  '67037f4586e4a2ca8702-90b4754b9666091fb1d0'),
 ('production-design-service.spec.ts',
  'Production retries an initial copy read for UNKNOWN input without replacing raw intent or replaying a real '
  'operation',
  344,
  3,
  '67037f4586e4a2ca8702-0be19c85e2f6ba251fb7'),
 ('production-design-service.spec.ts',
  'Production confirms an empty pointer after an initial read failure while retaining the input typed during the '
  'blocker',
  445,
  1,
  '67037f4586e4a2ca8702-f03a03c37c7e24a57882'),
 ('production-design-service.spec.ts',
  'Production component retains its first observed UNKNOWN bytes when a failed recovery read is followed by a '
  'same-writer replacement',
  476,
  3,
  '67037f4586e4a2ca8702-9fce096231ad683cfeb6'),
 ('production-design-service.spec.ts',
  'Production plan retains its first observed UNKNOWN bytes when a failed recovery read is followed by a same-writer '
  'replacement',
  476,
  3,
  '67037f4586e4a2ca8702-ff2419213ef0ab7dd644'),
 ('production-design-service.spec.ts',
  'opaque source attachment raw input and a silent pointer failure preserve the existing asset before one explicit '
  'SQLite attachment',
  595,
  1,
  '67037f4586e4a2ca8702-39d8e0f11784333f76cb'),
 ('production-design-service.spec.ts',
  'opaque source attachment late real ACK retains two scene drafts and permits only a separate explicit later '
  'attachment',
  644,
  1,
  '67037f4586e4a2ca8702-de940abf33c28468485b'),
 ('production-design-service.spec.ts',
  'opaque source attachment lost committed service reply preserves the exact submitted capsule and later cleared input '
  'without replay',
  683,
  1,
  '67037f4586e4a2ca8702-565026398cb1219ce997'),
 ('production-design-service.spec.ts',
  'opaque source attachment pre-dispatch delivery interruption stays UNKNOWN on N until a real peer advances SQLite, '
  'with no original replay',
  723,
  1,
  '67037f4586e4a2ca8702-dd1595820641a3998c40'),
 ('review-service.spec.ts',
  'review raw range survives actual App navigation and reload before one exact SQLite commit and restart',
  132,
  1,
  '0f11b70400fd2e6eaf8a-d7a91be5cd90ec57d924'),
 ('review-service.spec.ts',
  'a held original review reply preserves later input before a distinct granted save',
  162,
  1,
  '0f11b70400fd2e6eaf8a-dda45573a1af59fa6501'),
 ('review-service.spec.ts',
  'a committed but lost review reply remains UNKNOWN through explicit saved-history inspection and a separate intent',
  181,
  1,
  '0f11b70400fd2e6eaf8a-cdad8227f658df285515'),
 ('review-service.spec.ts',
  'ABA author input survives lost review reply, inspection, separate save and SQLite restart',
  217,
  1,
  '0f11b70400fd2e6eaf8a-01f395dac753f606e235'),
 ('review-service.spec.ts',
  'denied review custody prevents any SQLite mutation until a confirmed copy and explicit save',
  257,
  1,
  '0f11b70400fd2e6eaf8a-5b269e07dc56c2b6213f'),
 ('review-service.spec.ts',
  'an original resolution keeps a later raw reason through actual reopen and new SQLite acknowledgement',
  280,
  1,
  '0f11b70400fd2e6eaf8a-b230662f6c6cc8a4565a'),
 ('review-service.spec.ts',
  'an actual saved alternative retains raw rationale and records only an open manual review',
  300,
  1,
  '0f11b70400fd2e6eaf8a-82a1d62736a84b080146'),
 ('review-service.spec.ts',
  'selected review deliverable, language and rational range persist through actual App reload and SQLite restart',
  326,
  1,
  '0f11b70400fd2e6eaf8a-058b23f0cb72848d5459'),
 ('review-service.spec.ts',
  'later explicit empty review profile survives a held original reply and saves separately to SQLite',
  366,
  1,
  '0f11b70400fd2e6eaf8a-488c23f83c87d066b732'),
 ('review-service.spec.ts',
  'lost reply keeps original selected review profile UNKNOWN while a later empty selection survives actual SQLite '
  'recovery',
  393,
  1,
  '0f11b70400fd2e6eaf8a-05217b4a6100f079e08a'),
 ('review-service.spec.ts',
  'pre-dispatch review transport interruption keeps the saved base UNKNOWN until an independent SQLite peer advances '
  'it without replay',
  440,
  1,
  '0f11b70400fd2e6eaf8a-ee41ca09d4b06f0c2fd7'))
STUDIO68_PRODUCT_PINS = {'Cargo.lock': '0c6a330de85c99f0913f4fc9e1b2af794d8cae239085952dd6400a017c67205b',
 'Cargo.toml': '8c63f973bcbc43f45c26f074259f36aa8b3a46595657d34eecf86093cddca2c9',
 'SOURCE_LOCK.json': 'aeb60e60036d29ad96abec0dc7acbcf3302a4411c16a7ea37c4b3c58c36e0b4f',
 'apps/studio/package-lock.json': 'bf7e12b36faff3d553033f903560070974774700f404ff6704ed0942094bc994',
 'apps/studio/package.json': '428526cf98577d3c960a1f13731cf89a1c1c19cb7a4e653a420d1e832c75c9b4',
 'apps/studio/playwright.config.ts': 'facf22ed7a17d6150f0bf78a9eeb4e86daed7bd746b7de59dfb1b84ea057d38c',
 'apps/studio/tests/branch-authoring-service.spec.ts': '17646fc6324af8a9da0560fe7f71cc21c4a8b56f270c6c30498a585011f4838d',
 'apps/studio/tests/brand-authoring-service.spec.ts': '0917b454baba213fd75525fc2d6d36ec39288f7e0fa81ad8ae1031fe7baa52b7',
 'apps/studio/tests/component-distillation-service.spec.ts': '64c7f67179644ccd91480e4bdfd77e260e6a0c6ec8f295803aa16e04f0872d51',
 'apps/studio/tests/production-design-service.spec.ts': '019cc59f2fc2ada5a14f85f45525677715528ae32d8c465aec0967e41d9ecdcb',
 'apps/studio/tests/review-service.spec.ts': 'b8c1597384c7bf9de4c3d8f38c6b7839ee3ed30c4fd52e2d4583f34ca2d6b168',
 'apps/studio/tests/support/scopedChangeService.ts': '2fba83ace30c032e11f3c108203c0d7a0e98821d79d081717281194108138f1d',
 'apps/studio/vite.config.ts': '6743b496e0276b2e7197330d8c78c3782fb1eaa769d55d8b59c98ef24255294a',
 'crates/service/Cargo.toml': '029581d64961e5add8afe5ca83bb197dcee15968f6abe2e8a9e3f6ed3b316333',
 'crates/service/examples/studio_review_session.rs': '35cc60ab2bf1c3bbe4dc1399ad59a7d7117ea2061ca6bf9f4b6b74a9cd363c98',
 'rust-toolchain.toml': '887f9be066a15585a2c583578e84b0fcb541126d81546276bad3d2ff00d61167'}
STUDIO68_HELPER_PINS = {
    "ci_storage_apply_recovery": "9aea9a41d365a36fd1abae86ec5a0974236d62d9781a372eace0578ef2da1520",
    "ci_foundation_causal": "1d31c58aa7d63c927ceb2518282a433e0f728fc125da8978fb635b18cff354c2",
    "ci_sdk_native_source": "65bcda73f16423ebb644a0979bca3a7927f8e7337c124702874bce51adcf8127",
}
STUDIO68_READER_PINS = {
    "tooling/service_readback/service_archive_readback_r3.py": "71319a62dab38779fd29a96d7b88c7e32ddb24a770a771e8db14bee6f30f73c3",
    "tooling/service_readback/test_readback_r3.py": "b457817f1e05821ef84b1c7c78cca0a3b825264c6cab470747bbd9b73188e855",
}
STUDIO68_POLICIES = ("source_policy.py", "public_surface_policy.py",
                     "acceptance_policy.py", "creative_status_policy.py")
STUDIO68_RUNNER_SHA256 = "b7d39ad4c94be70abcac75880f92d00cf3c8d49eb0198b404a962ce5ab1a712f"

def studio68_context(environment, product_source, platform=None):
    platform = sys.platform if platform is None else platform
    require(platform == "linux" and environment.get("RUNNER_OS") == "Linux"
            and environment.get("RUNNER_ARCH") == "X64", "Studio68 requires Linux x64")
    require(environment.get("GITHUB_ACTIONS") == "true"
            and environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and environment.get("GITHUB_REPOSITORY") == "seradotcom/motionwright"
            and environment.get("GITHUB_JOB") == STUDIO68_SCOPE,
            "Studio68 requires its explicit workflow job")
    require(product_source == STUDIO68_SOURCE, "Studio68 product source is not reviewed")
    require(all(re.fullmatch(r"[0-9a-f]{40}", environment.get(k, ""))
                for k in ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA"))
            and environment["GITHUB_SHA"] == environment["GITHUB_WORKFLOW_SHA"]
            and environment["GITHUB_SHA"] != product_source,
            "Studio68 gate and product must have separate exact source identities")
    require(environment.get("GITHUB_RUN_ATTEMPT") == "1"
            and re.fullmatch(r"[1-9][0-9]*", environment.get("GITHUB_RUN_ID", "")),
            "Studio68 permits the first observed attempt only; no replay")
    require(environment.get("GITHUB_WORKFLOW_REF", "").startswith(
        "seradotcom/motionwright/" + STUDIO68_WORKFLOW + "@"), "Studio68 workflow identity")
    keys = ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_REPOSITORY",
            "GITHUB_JOB", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "RUNNER_OS", "RUNNER_ARCH")
    return {"gate_source_sha": environment["GITHUB_SHA"],
            "executed_product_source_sha": product_source, "github": {k: environment[k] for k in keys}}


def studio68_environment(environment, root):
    forbidden = {"RUSTC", "RUSTDOC", "RUSTFMT", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER",
                 "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTDOCFLAGS", "CARGO_ENCODED_RUSTDOCFLAGS",
                 "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "NODE_OPTIONS", "NODE_PATH",
                 "TS_NODE_PROJECT", "NPM_CONFIG_USERCONFIG", "npm_config_userconfig",
                 "CC", "CXX", "AR", "CFLAGS", "CXXFLAGS", "LDFLAGS"}
    bad = [k for k, v in environment.items() if v and (k in forbidden or k.startswith(
        ("GIT_", "SEMWRIGHT_", "MOTIONWRIGHT_", "CARGO_TARGET_", "CARGO_PROFILE_", "CARGO_ALIAS_")))]
    require(not bad, "Studio68 inherited execution override: " + ",".join(sorted(bad)))
    value = dict(environment)
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT"):
        value.pop(key, None)
    value.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", CARGO_BUILD_JOBS="1",
                 CARGO_HOME=str(root / "cargo"), CARGO_TARGET_DIR=str(root / "target"),
                 NPM_CONFIG_CACHE=str(root / "npm"), PLAYWRIGHT_BROWSERS_PATH=str(root / "browsers"),
                 TMP=str(root / "tmp"), TEMP=str(root / "tmp"), TMPDIR=str(root / "tmp"),
                 XDG_CACHE_HOME=str(root / "xdg"), CI="true")
    return value


class Studio68Clock:
    def __init__(self, started_ns):
        import time
        now = time.monotonic_ns()
        require(type(started_ns) is int and 0 < started_ns <= now, "Studio68 original clock")
        self.started_ns, self.last_ns = started_ns, now
        self.deadline_ns = started_ns + STUDIO68_SECONDS * 1_000_000_000
        self.retention_started = False

    def remaining(self, cap, *, reserve=None):
        import time
        if reserve is None:
            reserve = STUDIO68_CLEANUP_RESERVE + (0 if self.retention_started else STUDIO68_PHASE_SECONDS["retention"])
        require(type(cap) is int and 0 < cap <= STUDIO68_SECONDS
                and type(reserve) is int and 0 <= reserve <= STUDIO68_CLEANUP_RESERVE + STUDIO68_PHASE_SECONDS["retention"],
                "Studio68 finite phase budget")
        now = time.monotonic_ns()
        require(now >= self.last_ns, "Studio68 clock regressed")
        self.last_ns = now
        remaining = (self.deadline_ns - now) / 1_000_000_000 - reserve
        require(remaining > 0, "Studio68 original deadline exhausted")
        return min(cap, remaining)

    def phase(self, seconds, *, retention=False):
        require(type(retention) is bool and not self.retention_started
                and (not retention or seconds == STUDIO68_PHASE_SECONDS["retention"]),
                "Studio68 retention is a single terminal phase")
        if retention:
            self.retention_started = True
        return Studio68Phase(self, seconds)


class Studio68Phase:
    def __init__(self, clock, seconds):
        import time
        self.clock = clock
        self.deadline_ns = time.monotonic_ns() + int(clock.remaining(seconds) * 1_000_000_000)

    def remaining(self, cap):
        import time
        remaining = (self.deadline_ns - time.monotonic_ns()) / 1_000_000_000
        require(remaining > 0, "Studio68 phase deadline exhausted")
        return min(remaining, self.clock.remaining(cap))


def studio68_original(path, limit):
    path = Path(path)
    archive_ancestors(path)
    before = archive_stat(path)
    require(stat.S_ISREG(before.st_mode) and before.st_size <= limit, "Studio68 original byte bound")
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(os.open(path, flags), "rb") as stream:
        require(archive_identity(os.fstat(stream.fileno())) == archive_identity(before),
                "Studio68 original changed before open")
        raw = stream.read(limit + 1)
        require(len(raw) == before.st_size and len(raw) <= limit
                and archive_identity(os.fstat(stream.fileno())) == archive_identity(before),
                "Studio68 original changed during read")
    require(archive_identity(archive_stat(path)) == archive_identity(before),
            "Studio68 original changed after read")
    return raw, {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def studio68_load_helpers(repo):
    import importlib.util
    import types
    package = sys.modules.get("tooling")
    require(package is None or list(getattr(package, "__path__", ())) == [str(repo / "tooling")],
            "Studio68 foreign tooling package already loaded")
    if package is None:
        package = types.ModuleType("tooling")
        package.__path__ = [str(repo / "tooling")]
        sys.modules["tooling"] = package
    for name, expected in STUDIO68_HELPER_PINS.items():
        path = repo / "tooling" / (name + ".py")
        raw, identity = studio68_original(path, 2 * 1024 * 1024)
        require(identity["sha256"] == expected, "Studio68 shared helper source changed: " + name)
        qualified = "tooling." + name
        require(qualified not in sys.modules, "Studio68 shared helper was preloaded")
        spec = importlib.util.spec_from_file_location(qualified, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        setattr(package, name, module)
        exec(compile(raw, str(path), "exec"), module.__dict__)
    return sys.modules["tooling.ci_sdk_native_source"]


def studio68_snapshot(repo, source, git, *, expected_tree=None, allow_node_modules=False, cargo_marker=False):
    require(re.fullmatch(r"[0-9a-f]{40}", source), "Studio68 full source SHA")
    require(git(repo, "rev-parse", "HEAD").decode().strip() == source, "Studio68 checkout SHA changed")
    tree = git(repo, "rev-parse", "HEAD^{tree}").decode().strip()
    require(expected_tree is None or tree == expected_tree, "Studio68 source tree changed")
    require(not git(repo, "diff", "--name-only", "HEAD", "--"), "Studio68 tracked source is dirty")
    generated = []
    for options in (("--others", "--exclude-standard"), ("--others", "--ignored", "--exclude-standard")):
        generated.extend(x.decode("utf-8") for x in git(repo, "ls-files", "-z", *options).split(b"\0") if x)
    require(all((allow_node_modules and x.startswith("apps/studio/node_modules/"))
                or (cargo_marker and x == ".cargo-ok") for x in generated),
            "Studio68 unbound checkout input outside the declared npm tree")
    listing = git(repo, "ls-tree", "-r", "-z", source)
    files, folded, total = {}, set(), 0
    for record in listing.split(b"\0"):
        if not record:
            continue
        header, encoded = record.split(b"\t", 1)
        mode, kind, oid = header.decode("ascii").split()
        name = encoded.decode("utf-8")
        require(mode in ("100644", "100755") and kind == "blob"
                and re.fullmatch(r"[0-9a-f]{40}", oid)
                and name and not name.startswith("/") and "\\" not in name
                and all(x not in ("", ".", "..", ".git") for x in name.split("/"))
                and name.casefold() not in folded and len(files) < 30000,
                "Studio68 source manifest contains an unsupported member")
        raw, identity = studio68_original(repo / name, 256 * 1024 * 1024)
        require(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == oid,
                "Studio68 working bytes differ from the exact Git blob: " + name)
        total += len(raw)
        require(total <= 1024 * 1024 * 1024, "Studio68 source closure byte bound")
        folded.add(name.casefold())
        files[name] = {"blob": oid, "mode": mode, **identity}
    require(files, "Studio68 source closure is empty")
    return {"sha": source, "tree": tree, "files": files, "bytes": total,
            "listing_sha256": hashlib.sha256(listing).hexdigest()}


def studio68_product_identity(snapshot):
    require(snapshot.get("sha") == STUDIO68_SOURCE and snapshot.get("tree") == STUDIO68_TREE,
            "Studio68 product identity differs")
    require(all(snapshot.get("files", {}).get(path, {}).get("sha256") == value
                for path, value in STUDIO68_PRODUCT_PINS.items()), "Studio68 product input pin differs")
    return {"sha": snapshot["sha"], "tree": snapshot["tree"], "sdk_lock_sha": STUDIO68_SDK}


def studio68_command(argv, cwd, environment, output, label, cap, clock, commands, *, defer_exit_status=False):
    require(type(defer_exit_status) is bool and (not defer_exit_status or label in
            ("product-contracts", "independent-readback")), "Studio68 deferred exit has no bound inner receipt")
    require(label not in {x["label"] for x in commands}, "Studio68 command label was already attempted")
    record = {"label": label, "argv": list(argv), "status": "STARTED"}
    commands.append(record)
    try:
        result = execute(argv, cwd, environment, output, label, clock.remaining(cap))
        record.update(result)
        require_observed_command(result)
        require(defer_exit_status or result["exit_code"] == 0, "Studio68 command failed: " + label)
        clock.remaining(1)
        streams = {}
        for stream in ("stdout", "stderr"):
            path = output / (label + "." + stream + ".log")
            raw, identity = studio68_original(path, ARCHIVE_LIMITS["file_bytes"])
            require(identity == result[stream], "Studio68 command original changed")
            streams[stream] = raw
        record["status"] = "PASS_OBSERVED_COMMAND_ONLY" if result["exit_code"] == 0 else "OBSERVED_NONZERO_EXIT"
        return result, streams
    except UnknownObservation as error:
        record.update(status="UNKNOWN_NO_RETRY", retained_command=error.command)
        raise
    except BaseException:
        record["status"] = "FAIL_OR_PREPARATION_REJECTED"
        raise


def studio68_inner_terminal(command, receipt, identity, kind):
    """Preserve inner uncertainty even when the original observer exited nonzero."""
    require_observed_command(command)
    require(type(receipt) is dict and kind in ("product-g0", "readback"), "Studio68 inner receipt type")
    uncertain = []
    if kind == "product-g0":
        if receipt.get("status") == "UNKNOWN":
            uncertain.append("runner_status_UNKNOWN")
        for step in receipt.get("steps", ()):
            if type(step) is dict and step.get("status") == "UNKNOWN":
                uncertain.append("entrypoint_UNKNOWN:" + str(step.get("entry_point")))
    else:
        for case in receipt.get("cases", ()):
            observed = case.get("readback") if type(case) is dict else None
            if type(observed) is not dict or observed.get("transportAttempted") is not True:
                continue
            close = observed.get("close")
            if (observed.get("transportExecuted") is not True or type(close) is not dict
                    or type(close.get("returnCode")) is not int or close.get("taints") or close.get("errors")):
                uncertain.append("transport_or_capture_not_closed:" + str(case.get("case")))
    if uncertain:
        raise UnknownObservation("Studio68 inner receipt retains uncertainty; no further command or retry",
                                 {"outer_command": command, "inner_receipt": identity,
                                  "inner_kind": kind, "uncertainty": uncertain})
    require(type(command.get("exit_code")) is int and command["exit_code"] == 0,
            "Studio68 inner receipt cannot replace nonzero original exit")


def studio68_test_roster(raw, prefix):
    import ast
    module = ast.parse(raw)
    names = []
    for cls in module.body:
        if isinstance(cls, ast.ClassDef):
            for method in cls.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith("test_"):
                    require(ast.get_docstring(method) is None,
                            "Studio68 fixed product G0 source unexpectedly has a multiline test description")
                    names.append(prefix + "." + cls.name + "." + method.name)
    require(names and len(names) == len(set(names)), "Studio68 source test roster is empty or duplicated")
    return tuple(sorted(names))


def studio68_g0_receipt(receipt, expected, context, original_reader, summary_parser):
    require(receipt.get("schema") == "motionwright.unittest-execution/1"
            and receipt.get("executed_source_sha") == STUDIO68_SOURCE
            and receipt.get("tree_sha") == STUDIO68_TREE
            and receipt.get("runner_sha256") == STUDIO68_RUNNER_SHA256
            and receipt.get("require_complete") is True
            and type(receipt.get("exit_code")) is int and receipt["exit_code"] == 0
            and receipt.get("status") == "PASS" and not receipt.get("source_postcheck_error"),
            "Studio68 product G0 receipt is not an exact successful execution")
    require(receipt.get("python_executable_sha256") == expected["python_sha256"],
            "Studio68 G0 child Python differs from the observed executable")
    require(receipt.get("github") == {key: context["github"][key] for key in
            ("GITHUB_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")},
            "Studio68 product receipt relabelled the actual gate context")
    path = "tooling/tests/test_ci_canvas_service.py"
    require(receipt.get("test_definition_sha256") == {path: expected["definition"]},
            "Studio68 G0 test definition differs from F")
    require(receipt.get("source_locks") == expected["locks"], "Studio68 G0 source locks differ")
    labels = ("discovery:test_ci_canvas_service.py", "dotted:tooling.tests.test_ci_canvas_service")
    steps = receipt.get("steps")
    require(type(steps) is list and len(steps) == 2 and tuple(x.get("entry_point") for x in steps) == labels,
            "Studio68 G0 entrypoint roster changed")
    for index, step in enumerate(steps):
        suffix = (["discover", "-s", "tooling/tests", "-p", "test_ci_canvas_service.py", "-v"]
                  if index == 0 else ["tooling.tests.test_ci_canvas_service", "-v"])
        require(step.get("command") == [expected["python"], "-B", "-E", "-s", "-m", "unittest", *suffix],
                "Studio68 G0 original child argv changed")
        require(step.get("status") == "PASS" and type(step.get("exit_code")) is int and step["exit_code"] == 0,
                "Studio68 incomplete or uncertain G0 step")
        streams = {}
        for stream in ("stdout", "stderr"):
            row = step.get(stream, {})
            name = "receipt-" + str(index).zfill(2) + "-" + stream + ".log"
            require(row.get("file") == name and re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", "")),
                    "Studio68 G0 original locator changed")
            raw = original_reader(name)
            require(type(raw) is bytes and hashlib.sha256(raw).hexdigest() == row["sha256"],
                    "Studio68 G0 original hash changed")
            streams[stream] = raw
        parsed = summary_parser(streams["stderr"].decode("utf-8", "strict"))
        counts = step.get("counts")
        require(type(counts) is dict and counts == parsed
                and all(type(counts.get(k)) is int for k in (
                    "tests_run", "passed", "failures", "errors", "skipped", "expected_failures", "unexpected_successes"))
                and counts["tests_run"] == counts["passed"] == len(expected["ids"])
                and counts["terminal_status"] == "OK"
                and not any(counts[k] for k in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes")),
                "Studio68 G0 footer/count/omission mismatch")
        text = streams["stderr"].decode("utf-8", "strict").replace("\r\n", "\n")
        lines = [line for line in text.splitlines() if line.startswith("test_")]
        matches = [re.fullmatch(r"(test_[A-Za-z_0-9]+) \(([^\r\n)]+)\) \.\.\. ok", line) for line in lines]
        require(matches and all(match and match.group(2).rsplit(".", 1)[-1] == match.group(1)
                                for match in matches), "Studio68 G0 original individual terminal is not ok")
        observed = [match.group(2) for match in matches]
        normalized = [x if x.startswith("tooling.tests.") else "tooling.tests." + x for x in observed]
        require(len(normalized) == len(set(normalized)) == len(expected["ids"])
                and set(normalized) == set(expected["ids"]), "Studio68 G0 original IDs differ")
    return {"status": "PASS_PRODUCT_CONTRACTS_ONLY", "methods_per_entry": len(expected["ids"]),
            "entrypoints": list(labels), "executed_product_source_sha": STUDIO68_SOURCE}


def studio68_roster(document, *, executed=False):
    require(type(document) is dict and document.get("errors") == [], "Studio68 reporter errors")
    config = document.get("config", {})
    require(type(config.get("workers")) is int and config["workers"] == 1 and config.get("shard") is None
            and config.get("globalSetup") is None and config.get("globalTeardown") is None
            and config.get("fullyParallel") is False and config.get("forbidOnly") is True
            and config.get("grep") == {} and config.get("grepInvert") is None,
            "Studio68 reporter execution configuration changed")
    projects = config.get("projects")
    require(type(projects) is list and len(projects) == 1, "Studio68 exactly one Chromium project")
    project = projects[0]
    require(project.get("id") == project.get("name") == "chromium"
            and type(project.get("retries")) is int and project["retries"] == 0
            and type(project.get("repeatEach")) is int and project["repeatEach"] == 1,
            "Studio68 project selection/repetition changed")
    stats = document.get("stats", {})
    require(all(type(stats.get(k)) is int for k in ("expected", "unexpected", "flaky", "skipped"))
            and {k: stats[k] for k in ("expected", "unexpected", "flaky", "skipped")} ==
            ({"expected": 68, "unexpected": 0, "flaky": 0, "skipped": 0} if executed else
             {"expected": 0, "unexpected": 0, "flaky": 0, "skipped": 68}),
            "Studio68 reporter counts/omissions changed")
    rows = []
    def visit(suite):
        require(type(suite) is dict, "Studio68 malformed reporter suite")
        for spec in suite.get("specs", []):
            tests = spec.get("tests")
            require(type(tests) is list and len(tests) == 1, "Studio68 duplicate project case")
            test = tests[0]
            require(test.get("projectId") == test.get("projectName") == "chromium"
                    and test.get("expectedStatus") == "passed"
                    and type(test.get("timeout")) is int and test["timeout"] == 120000
                    and test.get("annotations", []) == [], "Studio68 case timeout/skip policy changed")
            identity = (spec.get("file"), spec.get("title"), spec.get("line"), spec.get("column"), spec.get("id"))
            require(type(identity[2]) is int and type(identity[3]) is int, "Studio68 case location type")
            if executed:
                results = test.get("results")
                require(spec.get("ok") is True and test.get("status") == "expected"
                        and type(results) is list and len(results) == 1
                        and results[0].get("status") == "passed"
                        and type(results[0].get("retry")) is int and results[0]["retry"] == 0
                        and results[0].get("errors") == [], "Studio68 result is incomplete or retried")
            else:
                require(test.get("results") == [], "Studio68 enumeration contains execution results")
            rows.append(identity)
        for nested in suite.get("suites", []):
            visit(nested)
    for suite in document.get("suites", []):
        visit(suite)
    require(len(rows) == 68 and len(set(rows)) == 68
            and len({row[4] for row in rows}) == 68 and set(rows) == set(STUDIO68_CASES),
            "Studio68 exact named roster changed")
    return {"scope": STUDIO68_SCOPE, "mode": "execution" if executed else "enumeration",
            "ids": sorted(row[4] for row in rows), "unique_cases": 68,
            "physical_service_evidence": "REQUIRES_CLOSED_STORE_READBACK"}


def studio68_playwright_argv(node, cli, results, *, listing=False):
    argv = [node, str(cli), "test", *("tests/" + name for name in STUDIO68_FILES), "--project=chromium", "--workers=1",
            "--retries=0", "--repeat-each=1", "--forbid-only", "--reporter=json", "--output=" + str(results)]
    return argv + (["--list"] if listing else [])


def studio68_build_argv(cargo):
    return [cargo, "build", "--locked", "--offline", "--jobs", "1", "-p", "motionwright-service",
            "--example", "studio_review_session", "--message-format=json"]


def studio68_binary_spec():
    return ({"package": "motionwright-service", "crate": "service", "name": "studio_review_session",
             "kind": "example", "source": "crates/service/examples/studio_review_session.rs"},)


def studio68_readback_admission(report, command, expected_binary, expected_ids):
    require_observed_command(command)
    require(type(command.get("exit_code")) is int and command["exit_code"] == 0,
            "Studio68 independent reader process failed")
    require(report.get("status") == "READBACK_VERIFIED" and report.get("errors") == []
            and type(report.get("expectedCases")) is int and report["expectedCases"] == 68
            and report.get("backendSourceShaAssertedByCaller") == STUDIO68_SOURCE
            and type(report.get("playwrightExitCodeReportedByCaller")) is int
            and report["playwrightExitCodeReportedByCaller"] == 0
            and report.get("binary") == expected_binary
            and report.get("receipt", {}).get("status") == "WRITTEN"
            and report.get("observationJournal", {}).get("status") == "CLOSED"
            and report.get("transportAttempted") is True and report.get("transportExecuted") is True,
            "Studio68 independent reader receipt is incomplete")
    journal = report["observationJournal"]
    require(type(journal.get("recordsAttempted")) is int and journal["recordsAttempted"] > 0
            and type(journal.get("recordsConfirmed")) is int
            and journal["recordsConfirmed"] == journal["recordsAttempted"],
            "Studio68 independent reader journal is incomplete")
    cases = report.get("cases")
    require(type(cases) is list and len(cases) == 68 and len({x.get("case") for x in cases}) == 68,
            "Studio68 independent store roster changed")
    ids = []
    for case in cases:
        require(case.get("captureStatus") == "VERIFIED" and case.get("errors") == []
                and case.get("readback", {}).get("status") == "VERIFIED"
                and case["readback"].get("errors") == [], "Studio68 closed store/readback failed")
        observed = case["readback"]
        close = observed.get("close", {})
        require(observed.get("transportAttempted") is True and observed.get("transportExecuted") is True
                and type(observed.get("processId")) is int and observed["processId"] > 0
                and type(observed.get("rpcCount")) is int and observed["rpcCount"] >= 5
                and observed.get("primaryFailure") is None
                and observed.get("projectEqual") is True and observed.get("journalEqual") is True
                and observed.get("receipt", {}).get("status") == "WRITTEN"
                and close.get("clean") is True and close.get("errors") == [] and close.get("taints") == []
                and type(close.get("returnCode")) is int and close["returnCode"] == 0
                and close.get("processId") == observed["processId"]
                and close.get("rpcCount") == observed["rpcCount"], "Studio68 original readback transport is incomplete")
        item = case.get("playwrightIdentity", {})
        require(item.get("projectId") == "chromium", "Studio68 readback project changed")
        ids.append(item.get("playwrightId"))
    require(len(set(ids)) == 68 and set(ids) == set(expected_ids), "Studio68 readback IDs are not the executed cohort")
    return {"status": "PASS_REAL_SERVICE_AND_CLOSED_STORE_READBACK", "cases": 68,
            "product_source": STUDIO68_SOURCE, "human_acceptance": "NOT_GRANTED",
            "native_rendering": "NOT_RUN", "escaped_descendants": "NOT_OBSERVED"}


def studio68_readback_journal(raw, report):
    require(type(raw) is bytes and raw.endswith(b"\n"), "Studio68 readback journal is truncated")
    rows = [json.loads(line) for line in raw.splitlines()]
    require(len(rows) == 206 == report["observationJournal"]["recordsConfirmed"]
            and all(type(row) is dict and row.get("schema") == "motionwright.readback-observation/1"
                    and type(row.get("sequence")) is int and row["sequence"] == index
                    for index, row in enumerate(rows, 1)), "Studio68 readback journal sequence/record count")
    require(rows[0].get("kind") == "run_before_transport" and rows[0].get("case") is None
            and rows[-1].get("kind") == "run_before_receipt" and rows[-1].get("case") is None
            and rows[-1].get("observation", {}).get("cases") == report["cases"],
            "Studio68 readback journal boundaries differ")
    for index, case in enumerate(report["cases"]):
        group = rows[1 + index * 3:4 + index * 3]
        require([row.get("kind") for row in group] == ["launch_intent", "transport_observed", "case_closed"]
                and all(row.get("case") == case["case"] for row in group),
                "Studio68 readback journal case order changed")
        observed = dict(group[-1].get("observation", {}))
        current = dict(case["readback"])
        observed_receipt = dict(observed.pop("receipt", {}))
        current_receipt = dict(current.pop("receipt", {}))
        require(observed_receipt.pop("status", None) == "PENDING"
                and current_receipt.pop("status", None) == "WRITTEN"
                and observed_receipt == current_receipt and observed == current,
                "Studio68 case terminal differs from its original journal")
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "records": len(rows)}

def studio68_tools(product, environment, output, clock, commands, *, phase=None):
    phase = clock.phase(STUDIO68_PREPARATION_SECONDS) if phase is None else phase
    tools, identities = {}, {}
    for name in ("node", "npm", "rustup", "cc", "ar", "git"):
        found = shutil.which(name, path=environment.get("PATH"))
        require(found, "Studio68 tool unavailable: " + name)
        path = Path(found).resolve(strict=True)
        require(path.is_absolute(), "Studio68 tool must be absolute")
        tools[name] = str(path)
        identities[name] = {"path": str(path), **studio68_original(path, 256 * 1024 * 1024)[1]}
    tools["python"] = str(Path(sys.executable).resolve(strict=True))
    identities["python"] = {"path": tools["python"], **studio68_original(Path(tools["python"]), 256 * 1024 * 1024)[1]}
    def run(argv, label):
        return studio68_command(argv, product, environment, output, label, 30, phase, commands)[1]["stdout"].decode("utf-8", "strict").strip()
    node_version = run([tools["node"], "--version"], "node-version")
    require(re.fullmatch(r"v22\.[0-9]+\.[0-9]+", node_version), "Studio68 requires observed Node22")
    run([tools["node"], tools["npm"], "--version"], "npm-version")
    run([tools["python"], "-I", "-B", "-X", "utf8", "--version"], "python-version")
    run([tools["rustup"], "--version"], "rustup-version")
    run([tools["cc"], "--version"], "cc-version")
    run([tools["ar"], "--version"], "ar-version")
    run([tools["git"], "--version"], "git-version")
    rust_tools = ("cargo", "rustc", "rustdoc", "cargo-fmt", "rustfmt", "cargo-clippy", "clippy-driver")
    for name in rust_tools:
        raw = run([tools["rustup"], "which", "--toolchain", "1.98.1", name], "which-" + name)
        require("\n" not in raw and Path(raw).is_absolute(), "Studio68 ambiguous Rust tool")
        path = Path(raw).resolve(strict=True)
        tools[name] = str(path)
        identities[name] = {"path": str(path), **studio68_original(path, 256 * 1024 * 1024)[1]}
    require(len({str(Path(tools[k]).parent) for k in rust_tools}) == 1,
            "Studio68 mixed Rust tool roots")
    rustc = run([tools["rustc"], "-Vv"], "rustc-version")
    require("release: 1.98.1" in rustc.splitlines() and "host: x86_64-unknown-linux-gnu" in rustc.splitlines(),
            "Studio68 compiler release/host mismatch")
    for name in ("cargo", "rustdoc", "cargo-fmt", "rustfmt", "cargo-clippy", "clippy-driver"):
        require(run([tools[name], "--version"], name + "-version"), "Studio68 unobserved tool version")
    return tools, identities


def studio68_verify_tools(identities):
    for name, row in identities.items():
        require(studio68_original(Path(row["path"]), 256 * 1024 * 1024)[1] ==
                {k: row[k] for k in ("bytes", "sha256")}, "Studio68 tool changed: " + name)


def studio68_product_g0(product, snapshot, context, tools, environment, output, clock, commands, git):
    import ast
    phase = clock.phase(STUDIO68_PHASE_SECONDS["product_g0"])
    python = tools["python"]
    bootstrap = ("import runpy,sys; from pathlib import Path; p=Path(sys.argv[1]); "
                 "sys.path.insert(0,str(p.parent)); sys.argv=sys.argv[1:]; runpy.run_path(str(p),run_name='__main__')")
    for name in STUDIO68_POLICIES:
        studio68_command([python, "-I", "-B", "-X", "utf8", "-c", bootstrap, str(product / "tooling" / name)],
                         product, environment, output, "product-" + name[:-3], 600, phase, commands)
    runner_path = product / "tooling/ci_test_runner.py"
    runner_raw, runner_identity = studio68_original(runner_path, 1024 * 1024)
    require(runner_identity["sha256"] == STUDIO68_RUNNER_SHA256, "Studio68 product G0 runner source changed")
    # Execute the verified parser bytes, never an unrelated cached .pyc.
    parser_node = next(n for n in ast.parse(runner_raw).body
                       if isinstance(n, ast.FunctionDef) and n.name == "parse_unittest_summary")
    namespace = {"re": re}
    exec(compile(ast.Module(body=[parser_node], type_ignores=[]), str(runner_path), "exec"), namespace)
    report = output / "product-g0" / "receipt.json"
    python_identity = studio68_original(Path(python), 256 * 1024 * 1024)[1]
    command, _ = studio68_command([python, "-I", "-B", "-X", "utf8", str(runner_path), "--repo", str(product),
        "--mode", "both", "--pattern", "test_ci_canvas_service.py", "--expected-source", STUDIO68_SOURCE,
        "--report", str(report), "--require-complete", "--timeout", "300"],
        product, environment, output, "product-contracts", 660, phase, commands, defer_exit_status=True)
    raw, report_identity = studio68_original(report, 4 * 1024 * 1024)
    inner = json.loads(raw)
    studio68_inner_terminal(command, inner, report_identity, "product-g0")
    test_raw, test_identity = studio68_original(product / "tooling/tests/test_ci_canvas_service.py", 2 * 1024 * 1024)
    expected = {"definition": test_identity["sha256"],
                "ids": studio68_test_roster(test_raw, "tooling.tests.test_ci_canvas_service"),
                "python": python, "python_sha256": python_identity["sha256"],
                "locks": {p: snapshot["files"][p]["sha256"] for p in
                          ("Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json")}}
    contracts = studio68_g0_receipt(inner, expected, context,
        lambda name: studio68_original(report.parent / name, 32 * 1024 * 1024)[0], namespace["parse_unittest_summary"])
    studio68_command([tools["cargo-fmt"], "fmt", "--all", "--", "--check"], product, environment,
                     output, "product-format", 120, phase, commands)
    require(studio68_snapshot(product, STUDIO68_SOURCE, git, expected_tree=STUDIO68_TREE) == snapshot,
            "Studio68 product source changed during its own G0")
    return {"status": "PASS_PRODUCT_G0_FOCAL", "product": STUDIO68_SOURCE,
            "policies": list(STUDIO68_POLICIES), "contracts": contracts, "receipt": report_identity,
            "format": "OBSERVED_CARGO_FMT_WITH_OBSERVED_RUSTFMT", "gate_credit_transferred": False}


def studio68_dependency_closure(metadata, product, root, sdk, lock_raw):
    import tomllib
    require(type(metadata) is dict and metadata.get("version") == 1
            and Path(metadata.get("workspace_root", "")).resolve(strict=True) == product,
            "Studio68 Cargo metadata belongs to another checkout")
    packages = metadata.get("packages")
    require(type(packages) is list and len(packages) <= 2000, "Studio68 package roster bound")
    indexed = {p["id"]: p for p in packages}
    require(len(indexed) == len(packages), "Studio68 duplicate Cargo package")
    roots = [p["id"] for p in packages if p.get("name") == "motionwright-service"
             and Path(p.get("manifest_path", "")).absolute() == product / "crates/service/Cargo.toml"]
    require(len(roots) == 1, "Studio68 selected service package missing or ambiguous")
    nodes = {p["id"]: p for p in metadata.get("resolve", {}).get("nodes", [])}
    require(set(nodes) <= set(indexed), "Studio68 resolved package is unbound")
    selected, pending = set(), list(roots)
    while pending:
        package = pending.pop()
        if package in selected:
            continue
        require(package in nodes and len(selected) < 2000, "Studio68 unresolved dependency")
        selected.add(package)
        pending.extend(nodes[package].get("dependencies", []))
    lock = tomllib.loads(lock_raw.decode("utf-8"))
    locked = {(p["name"], p["version"], p.get("source")): p for p in lock["package"]}
    sources, sdk_snapshot = {}, None
    for package_id in sorted(selected):
        item = indexed[package_id]
        folder = Path(item["manifest_path"]).resolve(strict=True).parent
        source = item.get("source")
        key = (item["name"], item["version"], source)
        require(key in locked, "Studio68 dependency differs from Cargo.lock")
        if source is None:
            require(folder.is_relative_to(product), "Studio68 unbound local path dependency")
            continue
        require(folder.is_relative_to(root / "cargo"), "Studio68 dependency escaped the owned Cargo home")
        if source.startswith("git+"):
            require(source.startswith("git+https://github.com/seradotcom/semwright?")
                    and source.endswith("#" + STUDIO68_SDK)
                    and "rev=" + STUDIO68_SDK in source, "Studio68 unexpected Git dependency")
            sdk_root = Path(sdk.custody.git(folder, "rev-parse", "--show-toplevel").decode().strip()).resolve(strict=True)
            if sdk_snapshot is None:
                sdk_snapshot = studio68_snapshot(sdk_root, STUDIO68_SDK, sdk.custody.git, cargo_marker=True)
                sources["sdk"] = {"root": str(sdk_root), "snapshot": sdk_snapshot}
            require(folder.is_relative_to(Path(sources["sdk"]["root"])), "Studio68 split SDK checkout")
        else:
            require(source == "registry+https://github.com/rust-lang/crates.io-index",
                    "Studio68 noncanonical registry dependency")
            raw, checksum_identity = studio68_original(folder / ".cargo-checksum.json", 4 * 1024 * 1024)
            checksum = json.loads(raw)
            require(checksum.get("package") == locked[key].get("checksum")
                    and re.fullmatch(r"[0-9a-f]{64}", checksum.get("package", "")),
                    "Studio68 registry archive checksum differs from lock")
            inventory = sdk.dependency_inventory(folder)
            require(type(checksum.get("files")) is dict and checksum["files"], "Studio68 registry file roster missing")
            for name, expected in checksum["files"].items():
                require(name in inventory and inventory[name].get("sha256") == expected,
                        "Studio68 registry source checksum differs: " + name)
            require(set(inventory) <= set(checksum["files"]) | {".cargo-checksum.json", ".cargo-ok"},
                    "Studio68 unbound registry source input")
            sources[package_id] = {"root": str(folder), "inventory": inventory,
                                   "archive_checksum": checksum["package"], "checksum_original": checksum_identity}
    require(sdk_snapshot is not None, "Studio68 actual resolved SDK checkout was not observed")
    return {"root_package": roots[0], "resolved_package_ids": sorted(selected), "sources": sources,
            "scope": "Resolved source closure; not a claim that every package target executed"}


def studio68_verify_dependencies(closure, sdk):
    for key, value in closure["sources"].items():
        folder = Path(value["root"])
        actual = (studio68_snapshot(folder, STUDIO68_SDK, sdk.custody.git, cargo_marker=True)
                  if key == "sdk" else sdk.dependency_inventory(folder))
        require(actual == value["snapshot" if key == "sdk" else "inventory"],
                "Studio68 dependency changed after compilation: " + key)


def studio68_npm_inventory(root, sdk):
    inventory = sdk.dependency_inventory(root)
    # The pinned Vite configuration uses its documented node_modules caches.
    # Cache output is retained separately; it never becomes immutable package input.
    generated = (".vite/", ".vite-temp/")
    return {"files": {k: v for k, v in inventory.items() if not k.startswith(generated)},
            "generated_cache_roots": [".vite", ".vite-temp"]}


def studio68_seal_binary(raw, code, product, root, sdk):
    binaries = sdk.select_binaries(raw, code, product, root / "target", studio68_binary_spec(), testing=False)
    observed = binaries["studio_review_session"]
    original = Path(observed["absolute_path"])
    retained = root / "evidence/tools/studio_review_session"
    retained.parent.mkdir(parents=True, exist_ok=False)
    data, identity = studio68_original(original, 256 * 1024 * 1024)
    require(identity == {k: observed[k] for k in ("bytes", "sha256")}, "Studio68 compiled binary changed before retention")
    with retained.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    retained.chmod(0o500)
    require(studio68_original(retained, 256 * 1024 * 1024)[1] == identity,
            "Studio68 retained executable readback differs")
    sdk.verify_binaries(binaries, root / "target")
    return {"producer_original": observed, "retained": {"path": str(retained), **identity},
            "executed_product_source_sha": STUDIO68_SOURCE, "executable_target": studio68_binary_spec()[0]}


def studio68_reader_argv(python, reader, root, original, binary):
    return [python, "-I", "-B", "-X", "utf8", str(reader),
            "--results-root", str(root / "results"), "--output", str(root / "readback"),
            "--expected-cases", "68", "--playwright-exit-code", "0",
            "--playwright-json", str(root / "playwright-execution.json"),
            "--expected-playwright-sha256", original["execution"]["sha256"],
            "--expected-roster-json", str(root / "playwright-list.json"),
            "--expected-roster-sha256", original["list"]["sha256"],
            "--provenance-receipt", str(root / "source-build-binary.json"),
            "--expected-provenance-sha256", original["provenance"]["sha256"],
            "--provenance-reviewed", "--execute-readback", "--expected-binary-sha256", binary["sha256"],
            "--backend-source-sha", STUDIO68_SOURCE, "--rpc-timeout-seconds", "10"]


def studio68_main(arguments):
    import time
    started_ns = time.monotonic_ns()  # One origin before any controlled IO.
    parser = argparse.ArgumentParser(description="One exact 68-case Studio service cohort with independent closed-store readback")
    parser.add_argument("--gate-repo", required=True, type=Path)
    parser.add_argument("--product-repo", required=True, type=Path)
    parser.add_argument("--product-source", required=True)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args(arguments)
    context = studio68_context(os.environ, args.product_source)
    clock = Studio68Clock(started_ns)
    gate, product = args.gate_repo.resolve(strict=True), args.product_repo.resolve(strict=True)
    root = args.root.absolute()
    require(root.is_absolute() and not root.exists() and root.parent.resolve(strict=True) == root.parent
            and root.parent == Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
            and gate != product and not gate.is_relative_to(product) and not product.is_relative_to(gate)
            and not root.is_relative_to(gate) and not root.is_relative_to(product), "Studio68 isolated owned namespaces")
    environment = studio68_environment(os.environ, root)
    root.mkdir()
    output = root / "evidence"
    output.mkdir()
    for name in ("cargo", "target", "npm", "browsers", "tmp", "xdg"):
        (root / name).mkdir()
    commands = []
    receipt = {"schema": "motionwright.studio68-execution/1", "context": context,
               "started_mono_ns": started_ns, "deadline_mono_ns": clock.deadline_ns,
               "commands": commands, "status": "PREPARING", "service_cases": "NOT_RUN",
               "retry_authorized": False, "human_acceptance": "NOT_GRANTED",
               "process_boundary": "OWNED_POSIX_SESSION_GROUP", "escaped_descendants": "NOT_OBSERVED"}
    exit_code = 1
    try:
        preparation = clock.phase(STUDIO68_PREPARATION_SECONDS)
        sdk = studio68_load_helpers(gate)
        git = sdk.custody.git
        gate_before = studio68_snapshot(gate, context["gate_source_sha"], git)
        product_before = studio68_snapshot(product, STUDIO68_SOURCE, git, expected_tree=STUDIO68_TREE)
        studio68_product_identity(product_before)
        require(Path(__file__).resolve(strict=True) == gate / "tooling/ci_canvas_service.py", "Studio68 helper came from another checkout")
        for path, expected in STUDIO68_READER_PINS.items():
            require(gate_before["files"].get(path, {}).get("sha256") == expected, "Studio68 independent reader pin differs")
        write_new(output / "gate-source-before.json", gate_before)
        write_new(output / "product-source-before.json", product_before)
        preparation.remaining(1)
        receipt["gate_tree_sha"], receipt["executed_product_tree_sha"] = gate_before["tree"], product_before["tree"]
        tools, tool_identities = studio68_tools(product, environment, output, clock, commands, phase=preparation)
        environment.update(CARGO=tools["cargo"], RUSTC=tools["rustc"], RUSTDOC=tools["rustdoc"], RUSTFMT=tools["rustfmt"],
                           CC=tools["cc"], AR=tools["ar"], CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=tools["cc"],
                           RUSTUP_TOOLCHAIN="1.98.1", PATH=os.pathsep.join(
                               (str(Path(tools["node"]).parent), str(Path(tools["cargo"]).parent), environment.get("PATH", ""))))
        receipt["tools"] = tool_identities
        receipt["product_g0"] = studio68_product_g0(product, product_before, context, tools, environment, output, clock, commands, git)
        require(shutil.disk_usage(root).free >= 18 * 1024**3, "Studio68 compiler disk reserve unavailable")
        memory = Path("/proc/meminfo").read_text(encoding="ascii")
        available = re.search(r"(?m)^MemAvailable:\s+([0-9]+) kB$", memory)
        require(available and int(available.group(1)) * 1024 >= 4 * 1024**3, "Studio68 compiler memory reserve unavailable")
        phase = clock.phase(STUDIO68_PHASE_SECONDS["dependencies"])
        def run(argv, label, cap, selected_phase, *, cwd=product, env=environment, defer_exit_status=False):
            return studio68_command(argv, cwd, env, output, label, cap, selected_phase, commands,
                                    defer_exit_status=defer_exit_status)
        barrier = sdk.ProducerBarrier(lambda argv, label, cap, selected_phase, cwd=product, env=environment:
                                      run(argv, label, cap, selected_phase, cwd=cwd, env=env), tools["cargo"])
        barrier([tools["cargo"], "fetch", "--locked"], "locked-fetch", 600, phase)
        _, metadata = barrier([tools["cargo"], "metadata", "--locked", "--offline", "--format-version", "1"],
                              "resolved-dependencies", 120, phase)
        closure = studio68_dependency_closure(json.loads(metadata["stdout"]), product, root, sdk,
                                              studio68_original(product / "Cargo.lock", 16 * 1024 * 1024)[0])
        write_new(output / "compiled-source-dependencies.json", closure)
        studio = product / "apps/studio"
        run([tools["node"], tools["npm"], "ci", "--no-audit", "--no-fund"], "npm-ci", 600, phase, cwd=studio)
        cli = studio / "node_modules/@playwright/test/cli.js"
        require(cli.resolve(strict=True).is_relative_to(studio / "node_modules"), "Studio68 Playwright CLI escaped dependency tree")
        run([tools["node"], str(cli), "install", "--with-deps", "chromium"], "chromium-install", 600, phase, cwd=studio)
        npm = studio68_npm_inventory(studio / "node_modules", sdk)
        write_new(output / "npm-source-before.json", npm)
        barrier([tools["cargo"], "clippy", "--locked", "--offline", "--jobs", "1", "-p", "motionwright-service",
                 "--example", "studio_review_session", "--", "-D", "warnings"], "selected-clippy", 900,
                clock.phase(STUDIO68_PHASE_SECONDS["clippy"]))
        build_phase = clock.phase(STUDIO68_PHASE_SECONDS["build"])
        built, streams = barrier(studio68_build_argv(tools["cargo"]), "service-example-build", 1200, build_phase)
        barrier.begin_sealing()
        binary = studio68_seal_binary(streams["stdout"], built["exit_code"], product, root, sdk)
        studio68_verify_dependencies(closure, sdk)
        require(studio68_snapshot(product, STUDIO68_SOURCE, git, expected_tree=STUDIO68_TREE, allow_node_modules=True) == product_before,
                "Studio68 product source changed before observation")
        studio68_verify_tools(tool_identities)
        provenance = {"schema": "motionwright.studio68-source-build-binary/1", "context": context,
                      "product_g0": receipt["product_g0"], "product_source": STUDIO68_SOURCE, "product_tree": STUDIO68_TREE,
                      "gate_source": context["gate_source_sha"], "gate_tree": gate_before["tree"],
                      "binary": binary, "build_command": built, "tools": tool_identities,
                      "sdk_source": STUDIO68_SDK, "sdk_tree": closure["sources"]["sdk"]["snapshot"]["tree"],
                      "retry_authorized": False, "formal_acceptance": "NOT_GRANTED"}
        write_new(output / "source-build-binary.json", provenance)
        receipt["binary"] = binary
        barrier.begin_observation()
        pw_env = dict(environment, MOTIONWRIGHT_SERVICE_TEST_BIN=binary["retained"]["path"],
                      MOTIONWRIGHT_SERVICE_TEST_TMPDIR=str(root / "tmp"), MOTIONWRIGHT_PLAYWRIGHT_PORT="14361")
        originals = {"provenance": studio68_original(output / "source-build-binary.json", 8 * 1024 * 1024)[1]}
        build_phase.remaining(1)
        for listing, label in ((True, "list"), (False, "execution")):
            cap = STUDIO68_PHASE_SECONDS[label]
            reporter = output / ("playwright-" + label + ".json")
            current_env = dict(pw_env, PLAYWRIGHT_JSON_OUTPUT_FILE=str(reporter))
            args_pw = studio68_playwright_argv(tools["node"], cli, output / "results", listing=listing)
            # No Cargo producer remains reachable through this barrier phase.
            result, _ = barrier(args_pw, "playwright-" + label, cap, clock.phase(cap), studio, current_env)
            raw, originals[label] = studio68_original(reporter, 64 * 1024 * 1024)
            document = json.loads(raw)
            require(document.get("config", {}).get("argv") == args_pw, "Studio68 reporter argv differs from observed launch")
            require(Path(document["config"].get("rootDir", "")).resolve(strict=True) == studio / "tests"
                    and Path(document["config"].get("configFile", "")).resolve(strict=True) == studio / "playwright.config.ts",
                    "Studio68 reporter source roots changed")
            receipt[label] = studio68_roster(document, executed=not listing)
            require(studio68_original(Path(binary["retained"]["path"]), 256 * 1024 * 1024)[1] ==
                    {k: binary["retained"][k] for k in ("bytes", "sha256")}, "Studio68 executed service binary changed")
        require(receipt["list"]["ids"] == receipt["execution"]["ids"], "Studio68 list/execution IDs differ")
        require(studio68_npm_inventory(studio / "node_modules", sdk) == npm, "Studio68 installed package sources changed")
        require(studio68_snapshot(product, STUDIO68_SOURCE, git, expected_tree=STUDIO68_TREE, allow_node_modules=True) == product_before,
                "Studio68 source changed before independent readback")
        require(json.loads(studio68_original(output / "source-build-binary.json", 8 * 1024 * 1024)[0]) == provenance,
                "Studio68 independent source receipt changed")
        reader = gate / "tooling/service_readback/service_archive_readback_r3.py"
        require(studio68_original(reader, 2 * 1024 * 1024)[1]["sha256"] == STUDIO68_READER_PINS[reader.relative_to(gate).as_posix()],
                "Studio68 independent reader changed before execution")
        read_command, _ = run(studio68_reader_argv(tools["python"], reader, output, originals, binary["retained"]),
                             "independent-readback", 5400, clock.phase(STUDIO68_PHASE_SECONDS["readback"]),
                             env=pw_env, defer_exit_status=True)
        retention_phase = clock.phase(STUDIO68_PHASE_SECONDS["retention"], retention=True)
        read_raw, read_identity = studio68_original(output / "readback/readback-report.json", 32 * 1024 * 1024)
        read_report = json.loads(read_raw)
        studio68_inner_terminal(read_command, read_report, read_identity, "readback")
        receipt["readback"] = studio68_readback_admission(read_report, read_command,
            {k: binary["retained"][k] for k in ("path", "sha256")}, receipt["execution"]["ids"])
        receipt["readback_original"] = read_identity
        journal_raw, _ = studio68_original(output / "readback/execution-observations.jsonl", 128 * 1024 * 1024)
        receipt["readback_journal_original"] = studio68_readback_journal(journal_raw, read_report)
        sealed = output / "readback/sealed-playwright-results.zip"
        require(read_report.get("archive", {}).get("archive") == str(sealed), "Studio68 readback archive locator changed")
        sealed_identity = archive_file_digest(sealed, ARCHIVE_LIMITS["total_bytes"])
        require(sealed_identity["sha256"] == read_report["archive"].get("archiveSha256"), "Studio68 sealed ZIP changed")
        seal_raw, seal_identity = studio68_original(output / "readback/seal-manifest.json", 32 * 1024 * 1024)
        seal = json.loads(seal_raw)
        require(seal.get("schema") == "motionwright.playwright-results-seal/1"
                and {k: seal.get(k) for k in ("archive", "archiveSha256", "treeSha256")} == read_report["archive"]
                and seal.get("sourceDirectory") == str(output / "results"), "Studio68 sealed original manifest differs")
        receipt["sealed_originals"] = {"archive": {"path": str(sealed), **sealed_identity}, "manifest": seal_identity}
        require(studio68_snapshot(product, STUDIO68_SOURCE, git, expected_tree=STUDIO68_TREE, allow_node_modules=True) == product_before
                and studio68_snapshot(gate, context["gate_source_sha"], git) == gate_before,
                "Studio68 final source readback differs")
        studio68_verify_dependencies(closure, sdk)
        studio68_verify_tools(tool_identities)
        sdk.verify_binaries({"studio_review_session": binary["producer_original"]}, root / "target")
        require(studio68_original(Path(binary["retained"]["path"]), 256 * 1024 * 1024)[1] ==
                {k: binary["retained"][k] for k in ("bytes", "sha256")}, "Studio68 retained executable changed after readback")
        require(studio68_npm_inventory(studio / "node_modules", sdk) == npm, "Studio68 package sources changed after readback")
        for row in commands:
            require(row.get("status") == "PASS_OBSERVED_COMMAND_ONLY", "Studio68 a command is not admitted")
            for stream in ("stdout", "stderr"):
                require(studio68_original(output / (row["label"] + "." + stream + ".log"), ARCHIVE_LIMITS["file_bytes"])[1] == row[stream],
                        "Studio68 final original stream readback differs")
        retention_phase.remaining(1)
        clock.remaining(1, reserve=0)
        receipt.update(status="PASS_STUDIO68_REAL_SERVICE_CLOSED_READBACK", service_cases=68,
                       scope="Five exact F278 service specs and closed Project/journal readback; no installed/native/human acceptance")
        exit_code = 0
    except UnknownObservation as error:
        receipt.update(status="UNKNOWN_NO_RETRY", error=str(error), uncertain_command=error.command)
        exit_code = 124
    except BaseException as error:
        receipt.update(status="FAIL_OR_PREPARATION_REJECTED", error=type(error).__name__ + ": " + str(error))
    receipt["finished_mono_ns"] = time.monotonic_ns()
    receipt["exit_code"] = exit_code
    write_new(output / "STUDIO68_RESULT.json", receipt)
    print(json.dumps({"status": receipt["status"], "gate": context["gate_source_sha"], "product": STUDIO68_SOURCE,
                      "receipt": str(output / "STUDIO68_RESULT.json"), "service_cases": receipt["service_cases"]}), flush=True)
    return exit_code

if __name__ == "__main__":
    if sys.argv[1:2] == ["studio68"]:
        raise SystemExit(studio68_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "claim-source-contracts":
        raise SystemExit(claim_source_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "workflow-route":
        raise SystemExit(workflow_route_main(sys.argv[2:]))
    sys.exit(archive_main(sys.argv[2:]) if sys.argv[1:2] == ["archive"]
             else unit_build_main(sys.argv[2:]) if sys.argv[1:2] == ["unit-build"] else main())
