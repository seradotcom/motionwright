import { useCallback, useRef } from "react";
let owner: HTMLMediaElement | null = null;
/** One audible local reviewer per Studio document. This is media UI only. */
export function claimLocalMedia(element: HTMLMediaElement | null): void {
  if (!element) return;
  if (owner && owner !== element) owner.pause();
  owner = element;
}
function release(element: HTMLMediaElement | null): void {
  if (!element) return;
  element.pause();
  if (owner === element) owner = null;
}
export function useLocalMediaElement<T extends HTMLMediaElement>() {
  const element = useRef<T | null>(null);
  const bind = useCallback((next: T | null) => {
    if (element.current !== next) release(element.current);
    element.current = next;
  }, []);
  return { element, bind, claim: () => claimLocalMedia(element.current), pause: () => release(element.current),
    ownsPlayback: () => element.current !== null && owner === element.current };
}

export function useLocalAudioElement() {
  return useLocalMediaElement<HTMLAudioElement>();
}
