export interface ExtensionMeta {
  name: string;
  color: string;
}

// Fallback color for the aggregated "Other" bucket.
export const OTHER_COLOR = '#9ca3af';

// Maps a normalized file extension (no leading dot, lower-case) to the display
// name and bar color used in the language breakdown. Colors follow GitHub
// Linguist. Unmapped extensions are aggregated into a single "Other" bucket.
export const EXTENSION_META: Record<string, ExtensionMeta> = {
  py: { name: 'Python', color: '#3572a5' },
  java: { name: 'Java', color: '#b07219' },
  ts: { name: 'TypeScript', color: '#3178c6' },
  tsx: { name: 'TypeScript', color: '#3178c6' },
  js: { name: 'JavaScript', color: '#f1e05a' },
  jsx: { name: 'JavaScript', color: '#f1e05a' },
  mjs: { name: 'JavaScript', color: '#f1e05a' },
  cjs: { name: 'JavaScript', color: '#f1e05a' },
  cbl: { name: 'COBOL', color: '#005ca5' },
  cob: { name: 'COBOL', color: '#005ca5' },
  cpy: { name: 'COBOL', color: '#005ca5' },
  html: { name: 'HTML', color: '#e34c26' },
  htm: { name: 'HTML', color: '#e34c26' },
  css: { name: 'CSS', color: '#563d7c' },
  scss: { name: 'SCSS', color: '#c6538c' },
  sass: { name: 'SCSS', color: '#c6538c' },
  json: { name: 'JSON', color: '#292929' },
  md: { name: 'Markdown', color: '#083fa1' },
  sh: { name: 'Shell', color: '#89e051' },
  bash: { name: 'Shell', color: '#89e051' },
  go: { name: 'Go', color: '#00add8' },
  rs: { name: 'Rust', color: '#dea584' },
  c: { name: 'C', color: '#555555' },
  h: { name: 'C', color: '#555555' },
  cpp: { name: 'C++', color: '#f34b7d' },
  cc: { name: 'C++', color: '#f34b7d' },
  hpp: { name: 'C++', color: '#f34b7d' },
  cs: { name: 'C#', color: '#178600' },
  rb: { name: 'Ruby', color: '#701516' },
  php: { name: 'PHP', color: '#4f5d95' },
  kt: { name: 'Kotlin', color: '#a97bff' },
  swift: { name: 'Swift', color: '#f05138' },
  scala: { name: 'Scala', color: '#c22d40' },
  sc: { name: 'Scala', color: '#c22d40' },
  yml: { name: 'YAML', color: '#cb171e' },
  yaml: { name: 'YAML', color: '#cb171e' },
  xml: { name: 'XML', color: '#0060ac' },
  sql: { name: 'SQL', color: '#e38c00' },
};
