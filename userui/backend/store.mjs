import { Worker } from 'node:worker_threads';

/** SQLite ve parola hesaplarını HTTP event loop'undan ayrı, sınırlı bir kuyruğa taşır. */
export class UserStore {
  constructor(path) {
    this.pending = new Map();
    this.sequence = 0;
    this.worker = new Worker(new URL('./store-worker.mjs', import.meta.url), {
      workerData: { path },
    });
    this.worker.on('message', ({ id, result, error }) => {
      const pending = this.pending.get(id);
      if (!pending) return;
      this.pending.delete(id);
      clearTimeout(pending.timer);
      if (error) pending.reject(Object.assign(new Error(error.message), { status: error.status }));
      else pending.resolve(result);
    });
    this.worker.on('error', (error) => this.fail(error));
    this.worker.on('exit', () => this.fail(new Error('Kullanıcı deposu kapandı.')));
  }
  fail(error) {
    this.closed = true;
    for (const p of this.pending.values()) {
      clearTimeout(p.timer);
      p.reject(error);
    }
    this.pending.clear();
  }
  call(operation, args = {}) {
    if (this.closed || this.pending.size >= 128)
      return Promise.reject(
        Object.assign(new Error('İstek yoğunluğu nedeniyle tekrar deneyin.'), { status: 503 }),
      );
    return new Promise((resolve, reject) => {
      const id = ++this.sequence;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(Object.assign(new Error('Kayıt işlemi zamanında tamamlanamadı.'), { status: 503 }));
      }, 15000);
      this.pending.set(id, { resolve, reject, timer });
      this.worker.postMessage({ id, operation, args });
    });
  }
  async close() {
    if (this.closed) return;
    try {
      await this.call('close');
    } finally {
      await this.worker.terminate();
    }
  }
}
