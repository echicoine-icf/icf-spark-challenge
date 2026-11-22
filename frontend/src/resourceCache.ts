/** Browser-local, persisted machine-readable URI cache. No server writes. */
export type ResourceSnapshot = {
  uri: string;
  retrieved_at: string;
  content_type: string;
  source: string;
  content: unknown;
  sha256?: string;
  byte_count?: number;
};
const DB_NAME = 'icf-spark-explored-resources-v1';
const STORE = 'resources';
const MAX_ENTRIES = 150;
const fallback = new Map<string, ResourceSnapshot>();

function openDatabase(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === 'undefined') return Promise.resolve(null);
  return new Promise(resolve => {
    try {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => { if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE, { keyPath: 'uri' }); };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
      req.onblocked = () => resolve(null);
    } catch { resolve(null); }
  });
}

export async function cachedResource(uri: string): Promise<ResourceSnapshot | null> {
  const db = await openDatabase();
  if (!db) return fallback.get(uri) ?? null;
  return new Promise(resolve => {
    const tx = db.transaction(STORE, 'readonly');
    const req = tx.objectStore(STORE).get(uri);
    req.onsuccess = () => resolve(req.result as ResourceSnapshot || null);
    req.onerror = () => resolve(fallback.get(uri) ?? null);
    tx.oncomplete = () => db.close();
    tx.onerror = () => db.close();
  });
}

export async function storeResource(item: ResourceSnapshot): Promise<void> {
  fallback.set(item.uri, item);
  const db = await openDatabase();
  if (!db) return;
  await new Promise<void>(resolve => {
    try {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).put(item);
      tx.oncomplete = () => { db.close(); resolve(); };
      tx.onerror = () => { db.close(); resolve(); };
    } catch { db.close(); resolve(); }
  });
  // Avoid unbounded browser storage even after long exploratory sessions.
  const all = await listResourceCache();
  if (all.length <= MAX_ENTRIES) return;
  const excess = all.slice(MAX_ENTRIES);
  const db2 = await openDatabase();
  if (!db2) return;
  await new Promise<void>(resolve => {
    const tx = db2.transaction(STORE, 'readwrite');
    for (const entry of excess) tx.objectStore(STORE).delete(entry.uri);
    tx.oncomplete = () => { db2.close(); resolve(); };
    tx.onerror = () => { db2.close(); resolve(); };
  });
}

export async function listResourceCache(): Promise<ResourceSnapshot[]> {
  const db = await openDatabase();
  if (!db) return Array.from(fallback.values()).sort((a, b) => b.retrieved_at.localeCompare(a.retrieved_at));
  return new Promise(resolve => {
    const tx = db.transaction(STORE, 'readonly');
    const req = tx.objectStore(STORE).getAll();
    req.onsuccess = () => resolve((req.result as ResourceSnapshot[]).sort((a, b) => b.retrieved_at.localeCompare(a.retrieved_at)));
    req.onerror = () => resolve(Array.from(fallback.values()));
    tx.oncomplete = () => db.close();
    tx.onerror = () => db.close();
  });
}

export async function removeCachedResource(uri: string): Promise<void> {
  fallback.delete(uri);
  const db = await openDatabase();
  if (!db) return;
  await new Promise<void>(resolve => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).delete(uri);
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); resolve(); };
  });
}

export function sourceUrls(...texts: Array<string | undefined>): string[] {
  const found = new Set<string>();
  for (const text of texts) {
    for (const match of (text || '').matchAll(/https?:\/\/[^\s<>"'`]+/gi)) {
      const raw = match[0].replace(/[),.;!?\]]+$/, '');
      try {
        const parsed = new URL(raw);
        if (parsed.protocol === 'http:' || parsed.protocol === 'https:') found.add(parsed.href);
      } catch { /* not a valid URL */ }
      if (found.size >= 30) break;
    }
  }
  return [...found];
}
