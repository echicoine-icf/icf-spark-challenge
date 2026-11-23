import { useMemo, useState } from 'react';
import { motion } from 'motion/react';

export type RecordTab = 'overview' | 'json' | 'xml' | 'raw';

type OriginalRecord = { original_description?: string };

function xmlSafeName(value: string): string {
  const safe = value.replace(/[^A-Za-z0-9_.-]/g, '_');
  return /^[A-Za-z_]/.test(safe) ? safe : `field_${safe}`;
}
function escapeXml(value: string): string {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&apos;');
}
function xmlNode(key: string, value: unknown, level = 0): string {
  const tag = xmlSafeName(key);
  const space = '  '.repeat(level);
  if (Array.isArray(value)) return value.map(v => xmlNode(tag, v, level)).join('');
  if (value === null || value === undefined) return `${space}<${tag} />\n`;
  if (typeof value === 'object') {
    const inner = Object.entries(value).map(([k, v]) => xmlNode(k, v, level + 1)).join('');
    return `${space}<${tag}>\n${inner}${space}</${tag}>\n`;
  }
  return `${space}<${tag}>${escapeXml(String(value))}</${tag}>\n`;
}

export default function RecordRepresentation({ tab, record, recordType }: { tab: Exclude<RecordTab, 'overview'>; record: OriginalRecord; recordType: 'study' | 'dataset' }) {
  const [copied, setCopied] = useState(false);
  const content = useMemo(() => tab === 'json' ? JSON.stringify(record, null, 2)
    : tab === 'xml' ? `<?xml version="1.0" encoding="UTF-8"?>\n${xmlNode(recordType === 'study' ? 'study' : 'dataset', record)}`
    : record.original_description || '', [tab, record, recordType]);
  const label = tab === 'json' ? 'JSON representation' : tab === 'xml' ? 'XML representation (generated)' : 'Original description text';
  return <section className="record-representation" aria-label={label}>
    <div className="record-format-top"><div><strong>{label}</strong><p>{tab === 'xml' ? 'Converted locally from the SPARK API JSON record; not original dbGaP XML.' : tab === 'json' ? 'Record fields as returned by the SPARK API. Not the original source file.' : 'Unmodified description text returned by SPARK. The original dbGaP source file is not exposed by this endpoint.'}</p></div>
      <button type="button" className="outline-button" onClick={async () => { try { await navigator.clipboard.writeText(content); setCopied(true); window.setTimeout(() => setCopied(false), 1400); } catch { setCopied(false); } }}>{copied ? 'Copied' : 'Copy'}</button></div>
    <motion.pre className="record-format-body" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.4 }}>{content || 'No original description was supplied for this record.'}</motion.pre>
  </section>;
}
