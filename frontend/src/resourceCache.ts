/** Browser-local machine-readable URI cache and study-to-resource relationships. No server writes. */
export type ResourceSnapshot = {
  uri: string;
  retrieved_at: string;
  content_type: string;
  source: string;
  content: unknown;
  sha256?: string;
  byte_count?: number;
};
export type ResourceAssociation = {
  id: string;
  study_id: string;
  uri: string;
  associated_at: string;
};
const DB_NAME = 'icf-spark-explored-resources-v1';
const STORE = 'resources';
const LINKS = 'study_resource_links';
const MAX_ENTRIES = 150;
const fallback = new Map<string, ResourceSnapshot>();
const fallbackLinks = new Map<string, ResourceAssociation>();
const linkId = (studyId: string, uri: string) => JSON.stringify([studyId, uri]);

function openDatabase(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === 'undefined') return Promise.resolve(null);
  return new Promise(resolve => {
    try {
      // Version 2 adds relationships without modifying or discarding the version 1 resource store.
      const req = indexedDB.open(DB_NAME, 2);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: 'uri' });
        if (!db.objectStoreNames.contains(LINKS)) {
          const links = db.createObjectStore(LINKS, { keyPath: 'id' });
          links.createIndex('study_id', 'study_id', { unique: false });
          links.createIndex('uri', 'uri', { unique: false });
        }
      };
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

/** Idempotently associate a previously retrieved document with the study where it was inspected. */
export async function associateStudyResource(studyId: string, uri: string): Promise<void> {
  if (!studyId || studyId === 'library' || !uri) return;
  const id = linkId(studyId, uri);
  const item: ResourceAssociation = { id, study_id: studyId, uri, associated_at: new Date().toISOString() };
  if (!fallbackLinks.has(id)) fallbackLinks.set(id, item);
  const db = await openDatabase();
  if (!db) return;
  await new Promise<void>(resolve => {
    const tx = db.transaction(LINKS, 'readwrite');
    const store = tx.objectStore(LINKS);
    // Do not overwrite the original association timestamp when re-opening a document.
    const req = store.get(id);
    req.onsuccess = () => { if (!req.result) store.put(item); };
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); resolve(); };
    tx.onabort = () => { db.close(); resolve(); };
  });
}

export async function listResourceAssociations(): Promise<ResourceAssociation[]> {
  const db = await openDatabase();
  if (!db) return [...fallbackLinks.values()];
  return new Promise(resolve => {
    const tx = db.transaction(LINKS, 'readonly');
    const req = tx.objectStore(LINKS).getAll();
    req.onsuccess = () => resolve(req.result as ResourceAssociation[]);
    req.onerror = () => resolve([...fallbackLinks.values()]);
    tx.oncomplete = () => db.close();
    tx.onerror = () => db.close();
  });
}

export async function listStudyResourceCache(studyId: string): Promise<ResourceSnapshot[]> {
  const [cached, links] = await Promise.all([listResourceCache(), listResourceAssociations()]);
  const uris = new Set(links.filter(link => link.study_id === studyId).map(link => link.uri));
  return cached.filter(item => uris.has(item.uri));
}

export async function storeResource(item: ResourceSnapshot): Promise<void> {
  fallback.set(item.uri, item);
  const db = await openDatabase();
  if (!db) return;
  await new Promise<void>(resolve => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).put(item);
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); resolve(); };
  });
  const all = await listResourceCache();
  if (all.length <= MAX_ENTRIES) return;
  const excess = all.slice(MAX_ENTRIES);
  for (const entry of excess) await removeCachedResource(entry.uri);
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
  for (const [id, link] of fallbackLinks) if (link.uri === uri) fallbackLinks.delete(id);
  const db = await openDatabase();
  if (!db) return;
  await new Promise<void>(resolve => {
    const tx = db.transaction([STORE, LINKS], 'readwrite');
    tx.objectStore(STORE).delete(uri);
    const index = tx.objectStore(LINKS).index('uri');
    const req = index.openCursor(IDBKeyRange.only(uri));
    req.onsuccess = () => {
      const cursor = req.result;
      if (cursor) { cursor.delete(); cursor.continue(); }
    };
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); resolve(); };
    tx.onabort = () => { db.close(); resolve(); };
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
