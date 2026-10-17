import React from 'react';
import {useCurrentFrame} from 'remotion';

// One deterministic source-only fixture. The script never reads clocks,
// random values or external assets and never claims native visual equivalence.
export const Baseline = () => {
  const frame = useCurrentFrame();
  const progress = frame / 59;
  const x = 256 + Math.round(progress * 1024);
  return (
    <div style={{
      height: '100%', width: '100%', backgroundColor: '#15171b',
      position: 'relative', overflow: 'hidden',
    }}>
      <div style={{
        position: 'absolute', left: x, top: 324, width: 192,
        height: 144, borderRadius: 0, background: '#e4e5e9',
      }} />
      <div style={{
        position: 'absolute', left: 256, top: 648, width: 1184,
        height: 16, background: '#5189d3',
      }} />
    </div>
  );
};
