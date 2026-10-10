# Motionwright command palette

The editor opens its workspace/action palette from the Commands button or Ctrl/Cmd+K (also Ctrl/Cmd+Shift+P).

It provides navigation across 15 workspaces, one-frame stepping, editorial playback, theme choice and rail visibility. Search is deterministic, multi-word and accent-insensitive. Arrow keys change selection, Enter activates a command, and Escape closes the dialog.

These commands affect the editor view only. They do not grant rendering permissions, alter project revisions, export media or approve content. Those operations retain their existing Semwright Native SDK and Studio authorization flows.

When a text input is focused, transport shortcuts are suspended. Opening from a focused input and closing the dialog returns focus to that input. The palette is accessible in both light and dark editor themes.

## Visual references

The layout adapts compact, familiar production controls from DaVinci Resolve Edit (https://www.blackmagicdesign.com/mx/products/davinciresolve/edit) and Adobe Premiere workspaces (https://helpx.adobe.com/premiere/desktop/get-started/tour-the-workspace/manage-workspaces.html). Frame.io review comparisons (https://help.frame.io/en/articles/9952618-comparison-viewer) informed separation between navigation and content approval. No third-party assets are copied.

The project-local Impeccable skill, PRODUCT.md, and DESIGN.md provide the lasting design constraints.

## Checks

Unit tests cover filtering, ranking, disabled entries and modifier/IME handling. Playwright Chromium covers workspace navigation, one-frame stepping, empty results, shortcut guidance, focused-field restoration, theme changes and revision invariance. Existing CI validates the Studio build.

Passing these tests does not change the 60 separate independent product acceptance cases, all of which still require actual independent evaluation.
