/** An editorial renderer consumes the saved palette but cannot invent a
 * different token order or interpret arbitrary CSS as a safe color.
 * The existing SVG paint guard validates the actual #RRGGBB[AA] value. */
export function inheritedTextInk(palette: readonly { name: string; value: string }[]): string | null {
  const token = palette.find(item => /^(?:text|ink)$/i.test(item.name));
  return token?.value ?? null;
}
