import { useCallback, useRef } from "react";
let owner: HTMLAudioElement | null = null;
/** One audible local reviewer per Studio document. This is media UI only. */
export function claimLocalAudio(element: HTMLAudioElement | null): void {
  if (!element) return;
  if (owner && owner !== element) owner.pause();
  owner = element;
}
function release(element: HTMLAudioElement | null): void {
  if (!element) return;
  element.pause();
  if (owner === element) owner = null;
}
export function useLocalAudioElement() {
  const element = useRef<HTMLAudioElement | null>(null);
  const bind = useCallback((next: HTMLAudioElement | null) => {
    if (element.current !== next) release(element.current);
    element.current = next;
  }, []);
  return { element, bind, claim: () => claimLocalAudio(element.current), pause: () => release(element.current) };
}
