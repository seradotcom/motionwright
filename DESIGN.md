# Motionwright Design System

<!-- impeccable:design-schema 1 -->

```yaml
tokens:
  color:
    bg: "#090B0E"
    surface-1: "#0F1216"
    surface-2: "#151A20"
    surface-3: "#1C222A"
    border: "#2B333D"
    text: "#F2F4F3"
    text-muted: "#9EA8B3"
    text-dim: "#6F7984"
    accent: "#F5B84A"
    accent-strong: "#FFD072"
    info: "#62A9FF"
    success: "#5FCB8D"
    warning: "#F5B84A"
    danger: "#FF6B72"
    unknown: "#A78BFA"
  radius:
    control: 8
    panel: 10
    popover: 12
  space:
    1: 4
    2: 8
    3: 12
    4: 16
    5: 20
    6: 24
    8: 32
  type:
    ui: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    data: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace"
```

## Direction

**Cut Room Ledger** is a serious creative workstation informed by film contact sheets, editorial edge codes, review annotations, and clean production paperwork. The reference supplies four things only: a restrained dark palette with warm review ink, compact professional typography, information density, and the splice-line signature move. Navigation, controls, tables, panels, timeline gestures, menus, tabs, and forms stay familiar product UI.

The interface should disappear behind the task. It is not a themed movie prop, a cyberpunk cockpit, a terminal, a glass dashboard, or a marketing page.

## Palette and Surfaces

Dark mode is the default because long editing sessions and visual preview benefit from a low-luminance shell. The program preview itself is neutral and never tinted by the application chrome. Light mode keeps the same hierarchy using warm off-white work surfaces rather than simply inverting every token.

Accent amber is reserved for current time, selected edit intent, and primary commit actions. Blue communicates informational/runtime activity, green confirms known success, red is failure/destructive state, violet marks explicit UNKNOWN/indeterminate state. Every semantic color has text/icon/pattern support.

Use one depth cue at a time: either a border or a soft offset shadow. Panels mostly rely on tonal separation and hairline boundaries; avoid stacks of rounded cards.

## Typography

Use the platform UI sans for the whole editor. Weight and spacing create hierarchy; no display face is used in controls or workspace labels.

- 12px: metadata, track labels, timecode helpers.
- 13px: dense controls, inspector values, tree rows.
- 14px: primary UI copy and navigation.
- 16px: panel headings and important selected values.
- 20–24px: project/opening-screen headings only.

Use the monospace stack only for timecode, frame/sample positions, SHAs, IDs, logs, measurements, and code. Tabular numerals are enabled for counters and timeline rulers.

## Layout

The primary editor is a four-region workstation:

1. **Top command bar** — project identity, workspace switcher, undo/redo, scoped agent action, preview/render controls, state and collaboration indicators.
2. **Left project rail** — assets, narrative, storyboard index, branches and deliverables. It is resizable and collapsible.
3. **Center stage** — program preview/canvas with mode switch between design representation and native preview. Secondary comparisons can split this region A/B/C without creating a separate product.
4. **Right inspector** — contextual properties, constraints, locks, renderer support and review state.
5. **Bottom timeline** — a horizontally scalable multi-track surface sharing the same playhead with transcript, storyboard and canvas.

The editor may hide rails for focus, but opening/closing them never changes project state.

## Signature Move: Splice Line

The active playhead is represented by a 1px amber splice line. It continues visually through the timeline and, where spatially meaningful, aligns an edge marker in the preview header, transcript/cue ruler, and storyboard strip. On seek or a committed timing edit it brightens and widens briefly, then settles. It does not pulse continuously.

Reduced-motion keeps the line static and disables the transient widening. The splice line is never the only indicator of current time; exact timecode remains visible.

## Components and States

Controls use standard buttons, segmented controls, tabs, selects, inputs, checkboxes, context menus, command palette and dialogs only when protected focus is actually required.

Every interactive component provides default, hover, focus-visible, active, disabled and loading states. Mutation controls distinguish **previewing**, **dirty**, **committing**, **stale/conflict**, and **committed** states.

Badges are compact and textual: `CURRENT`, `STALE`, `UNKNOWN`, `REVIEW`, `LOCKED`, `OFFLINE`. Color reinforces rather than replaces the label.

Empty states teach the next meaningful action. Failed renders keep the previous preview visible with its revision badge.

## Timeline

Tracks are not cards. They are compact lanes with a common ruler and real temporal geometry. Clip labels truncate intelligently; selection shows handles only when the resource supports the action. Voice/transcript/cue lanes use wave or text geometry only when data exists; placeholders never imply measured audio.

Snap targets, locks, constraints, unavailable renderer actions and stale ranges are visible without covering the media. Zoom preserves a stable playhead and pointer anchor.

## Canvas and Storyboard

Canvas selection uses crisp bounds and handles, not glow. Safe areas and camera bounds are distinct line styles with text labels. Relationship connectors keep stable endpoints.

Storyboard cards are production artifacts, not generic dashboard cards: thumbnail/frame, scene/shot label, objective, duration/time range, renderer and state. Reordering animates spatially and preserves card identity.

## Motion

Most state transitions use 160–220ms ease-out. Motion communicates selection, panel resizing, timeline navigation, drag commitment, comparison change, render progress and conflict recovery. No page-load choreography, floating decorations, background particles or endless bounce.

## Accessibility and Adaptation

Visible focus is mandatory. Keyboard users can traverse workspaces, trees, timeline items, inspector controls and transport. Pointer targets meet a 44px touch minimum where the desktop density permits touch use; dense timeline handles provide larger hit areas than their visual geometry.

The desktop shell is optimized for 1280px and above. At narrower widths the inspector collapses before the project rail; the timeline remains full width; primary editing never becomes a pile of vertically stacked cards. Reduced-motion and high-contrast preferences are respected.

## Anti-References

Do not use:
- generic SaaS dashboard cards or KPI tiles;
- purple/blue AI gradients, gradient text, glow borders or glassmorphism;
- icon tiles above headings;
- decorative monospace everywhere;
- fake waveform/audio measurements;
- controls styled as camera hardware, terminals, dials or film equipment;
- color as the only state signal;
- tooltips as the only place an operation's consequence is explained.
