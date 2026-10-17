import React from 'react';
import {Composition} from 'remotion';
import {Baseline} from './baseline.jsx';

export const Root = () => (
  <Composition
    id="StaticTwoLayerBaseline"
    component={Baseline}
    width={1920}
    height={1080}
    fps={30}
    durationInFrames={60}
  />
);
