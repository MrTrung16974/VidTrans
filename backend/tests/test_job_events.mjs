import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
const test = (name, run) => { run(); console.log(`PASS ${name}`); };
const source = readFileSync(new URL('../../web/js/job-events.js', import.meta.url), 'utf8');
const { applyJobChanges, jobEventsUrl } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const jobs = [
  { job_id: 'a', status: 'processing', progress: 0.1 },
  { job_id: 'b', status: 'queued', queue_position: 2 },
  { job_id: 'c', status: 'completed' },
];
test('uses wss behind HTTPS and ws locally', () => {
  assert.equal(jobEventsUrl({ protocol: 'https:', host: '169.58.116.223' }), 'wss://169.58.116.223/api/v1/jobs/events');
  assert.equal(jobEventsUrl({ protocol: 'http:', host: 'localhost:5200' }), 'ws://localhost:5200/api/v1/jobs/events');
});
test('progress of a visible job is patched without reloading the list', () => {
  const result = applyJobChanges(jobs, { items: [{ job_id: 'a', status: 'processing', progress: 0.5 }], removed: [] });
  assert.equal(result.reload, false);
  assert.equal(result.jobs[0].progress, 0.5);
  assert.equal(jobs[0].progress, 0.1, 'input list is not mutated');
});
test('queue positions shift and drop for claimed jobs', () => {
  const result = applyJobChanges(jobs, { items: [], removed: [], queue_positions: {} });
  assert.equal(result.jobs[1].queue_position, undefined);
  const moved = applyJobChanges(jobs, { items: [], removed: [], queue_positions: { b: 1 } });
  assert.equal(moved.jobs[1].queue_position, 1);
});
test('deleted jobs disappear; new jobs, filter changes and resync reload', () => {
  assert.deepEqual(applyJobChanges(jobs, { items: [], removed: ['c'] }).jobs.map(job => job.job_id), ['a', 'b']);
  assert.equal(applyJobChanges(jobs, { items: [{ job_id: 'z', status: 'queued' }], removed: [] }).reload, true);
  const filtered = applyJobChanges(jobs, { items: [{ job_id: 'a', status: 'completed' }] }, { statusFilter: 'processing' });
  assert.equal(filtered.reload, true);
  assert.equal(applyJobChanges(jobs, { resync: true }).reload, true);
});
