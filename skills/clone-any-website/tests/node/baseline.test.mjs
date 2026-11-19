import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { captureKey, readExistingCapture } from '../../scripts/capture-states.mjs';
import { loadExistingTarget } from '../../scripts/run-verification.mjs';

test('both baseline reuse paths reject failed and mismatched metadata with a leftover image', async () => {
  const output = await mkdtemp(path.join(tmpdir(), 'baseline-regression-'));
  try {
    const viewport = { name: 'desktop', width: 800, height: 600 };
    const state = { name: 'initial', viewport: 'desktop', actions: [] };
    const config = { target: { url: 'https://example.test/' }, environment: {}, viewports: [viewport], states: [state], paths: { output } };
    const image = path.join(output, 'target/desktop/initial.png');
    const metadata = path.join(output, 'target/metadata/desktop/initial.json');
    await mkdir(path.dirname(image), { recursive: true });
    await mkdir(path.dirname(metadata), { recursive: true });
    await writeFile(image, 'old image bytes');
    const valid = { side: 'target', state: 'initial', viewport_name: 'desktop', capture_key: captureKey(config, state, viewport) };
    for (const details of [{ ...valid, error: 'navigation failed' }, { ...valid, capture_key: 'old configuration' }, { ...valid, state: 'wrong state' }]) {
      await writeFile(metadata, JSON.stringify(details));
      assert.equal(await readExistingCapture(image, metadata, state, viewport, config), null);
      assert.equal((await loadExistingTarget(config)).states[0].status, 'failed');
    }
    await writeFile(metadata, JSON.stringify(valid));
    assert.equal((await readExistingCapture(image, metadata, state, viewport, config)).reused, true);
    assert.equal((await loadExistingTarget(config)).states[0].status, 'captured');
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});
