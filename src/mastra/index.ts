import { createDotagent } from './workflow.js';
import { configuration, nativeBridge } from './bridge.js';
import { homedir } from 'node:os';
import { join } from 'node:path';

// Build/Studio never launch the scheduler or native workers.
const config = process.env.DOTAGENT_RUNTIME_CONFIG ? configuration() : {
  state_dir: join(homedir(), '.local/state/dotagent/build'), studio: { port: 4111 },
};
export const mastra = createDotagent({ root: config.state_dir, bridge: nativeBridge(), port: config.studio?.port });
