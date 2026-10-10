import { Keyboard, Search, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { filterPaletteCommands, type PaletteSearchItem } from "./editorCommandSearch";
import "./command-palette.css";

export interface EditorCommand extends PaletteSearchItem {
  icon: LucideIcon;
  shortcut?: string;
  run: () => void;
}

interface Props {
  commands: readonly EditorCommand[];
  onClose: () => void;
}

const GROUPS = ["Navigate", "Transport", "View"] as const;

const HOTKEYS: Array<[string, string]> = [
  ["Ctrl / ⌘ + K", "Open or close the command palette"],
  ["Ctrl / ⌘ + Shift + P", "Open or close the command palette"],
  ["↑ / ↓", "Move through available commands"],
  ["Enter", "Run the selected command"],
  ["Esc", "Close the command palette"],
  ["Space", "Play or pause the design preview"],
  ["← / →", "Move the editorial playhead one frame"],
  ["K", "Pause the design preview"],
  ["L", "Play the design preview"],
];

export default function CommandPalette({ commands, onClose }: Props) {
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [showGuide, setShowGuide] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const labelId = useId();
  const listId = useId();
  const matches = useMemo(() => filterPaletteCommands(commands, query), [commands, query]);
  // Keyboard selection must follow the same visual group order as the listbox.
  // Preserve relevance-ranked results *within* each group, not across groups.
  const filtered = useMemo(
    () => GROUPS.flatMap((group) => matches.filter((command) => command.group === group)),
    [matches],
  );
  const currentIndex = Math.min(selectedIndex, Math.max(filtered.length - 1, 0));
  const activeCommand = filtered[currentIndex];
  const optionId = (id: string) => listId + "-" + id;

  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement
      ? document.activeElement : null;
    inputRef.current?.focus();
    return () => previouslyFocused?.focus();
  }, []);

  useEffect(() => {
    if (!showGuide) inputRef.current?.focus();
  }, [showGuide]);

  function run(command: EditorCommand | undefined) {
    if (!command || command.disabled) return;
    onClose();
    command.run();
  }

  function moveSelection(direction: 1 | -1) {
    if (filtered.length === 0) return;
    let index = currentIndex;
    for (let offset = 0; offset < filtered.length; offset += 1) {
      index = (index + direction + filtered.length) % filtered.length;
      if (!filtered[index].disabled) {
        setSelectedIndex(index);
        return;
      }
    }
  }

  function onSearchKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      moveSelection(event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Enter" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      run(activeCommand);
    }
  }

  function onDialogKeyDown(event: ReactKeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onClose();
    }
    if (event.key !== "Tab" || !dialogRef.current) return;
    const candidates = [...dialogRef.current.querySelectorAll<HTMLElement>("input, button:not(:disabled)")];
    const first = candidates[0];
    const last = candidates[candidates.length - 1];
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <div
      className="command-palette-scrim"
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      <section
        className="command-palette-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelId}
        ref={dialogRef}
        onKeyDown={onDialogKeyDown}
      >
        <header className="command-palette-header">
          <div>
            <h2 id={labelId}>Command palette</h2>
          </div>
          <button className="command-palette-close" type="button" aria-label="Close command palette" onClick={onClose}>
            <X size={17} aria-hidden="true" />
          </button>
        </header>

        {showGuide ? (
          <div className="command-palette-guide" aria-label="Keyboard shortcut reference">
            <p>Shortcuts operate on the current editor view. They never approve a render or commit a creative revision.</p>
            <dl>
              {HOTKEYS.map(([hotkey, meaning]) => (
                <div key={hotkey}><dt><kbd>{hotkey}</kbd></dt><dd>{meaning}</dd></div>
              ))}
            </dl>
            <p className="command-palette-caption">Transport keys pause while focus is in a text field or this palette.</p>
          </div>
        ) : (
          <>
            <div className="command-palette-search">
              <Search size={18} aria-hidden="true" />
              <input
                ref={inputRef}
                type="search"
                role="combobox"
                aria-label="Search editor commands"
                aria-autocomplete="list"
                aria-controls={listId}
                aria-expanded="true"
                aria-activedescendant={activeCommand ? optionId(activeCommand.id) : undefined}
                value={query}
                onChange={(event) => { setQuery(event.target.value); setSelectedIndex(0); }}
                onKeyDown={onSearchKeyDown}
                placeholder="Search workspace or action…"
                autoComplete="off"
                spellCheck={false}
              />
              <span className="command-palette-count">{filtered.length} commands</span>
            </div>
            <div className="command-palette-results" id={listId} role="listbox" aria-label="Editor commands">
              {filtered.length === 0 ? (
                <div className="command-palette-empty" role="status">
                  <Search size={20} aria-hidden="true" />
                  <strong>No matching command</strong>
                  <span>Try “canvas”, “play”, “deliver”, or “review”.</span>
                </div>
              ) : GROUPS.map((group) => {
                const grouped = filtered.filter((command) => command.group === group);
                if (grouped.length === 0) return null;
                return (
                  <div key={group} role="group" aria-label={group} className="command-palette-group">
                    <div className="command-palette-group-label" aria-hidden="true">{group}</div>
                    {grouped.map((command) => {
                      const index = filtered.indexOf(command);
                      const Icon = command.icon;
                      return (
                        <button
                          key={command.id}
                          id={optionId(command.id)}
                          type="button"
                          role="option"
                          aria-selected={index === currentIndex}
                          disabled={command.disabled}
                          data-active={index === currentIndex ? "true" : undefined}
                          className="command-palette-option"
                          onMouseMove={() => { if (index !== currentIndex) setSelectedIndex(index); }}
                          onClick={() => run(command)}
                        >
                          <Icon size={16} aria-hidden="true" />
                          <span className="command-palette-option-copy">
                            <strong>{command.label}</strong>
                            <small>{command.description}</small>
                          </span>
                          {command.shortcut && <kbd>{command.shortcut}</kbd>}
                        </button>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          </>
        )}
        <footer className="command-palette-footer">
          <div className="command-palette-hint">
            <kbd>↑↓</kbd> select <kbd>↵</kbd> run <kbd>esc</kbd> close
          </div>
          <button type="button" className="command-palette-guide-toggle" aria-pressed={showGuide} onClick={() => setShowGuide((value) => !value)}>
            <Keyboard size={15} aria-hidden="true" />
            {showGuide ? "Back to commands" : "Keyboard shortcuts"}
          </button>
        </footer>
      </section>
    </div>
  );
}
