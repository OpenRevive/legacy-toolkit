// These workers are lightweight supervisor clients, not device executors.
// Metadata/cancellation and frames must remain usable while control is pending.
export class BackendPool {
  constructor(create, defaultHost = 'blacklion') {
    this.create = create;
    this.defaultHost = defaultHost;
    this.workers = new Map();
    this.handles = new Map();
    this.barriers = new Set();
  }
  forget(worker) {
    for (const [handle, item] of this.handles) if (item.worker === worker) this.handles.delete(handle);
  }
  remember(value, worker) {
    if (!value || typeof value !== 'object') return;
    if (typeof value.snapshotId === 'string') this.handles.set(value.snapshotId, {worker, at: Date.now()});
    for (const item of Object.values(value)) if (item && typeof item === 'object') this.remember(item, worker);
    for (const [handle, item] of this.handles) if (Date.now() - item.at > 30000) this.handles.delete(handle);
    while (this.handles.size > 256) this.handles.delete(this.handles.keys().next().value);
  }
  async call(method, args, signal) {
    let host = args.host || this.defaultHost;
    let worker;
    if (args.snapshotId) {
      worker = this.handles.get(args.snapshotId)?.worker;
      if (!worker) throw new Error('Unknown, expired or superseded snapshot. Read a fresh tree in this MCP session.');
      host = worker.host;
    }
    if (this.barriers.has(host)) throw new Error('Host workflow in progress; refresh device state after it finishes.');
    if (method === 'run_workflow' || (method === 'device_session' && args.operation === 'close' && ['all','transport'].includes(args.component || 'all'))) {
      const selected = [...this.workers.values()].filter(w => w.host === host &&
        (method === 'run_workflow' || args.component === 'transport' || w.udid === args.udid));
      if (selected.some(w => w.pending || w.stopping)) throw new Error('Device operation in progress; no connection was closed.');
      this.barriers.add(host);
      try {
        for (const item of selected) { await item.stop(); this.forget(item); this.workers.delete(item.key); }
      } finally { this.barriers.delete(host); }
    }
    if (!worker) {
      const telemetry = ['read_logs','list_crashes','read_crash','capture_network'].includes(method) ||
        (method === 'device_session' && ['logs','crashes'].includes(args.component));
      const metadata = ['device_operation','device_artifact'].includes(method) ||
        (method==='device_session' && args.operation!=='close') ||
        (['observe_ui','ui_observe'].includes(method) && args.cached);
      const key = JSON.stringify([host, args.udid || 'mac', metadata ? 'metadata' : method==='get_screenshot' ? 'frames' : telemetry ? 'telemetry' : 'control']);
      worker = this.workers.get(key);
      if (!worker) {
        if (this.workers.size >= 12) throw new Error('Too many active device workers; close an idle device session first.');
        worker = this.create();
        Object.assign(worker, {host, udid: args.udid, key});
        this.workers.set(key, worker);
      }
    }
    // A CLI workflow is a host-wide barrier for its whole lifetime, not just
    // while old relays are being stopped. Input is never queued behind it.
    if (method === 'run_workflow') this.barriers.add(host);
    try {
      const value = await worker.call(method, args, signal);
      if (value.session) worker.sessionReport = value.session;
      if (!value.error) this.remember(value.result, worker);
      if (method === 'device_session' && value.result) value.result.workers = [...this.workers.values()]
        .filter(w => w.host === host && w.udid === args.udid)
        .map(w => ({kind: JSON.parse(w.key)[2], busy: !!w.pending, running: !!w.child,
          ...(w.child && w.sessionReport ? {session:w.sessionReport} : {})}));
      return value;
    } catch (error) {
      if (!worker.child || worker.stopping || signal.aborted) this.forget(worker);
      throw error;
    } finally {
      if (method === 'run_workflow') this.barriers.delete(host);
    }
  }
  async stop() {
    await Promise.all([...this.workers.values()].map(w => w.stop()));
    this.workers.clear(); this.handles.clear(); this.barriers.clear();
  }
}
